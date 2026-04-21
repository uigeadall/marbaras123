from __future__ import annotations

import logging
import time
import uuid
import hashlib
from urllib.parse import urlencode
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace
from typing import Iterable, Optional
from django.db.models import Max
import stripe
from stripe import _error as stripe_error
from allauth.account.views import LoginView
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import (
    Avg,
    Case,
    Count,
    F,
    IntegerField,
    Prefetch,
    Q,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie, csrf_protect
from django.middleware.csrf import get_token
from django.views.decorators.http import require_http_methods, require_POST

from .forms import ProductReviewForm
from .models import (
    BannerImage,
    BlogPost,
    CustomerReview,
    CartItem,
    Category,
    Coupon,
    ProductReview,
    Favorite,
    Order,
    OrderItem,
    Product,
    ProductImage,
    ProductVariant,
    ShippingOption, ProductBundleItem,
    UserProfile,
)
from .signals import order_submitted, user_registered
from .utils.emailing import (
    send_welcome_email,
    send_order_confirmation_email,
)

logger = logging.getLogger(__name__)


if settings.STRIPE_SECRET_KEY:
    try:

        cleaned_key = settings.STRIPE_SECRET_KEY.strip().replace('\n', '').replace('\r', '')

        cleaned_key.encode('latin-1')
        stripe.api_key = cleaned_key
        logger.debug("Stripe API key configured successfully")
    except UnicodeEncodeError:
        logger.error("Stripe secret key contains non-ASCII characters - Stripe API calls will fail")
        stripe.api_key = None
else:
    stripe.api_key = None


UNLIMITED_STOCK = 10**9

# Contact form anti-bot (see contact view)
CONTACT_BOT_RAW_POST_LIMIT = 25
CONTACT_BOT_SENT_PER_HOUR = 5
CONTACT_BOT_WINDOW_SEC = 3600
CONTACT_BOT_MIN_SUBMIT_MS = 3000
CONTACT_MESSAGE_MIN_LEN = 10
CONTACT_MESSAGE_MAX_LEN = 1200
CONTACT_NAME_MAX_LEN = 200
CONTACT_SUBJECT_MAX_LEN = 200

# Checkout / PaymentIntent abuse (bots opening checkout or hitting Apple Pay API)
CHECKOUT_PI_RATE_WINDOW_SEC = 3600


def _client_ip(request: HttpRequest) -> str:
    """Client IP; prefers X-Forwarded-For when behind a reverse proxy (e.g. Railway)."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip() or "unknown"
    return (request.META.get("REMOTE_ADDR") or "").strip() or "unknown"


def _checkout_payment_intent_rate_allow(request: HttpRequest) -> bool:
    """Limit PaymentIntent-related Stripe calls per IP / user (sliding window via cache TTL)."""
    if not getattr(settings, "CHECKOUT_PI_RATE_LIMIT_ENABLED", True):
        return True
    if request.user.is_authenticated:
        limit = int(getattr(settings, "CHECKOUT_PI_RATE_PER_HOUR_USER", 120))
        bucket = f"u{request.user.pk}"
    else:
        limit = int(getattr(settings, "CHECKOUT_PI_RATE_PER_HOUR_IP", 45))
        raw_ip = _client_ip(request)
        bucket = "i" + raw_ip.replace(":", "_")[:120]
    key = f"c_stripe_pi:{bucket}"
    window = int(getattr(settings, "CHECKOUT_PI_RATE_WINDOW_SEC", CHECKOUT_PI_RATE_WINDOW_SEC))
    try:
        n = cache.incr(key)
    except ValueError:
        cache.set(key, 1, window)
        return True
    if n > limit:
        logger.warning(
            "checkout Stripe PI rate limit exceeded key=%s n=%s limit=%s",
            key,
            n,
            limit,
        )
        return False
    return True


def _get_currency_from_request(request: HttpRequest) -> str:
    """ISO 4217 code for Order.currency (POST/GET/session); defaults to EUR."""
    for key in ("currency", "order_currency"):
        raw = (request.POST.get(key) or request.GET.get(key) or "").strip().upper()
        if len(raw) == 3 and raw.isalpha():
            return raw
    sess = (request.session.get("order_currency") or "").strip().upper()
    if len(sess) == 3 and sess.isalpha():
        return sess
    default = getattr(settings, "DEFAULT_ORDER_CURRENCY", None) or getattr(
        settings, "META_PIXEL_CURRENCY", None
    )
    if default and len(str(default).strip()) == 3:
        return str(default).strip().upper()[:3]
    return "EUR"


def _ensure_session(request: HttpRequest) -> None:

    if not request.session.session_key:
        request.session.create()


def _owner_filter(request: HttpRequest) -> dict:

    if request.user.is_authenticated:
        return {"user": request.user}
    _ensure_session(request)
    return {"session_key": request.session.session_key}


def _cart_total_quantity(request: HttpRequest) -> int:
    """Total item quantity in cart (same logic as cart_count context processor)."""
    try:
        if request.user.is_authenticated:
            total = (
                CartItem.objects.filter(user=request.user)
                .aggregate(c=Sum("quantity"))
                .get("c")
            )
            return int(total or 0)
        _ensure_session(request)
        sk = request.session.session_key
        if not sk:
            return 0
        total = (
            CartItem.objects.filter(session_key=sk)
            .aggregate(c=Sum("quantity"))
            .get("c")
        )
        return int(total or 0)
    except Exception:
        return 0


def _cart_items_for(request: HttpRequest) -> Iterable[CartItem]:
    if request.user.is_authenticated:
        return (
            CartItem.objects
            .filter(user=request.user)
            .select_related("product", "variant")
            .prefetch_related(
                Prefetch("product__images", queryset=ProductImage.objects.all()),
                "product__categories"
            )
        )
    _ensure_session(request)
    return (
        CartItem.objects
        .filter(session_key=request.session.session_key)
        .select_related("product", "variant")
        .prefetch_related(
            Prefetch("product__images", queryset=ProductImage.objects.all()),
            "product__categories"
        )
    )


def _compute_subtotal(items: Iterable[CartItem]) -> Decimal:

    total = Decimal("0")
    for it in items:
        price = it.product.get_discounted_price() or Decimal("0")
        it.subtotal = price * it.quantity
        total += it.subtotal
    return total


def _to_cents(amount: Decimal) -> int:

    return int((amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100))


def _available_stock(product: Product, variant_id: Optional[int] = None) -> int:

    if variant_id:
        return int(
            ProductVariant.objects.filter(id=variant_id, product=product)
            .values_list("stock", flat=True)
            .first()
            or 0
        )

    try:
        if hasattr(product, "variants") and product.variants.exists():
            agg = product.variants.aggregate(total=Sum("stock"))
            return int(agg["total"] or 0)
    except Exception:
        pass

    for field in ("stock", "quantity", "inventory"):
        if hasattr(product, field):
            val = getattr(product, field, None)
            if val is not None:
                return int(val or 0)

    return UNLIMITED_STOCK


def _cap_quantity(requested_total: int, available: int) -> int:
    try:
        requested_total = int(requested_total)
    except Exception:
        requested_total = 0
    try:
        available = int(available)
    except Exception:
        available = 0

    if available < 0:
        available = 0
    if requested_total < 0:
        requested_total = 0
    return min(requested_total, available)


class InsufficientStockError(Exception):
    """Raised inside a transaction.atomic() block when a cart item's stock
    cannot be satisfied at the moment of checkout. The caller is expected
    to let the transaction roll back and surface a user-friendly message.
    """

    def __init__(self, product_name: str, requested: int, available: int):
        self.product_name = product_name
        self.requested = int(requested)
        self.available = int(available)
        super().__init__(
            f"Insufficient stock for '{product_name}': requested {self.requested}, available {self.available}."
        )


def _consume_cart_stock(cart_items: Iterable[CartItem]) -> None:
    """Atomically lock + decrement stock for every item in the cart.

    Must be called **inside** a ``transaction.atomic()`` block. Uses
    ``SELECT ... FOR UPDATE`` on each Product/ProductVariant row so that
    two concurrent checkouts competing for the last unit are serialised
    by the database — preventing oversells.

    Rules:
      * If ``CartItem.variant_id`` is set → decrement
        :class:`ProductVariant.stock`.
      * Otherwise → decrement :class:`Product.stock` directly.

    Raises :class:`InsufficientStockError` if any requested quantity
    exceeds what is on hand. The outer atomic block will then roll back
    all changes (including the Order creation) when this exception
    propagates.
    """
    from collections import defaultdict

    demand: dict[tuple[int, Optional[int]], int] = defaultdict(int)
    labels: dict[tuple[int, Optional[int]], str] = {}

    for item in cart_items:
        if item is None or not getattr(item, 'product_id', None):
            continue
        qty = int(getattr(item, 'quantity', 0) or 0)
        if qty <= 0:
            continue
        variant_id = getattr(item, 'variant_id', None)
        key = (int(item.product_id), int(variant_id) if variant_id else None)
        demand[key] += qty
        if key not in labels:
            if variant_id and getattr(item, 'variant', None):
                labels[key] = f"{item.product.name} ({item.variant.size or item.variant.variant_type})"
            else:
                labels[key] = getattr(item.product, 'name', f"Product #{item.product_id}")

    for (product_id, variant_id), qty in demand.items():
        label = labels.get((product_id, variant_id), f"Product #{product_id}")
        if variant_id:
            variant = (
                ProductVariant.objects.select_for_update()
                .get(pk=variant_id)
            )
            available = int(variant.stock or 0)
            if available < qty:
                raise InsufficientStockError(label, qty, available)
            variant.stock = available - qty
            variant.save(update_fields=['stock'])
            logger.info(
                f"Stock: variant#{variant_id} ({label}) -{qty} → {variant.stock}"
            )
        else:
            product = (
                Product.objects.select_for_update()
                .get(pk=product_id)
            )
            available = int(product.stock or 0)
            if available < qty:
                raise InsufficientStockError(label, qty, available)
            product.stock = available - qty
            product.save(update_fields=['stock'])
            logger.info(
                f"Stock: product#{product_id} ({label}) -{qty} → {product.stock}"
            )


def _process_coupon(coupon_code: str, subtotal: Decimal, apply_usage: bool = True, cart_items: Optional[Iterable[CartItem]] = None) -> tuple[Decimal, Decimal, Optional[str], Optional[str]]:
    """Process coupon code and return (new_subtotal, discount, coupon_applied, coupon_error).
    
    Args:
        coupon_code: The coupon code to process
        subtotal: The subtotal amount (total of all items)
        apply_usage: If True, increment used_count (for actual order). If False, just validate (for preview).
        cart_items: Optional cart items to check for Sale category products.
    
    Returns:
        Tuple of (new_subtotal, discount, coupon_applied, coupon_error)
        - new_subtotal: Subtotal after applying coupon (only to non-Sale items)
        - discount: Discount amount applied
        - coupon_applied: Coupon code if applied, None otherwise
        - coupon_error: Error message if coupon cannot be applied
    """
    discount = Decimal("0.00")
    coupon_applied = None
    coupon_error = None
    
    if coupon_code:
        # Calculate subtotal only for non-Sale products
        non_sale_subtotal = subtotal
        sale_subtotal = Decimal("0.00")
        
        if cart_items:
            from ecommerce.models import Category, CartItem, Product
            import logging
            logger = logging.getLogger(__name__)
            
            sale_category = Category.objects.filter(
                Q(name__iexact='Sale') | Q(name__icontains='разпродажба')
            ).first()
            
            if sale_category:
                logger.info(f"Checking for Sale category products. Sale category ID: {sale_category.id}, Name: {sale_category.name}")
                
                # Get product IDs that are in Sale category
                sale_product_ids = set()
                if hasattr(cart_items, 'model') and cart_items.model == CartItem:
                    # It's a queryset - get product IDs directly
                    sale_product_ids = set(
                        cart_items.filter(product__categories=sale_category)
                        .values_list('product_id', flat=True)
                    )
                else:
                    # It's a list/iterable - check each item
                    cart_items_list = list(cart_items) if not isinstance(cart_items, list) else cart_items
                    for item in cart_items_list:
                        product_id = None
                        if hasattr(item, 'product_id'):
                            product_id = item.product_id
                        elif hasattr(item, 'product') and hasattr(item.product, 'id'):
                            product_id = item.product.id
                        
                        if product_id:
                            # Check if this product is in Sale category
                            if Product.objects.filter(id=product_id, categories=sale_category).exists():
                                sale_product_ids.add(product_id)
                
                logger.info(f"Sale product IDs: {sale_product_ids}")
                
                # Calculate subtotals separately
                if sale_product_ids:
                    # Calculate subtotal for Sale products
                    if hasattr(cart_items, 'model') and cart_items.model == CartItem:
                        sale_items = cart_items.filter(product_id__in=sale_product_ids)
                    else:
                        cart_items_list = list(cart_items) if not isinstance(cart_items, list) else cart_items
                        sale_items = [item for item in cart_items_list 
                                    if (hasattr(item, 'product_id') and item.product_id in sale_product_ids) or
                                       (hasattr(item, 'product') and hasattr(item.product, 'id') and item.product.id in sale_product_ids)]
                    
                    sale_subtotal = _compute_subtotal(sale_items)
                    non_sale_subtotal = subtotal - sale_subtotal
                    
                    logger.info(f"Sale subtotal: {sale_subtotal}, Non-Sale subtotal: {non_sale_subtotal}")
        
        # Apply coupon only to non-Sale subtotal
        coupon = Coupon.objects.filter(code=coupon_code).first()
        if not coupon:
            coupon_error = f"Coupon code '{coupon_code}' not found."
        elif not coupon.is_valid_now():
            # Provide specific error message based on why coupon is invalid
            from django.utils import timezone
            now = timezone.now()
            if not coupon.active:
                coupon_error = f"Coupon '{coupon_code}' is inactive."
            elif coupon.starts_at and now < coupon.starts_at:
                coupon_error = f"Coupon '{coupon_code}' is not yet active. It will be available from {coupon.starts_at.strftime('%Y-%m-%d %H:%M')}."
            elif coupon.ends_at and now > coupon.ends_at:
                coupon_error = f"Coupon '{coupon_code}' has expired on {coupon.ends_at.strftime('%Y-%m-%d %H:%M')}."
            elif coupon.usage_limit and coupon.used_count >= coupon.usage_limit:
                coupon_error = f"Coupon '{coupon_code}' has reached its usage limit ({coupon.used_count}/{coupon.usage_limit})."
            else:
                coupon_error = f"Coupon '{coupon_code}' is invalid."
        else:
            # Apply coupon only to non-Sale subtotal
            discounted_non_sale_subtotal = coupon.apply(non_sale_subtotal)
            discount = non_sale_subtotal - discounted_non_sale_subtotal
            # Final subtotal = discounted non-Sale items + original Sale items
            subtotal = discounted_non_sale_subtotal + sale_subtotal
            
            if apply_usage:
                coupon.used_count += 1
                coupon.save(update_fields=["used_count"])
            coupon_applied = coupon.code
    
    return subtotal, discount, coupon_applied, coupon_error


def _get_shipping_option(shipping_option_id: Optional[str]) -> tuple[Optional[ShippingOption], Decimal]:
    """Get shipping option and return (shipping_option, shipping_cost)."""
    shipping_option = None
    shipping_cost = Decimal("0.00")

    if shipping_option_id:
        try:
            shipping_id = int(shipping_option_id)
            shipping_option = ShippingOption.objects.get(id=shipping_id)
            shipping_cost = getattr(shipping_option, "price", Decimal("0")) or Decimal("0")
        except (ValueError, TypeError, ShippingOption.DoesNotExist):
            shipping_option = None
            shipping_cost = Decimal("0.00")

    return shipping_option, shipping_cost


_CHECKOUT_COUNTRY_CODE_MAP = {
    "AL": "Albania",
    "AD": "Andorra",
    "BA": "Bosnia and Herzegovina",
    "VA": "Vatican",
    "GB": "United Kingdom",
    "IS": "Iceland",
    "LI": "Liechtenstein",
    "MC": "Monaco",
    "ME": "Montenegro",
    "NO": "Norway",
    "SM": "San Marino",
    "RS": "Serbia",
    "CH": "Switzerland",
    "BH": "Bahrain",
    "JP": "Japan",
    "QA": "Qatar",
    "SA": "Saudi Arabia",
    "AE": "United Arab Emirates",
    "ZA": "South Africa",
    "CA": "Canada",
    "CR": "Costa Rica",
    "US": "United States",
    "AU": "Australia",
    "NZ": "New Zealand",
}

_CHECKOUT_ALLOWED_COUNTRIES = {
    "Albania",
    "Andorra",
    "Bosnia and Herzegovina",
    "Vatican",
    "United Kingdom",
    "Iceland",
    "Liechtenstein",
    "Monaco",
    "Montenegro",
    "Norway",
    "San Marino",
    "Serbia",
    "Switzerland",
    "Bahrain",
    "Japan",
    "Qatar",
    "Saudi Arabia",
    "United Arab Emirates",
    "South Africa",
    "Canada",
    "Costa Rica",
    "United States",
    "Australia",
    "New Zealand",
}


def _ensure_default_shipping_options() -> None:
    """Ensure standard shipping rows exist. Never delete all options (breaks FKs and orders)."""
    defaults = [
        ("Free Shipping", Decimal("0.00"), "7-10 business days"),
        ("Standard Shipping", Decimal("5.99"), "5-7 business days"),
        ("Express Shipping", Decimal("19.99"), "2-3 business days"),
    ]
    for name, price, delivery_time in defaults:
        ShippingOption.objects.update_or_create(
            name=name,
            defaults={"price": price, "delivery_time": delivery_time},
        )


def _normalize_checkout_country(country: str) -> Optional[str]:
    c = (country or "").strip()
    if not c:
        return None
    if c in _CHECKOUT_COUNTRY_CODE_MAP:
        c = _CHECKOUT_COUNTRY_CODE_MAP[c]
    if c not in _CHECKOUT_ALLOWED_COUNTRIES:
        return None
    return c


def _checkout_compute_total(
    cart_items,
    *,
    coupon_code: str,
    country_full: Optional[str],
    shipping_option_id: Optional[str],
    apply_coupon_usage: bool,
) -> tuple[Optional[Decimal], Optional[ShippingOption], Optional[str], Optional[str], Optional[str]]:
    """
    Server-side payable total for Stripe and order creation.
    Returns (total, shipping_option, coupon_applied, coupon_error, fatal_error).
    """
    subtotal = _compute_subtotal(cart_items)
    subtotal, discount, coupon_applied, coupon_error = _process_coupon(
        (coupon_code or "").strip().upper(),
        subtotal,
        apply_usage=apply_coupon_usage,
        cart_items=cart_items,
    )
    if coupon_error:
        return None, None, coupon_applied, coupon_error, "coupon"

    shipping_option, shipping_cost = _get_shipping_option(shipping_option_id)
    if shipping_option_id and not shipping_option:
        return None, None, coupon_applied, None, "shipping_option"

    if not shipping_option_id and country_full:
        if country_full in ["Canada", "Australia", "New Zealand", "Norway"]:
            standard_shipping = ShippingOption.objects.filter(price=Decimal("5.99")).first()
            if standard_shipping:
                shipping_option = standard_shipping
                shipping_cost = standard_shipping.price
            else:
                shipping_cost = Decimal("5.99")
        else:
            shipping_cost = Decimal("0.00")

    total = (subtotal + shipping_cost).quantize(Decimal("0.01"))
    return total, shipping_option, coupon_applied, coupon_error, None


def _stripe_checkout_currency() -> str:
    cur = (getattr(settings, "STRIPE_CHECKOUT_CURRENCY", None) or "eur").strip().lower()
    if len(cur) != 3 or not cur.isalpha():
        return "eur"
    return cur


_CHECKOUT_CHARGE_CURRENCIES = frozenset({"eur", "usd", "gbp"})


def _checkout_fx_fallback_rates() -> dict[str, Decimal]:
    """Foreign currency units per 1 EUR (same convention as frontend convertPrice)."""
    try:
        usd = Decimal(str(getattr(settings, "CHECKOUT_FX_FALLBACK_USD", "1.09")))
    except Exception:
        usd = Decimal("1.09")
    try:
        gbp = Decimal(str(getattr(settings, "CHECKOUT_FX_FALLBACK_GBP", "0.86")))
    except Exception:
        gbp = Decimal("0.86")
    if usd <= 0:
        usd = Decimal("1.09")
    if gbp <= 0:
        gbp = Decimal("0.86")
    return {"eur": Decimal("1"), "usd": usd, "gbp": gbp}


def _normalize_checkout_charge_currency(raw: Optional[str]) -> str:
    c = (raw or "").strip().lower()
    if len(c) != 3 or not c.isalpha():
        c = _stripe_checkout_currency()
    if c not in _CHECKOUT_CHARGE_CURRENCIES:
        c = "eur"
    return c


def _parse_client_fx_multiplier(val: object) -> Optional[Decimal]:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    try:
        d = Decimal(s)
    except Exception:
        return None
    if d <= 0:
        return None
    return d


def _fx_client_sane_for_charge(client: Decimal, fallback: Decimal) -> bool:
    if fallback <= 0:
        return False
    lo = fallback * Decimal("0.80")
    hi = fallback * Decimal("1.25")
    return lo <= client <= hi


def _resolve_eur_to_charge_multiplier(
    charge_cur: str,
    *,
    fx_usd: Optional[Decimal] = None,
    fx_gbp: Optional[Decimal] = None,
) -> Decimal:
    charge_cur = (charge_cur or "eur").strip().lower()
    fb = _checkout_fx_fallback_rates()
    if charge_cur == "eur":
        return Decimal("1")
    if charge_cur == "usd":
        fbf = fb["usd"]
        if fx_usd is not None and _fx_client_sane_for_charge(fx_usd, fbf):
            return fx_usd
        return fbf
    if charge_cur == "gbp":
        fbf = fb["gbp"]
        if fx_gbp is not None and _fx_client_sane_for_charge(fx_gbp, fbf):
            return fx_gbp
        return fbf
    return Decimal("1")


def _checkout_total_eur_to_charge(
    total_eur: Decimal,
    charge_cur: str,
    *,
    fx_usd: Optional[Decimal] = None,
    fx_gbp: Optional[Decimal] = None,
) -> tuple[Decimal, int, str]:
    """Amount in charge currency, Stripe minor units, normalized lowercase ISO currency."""
    charge_cur = _normalize_checkout_charge_currency(charge_cur)
    mult = _resolve_eur_to_charge_multiplier(charge_cur, fx_usd=fx_usd, fx_gbp=fx_gbp)
    if charge_cur == "eur":
        amount = total_eur.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        amount = (total_eur * mult).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    minor = int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return amount, minor, charge_cur


def _checkout_fx_from_http_post(request: HttpRequest) -> tuple[Optional[Decimal], Optional[Decimal]]:
    return (
        _parse_client_fx_multiplier(request.POST.get("fx_usd")),
        _parse_client_fx_multiplier(request.POST.get("fx_gbp")),
    )


def _checkout_fx_from_wallet_json(data: dict) -> tuple[Optional[Decimal], Optional[Decimal]]:
    return (
        _parse_client_fx_multiplier(data.get("fx_usd")),
        _parse_client_fx_multiplier(data.get("fx_gbp")),
    )


def _verify_stripe_payment_intent_for_checkout(
    payment_intent_id: str,
    expected_minor: int,
    expected_currency: str,
) -> tuple[bool, str]:
    """Ensure the browser-completed PaymentIntent matches server-computed charge."""
    if not (payment_intent_id or "").strip():
        return False, "missing_payment_intent"
    if not settings.STRIPE_SECRET_KEY:
        return False, "stripe_not_configured"
    try:
        pi = stripe.PaymentIntent.retrieve((payment_intent_id or "").strip())
    except stripe_error.StripeError as e:
        return False, f"stripe:{e}"
    want_cur = _normalize_checkout_charge_currency(expected_currency)
    if (pi.currency or "").lower() != want_cur:
        return False, "currency_mismatch"
    if pi.status != "succeeded":
        return False, f"status:{pi.status}"
    received = int(getattr(pi, "amount_received", None) or pi.amount or 0)
    if received != int(expected_minor):
        return False, f"amount:{received}!={expected_minor}"
    return True, ""


def _create_stripe_intent(
    amount_eur: Decimal,
    session_key: Optional[str],
    is_guest: bool = False,
    *,
    idempotency_salt: str = "",
    charge_currency: Optional[str] = None,
    fx_usd: Optional[Decimal] = None,
    fx_gbp: Optional[Decimal] = None,
) -> Optional[stripe.PaymentIntent]:
    """Create Stripe PaymentIntent (amount is catalog total in EUR; charged in charge_currency)."""
    # Check if Stripe is configured
    if not settings.STRIPE_SECRET_KEY:
        logger.error("Stripe secret key is not configured")
        return None
    
    # Validate Stripe API key doesn't contain non-ASCII characters
    try:
        settings.STRIPE_SECRET_KEY.encode('latin-1')
    except UnicodeEncodeError:
        logger.error("Stripe secret key contains non-ASCII characters")
        return None
    
    try:
        charge_cur = _normalize_checkout_charge_currency(
            charge_currency or _stripe_checkout_currency()
        )
        _, cents, charge_cur = _checkout_total_eur_to_charge(
            amount_eur,
            charge_cur,
            fx_usd=fx_usd,
            fx_gbp=fx_gbp,
        )
        # Create a safe idempotency key (only ASCII characters, no Cyrillic)
        # Use hash of session_key to avoid encoding issues
        if session_key:
            # Encode session_key to bytes, then hash it to get only ASCII characters
            try:
                session_hash = hashlib.md5(session_key.encode('utf-8')).hexdigest()
            except UnicodeEncodeError:
                # Fallback: use a hash of the repr if direct encoding fails
                session_hash = hashlib.md5(repr(session_key).encode('utf-8')).hexdigest()
        else:
            session_hash = 'nouser'
        
        salt = (idempotency_salt or "")[:120]
        salt_hash = hashlib.sha256(salt.encode("utf-8", errors="ignore")).hexdigest()[:24] if salt else "nosalt"
        idempotency_key = f"pi-{'g' if is_guest else 'u'}-{session_hash}-{charge_cur}-{cents}-{salt_hash}"
        
        # Ensure idempotency_key is ASCII-safe
        try:
            idempotency_key.encode('latin-1')
        except UnicodeEncodeError:
            logger.error("Idempotency key contains non-ASCII characters: %s", idempotency_key)
            idempotency_key = f"pi-{session_hash}-{charge_cur}-{cents}"
        
        # Ensure all parameters are ASCII-safe
        intent = stripe.PaymentIntent.create(
            amount=int(cents),
            currency=charge_cur,
            idempotency_key=idempotency_key,
        )
        return intent
    except stripe_error.StripeError as e:
        logger.error("Stripe PaymentIntent creation failed: %s", str(e), exc_info=True)
        return None
    except UnicodeEncodeError as e:
        logger.error("UnicodeEncodeError creating Stripe PaymentIntent: %s", str(e), exc_info=True)
        return None
    except Exception as e:
        logger.error("Unexpected error creating Stripe PaymentIntent: %s", str(e), exc_info=True)
        return None


def _get_categories():
    """Get all categories ordered by name with subcategories grouped under their parents (cached for 1 hour)."""
    cache_key = 'all_categories_ids'
    cached_ids = cache.get(cache_key)
    
    if cached_ids is None:
        # Get all categories with parent field to properly identify subcategories
        all_categories = list(Category.objects.select_related('parent').all())
        
        # Separate parent categories (those without a parent) and subcategories
        parent_categories = []
        subcategories_dict = {}
        sale_category = None
        
        for cat in all_categories:
            cat_name_lower = cat.name.lower()
            
            # Check if it's a Sale category - skip adding it to parent_categories now
            # We'll add it at the end separately
            if cat_name_lower == 'sale':
                sale_category = cat
                if sale_category.id not in subcategories_dict:
                    subcategories_dict[sale_category.id] = []
                # Skip adding Sale to parent_categories - we'll add it at the end
                continue
            
            # Check if category has a parent (is a subcategory)
            if cat.parent:
                # This is a subcategory - add it under its parent
                parent_id = cat.parent.id
                if parent_id not in subcategories_dict:
                    subcategories_dict[parent_id] = []
                subcategories_dict[parent_id].append(cat)
            else:
                # This is a parent category (no parent field)
                parent_categories.append(cat)
                if cat.id not in subcategories_dict:
                    subcategories_dict[cat.id] = []
        
        # Sort parent categories by name (case-insensitive, stable sort)
        parent_categories.sort(key=lambda x: (x.name.lower(), x.id))
        
        # Sort subcategories within each parent by name
        for parent_id in subcategories_dict:
            subcategories_dict[parent_id].sort(key=lambda x: (x.name.lower(), x.id))
        
        # Build final list: parent category followed by its sub-categories
        categories = []
        for parent in parent_categories:
            categories.append(parent)
            if parent.id in subcategories_dict:
                categories.extend(subcategories_dict[parent.id])
        
        # Add Sale category at the end if it exists (only once)
        if sale_category:
            categories.append(sale_category)
            if sale_category.id in subcategories_dict:
                categories.extend(subcategories_dict[sale_category.id])
        
        # Cache only the IDs to avoid Django model instance caching issues
        category_ids = [cat.id for cat in categories]
        cache.set(cache_key, category_ids, 3600)
    else:
        # Retrieve categories by IDs in the correct order with parent field
        categories = list(Category.objects.select_related('parent').filter(id__in=cached_ids).all())
        # Create a dict for quick lookup
        categories_dict = {cat.id: cat for cat in categories}
        # Rebuild the list in the correct order, ensuring all categories are included
        categories = []
        for cid in cached_ids:
            if cid in categories_dict:
                categories.append(categories_dict[cid])
        # If some categories are missing (maybe deleted), invalidate cache and rebuild
        if len(categories) != len(cached_ids):
            cache.delete(cache_key)
            return _get_categories()  # Recursive call to rebuild cache
    
    return categories


def _published_customer_reviews_queryset():
    return (
        CustomerReview.objects.filter(is_published=True)
        .select_related("related_product", "related_product__category")
        .prefetch_related(
            Prefetch(
                "related_product__images",
                queryset=ProductImage.objects.order_by("id"),
            )
        )
    )


@ensure_csrf_cookie
def home(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q")
    sort = request.GET.get("sort")
    category_slug = request.GET.get("category")
    products = (
        Product.objects
        .all()
        .select_related("category")
        .prefetch_related(Prefetch("images", queryset=ProductImage.objects.all()))
    )

    selected_category = None
    if category_slug:

        selected_category = Category.objects.filter(slug=category_slug).first()
        if not selected_category:
            # Try to find by pk as fallback for backward compatibility
            try:
                category_pk = int(category_slug)
                selected_category = Category.objects.filter(pk=category_pk).first()
            except (ValueError, TypeError):
                pass
        if selected_category:
            products = products.filter(category=selected_category)

    if query:
        products = products.filter(Q(name__icontains=query) | Q(description__icontains=query))


    products = products.annotate(_eff_price=Coalesce("discount_price", "price"))
    if sort == "price_asc":
        products = products.order_by("_eff_price")
    elif sort == "price_desc":
        products = products.order_by("-_eff_price")
    elif sort == "most_sold":
        products = products.order_by("-recently_sold", "-id")
    elif sort == "newest":
        products = products.order_by("-id")


    ids = [int(pk) for pk in request.session.get("recently_viewed", []) if str(pk).isdigit()]
    recently_viewed_qs = Product.objects.none()
    if ids:
        preserved_order = Case(*[When(id=pk, then=pos) for pos, pk in enumerate(ids)], output_field=IntegerField())
        recently_viewed_qs = (
            Product.objects
            .filter(id__in=ids)
            .select_related("category")
            .prefetch_related(Prefetch("images", queryset=ProductImage.objects.all()))
            .order_by(preserved_order)
        )


    POPULAR_LIMIT = 15
    # Generate cache key based on 5-minute intervals (round down to nearest 5 minutes)
    # This ensures all requests within the same 5-minute window get the same random products
    current_timestamp = int(time.time())
    five_minutes = 300  # 5 minutes in seconds
    time_slot = (current_timestamp // five_minutes) * five_minutes
    cache_key_popular = f'popular_products_{time_slot}'
    popular_products = cache.get(cache_key_popular)
    if popular_products is None:
        # Get random products for "Trending Now" section
        popular_products = list(
            Product.objects
            .select_related("category")
            .prefetch_related(Prefetch("images", queryset=ProductImage.objects.all()))
            .order_by('?')[:POPULAR_LIMIT]  # Random order
        )
        cache.set(cache_key_popular, popular_products, five_minutes + 10)  # Cache for 5 min + 10 sec buffer


    cache_key_editors = f'editors_choice_products_{time_slot}'
    editors_choice = cache.get(cache_key_editors)
    if editors_choice is None:
        # Get random products for "Handpicked This Month" section
        editors_choice = list(
            Product.objects.select_related("category")
            .prefetch_related(
                Prefetch(
                    "images", 
                    queryset=ProductImage.objects.order_by('id')
                )
            )
            .order_by('?')[:10]  # Random order
        )
        cache.set(cache_key_editors, editors_choice, five_minutes + 10)  # Cache for 5 min + 10 sec buffer

    # Get active banner images for carousel
    banner_images = BannerImage.objects.filter(is_active=True).order_by("order", "-created_at")[:4]

    customer_reviews_all = list(_published_customer_reviews_queryset())
    customer_reviews_count = len(customer_reviews_all)

    context = {
        "products": products,
        "recently_viewed_products": list(recently_viewed_qs[:5]),
        "recently_viewed_full_count": recently_viewed_qs.count(),
        "categories": _get_categories(),
        "favorite_ids": (
            list(Favorite.objects.filter(user=request.user).values_list("product_id", flat=True))
            if request.user.is_authenticated
            else []
        ),
        "popular_products": popular_products,
        "editors_choice": editors_choice,
        "blog_posts": BlogPost.objects.filter(is_published=True)[:3],
        "customer_reviews": customer_reviews_all[:6],
        "customer_reviews_all": customer_reviews_all,
        "customer_reviews_count": customer_reviews_count,
        "banner_images": banner_images,
        "sort": sort,
        "query": query,
        "selected_category": selected_category,
    }

    if request.headers.get("HX-Request") == "true":
        return render(request, "home_partial.html", context)
    return render(request, "home.html", context)


def customer_reviews_list(request: HttpRequest) -> HttpResponse:
    reviews = list(_published_customer_reviews_queryset())
    return render(
        request,
        "customer_reviews.html",
        {
            "customer_reviews": reviews,
            "customer_reviews_count": len(reviews),
        },
    )


def products_by_category(request: HttpRequest, slug: str) -> HttpResponse:
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"products_by_category called with slug: '{slug}'")
    logger.info(f"Request path: {request.path}")
    logger.info(f"Request GET params: {request.GET}")
    
    # Clear category cache to ensure fresh data
    cache.delete('all_categories_ids')
    
    # If slug is numeric, it's an old pk-based URL - find category and redirect once
    if slug.isdigit():
        from django.shortcuts import redirect
        from django.utils.text import slugify
        try:
            category_pk = int(slug)
            category = Category.objects.only('id', 'name', 'slug').get(pk=category_pk)
            
            # Check if category already has a proper non-numeric slug
            if category.slug and not category.slug.isdigit() and category.slug != slug:
                # Category has proper slug, redirect to it (one-time redirect)
                return redirect('products_by_category', slug=category.slug, permanent=True)
            
            # Category has numeric slug or no slug - generate proper slug from name
            proper_slug = slugify(category.name or "")
            if not proper_slug:
                proper_slug = f"category-{category.pk}"
            
            # Ensure uniqueness
            counter = 1
            base_slug = proper_slug
            while Category.objects.filter(slug=proper_slug).exclude(pk=category.pk).exists():
                proper_slug = f"{base_slug}-{counter}"
                counter += 1
            
            # Update category slug in database
            Category.objects.filter(pk=category.pk).update(slug=proper_slug)
            # Redirect to new slug (one-time redirect, no loop)
            return redirect('products_by_category', slug=proper_slug, permanent=True)
        except Category.DoesNotExist:
            from django.http import Http404
            raise Http404("Category not found")
    
    # Find category by slug - simple lookup
    # Use filter().first() instead of get() to avoid MultipleObjectsReturned exception
    # IMPORTANT: Use exact match to avoid any partial matches
    # Use get() with exact slug match to ensure we get the right category
    logger.info(f"Looking up category with slug: '{slug}'")
    
    # First, get all categories to see what we have
    all_cats = Category.objects.only('id', 'name', 'slug').all()
    logger.info(f"All categories in DB: {[(c.id, c.name, c.slug) for c in all_cats]}")
    
    try:
        category = Category.objects.only('id', 'name', 'slug').get(slug=slug)
        logger.info(f"Found category via get(): ID {category.pk}, Name: '{category.name}', Slug: '{category.slug}'")
    except Category.DoesNotExist:
        logger.warning(f"Category with slug '{slug}' not found via get()")
        category = None
    except Category.MultipleObjectsReturned as e:
        logger.warning(f"Multiple categories found with slug '{slug}': {e}")
        # If multiple categories have the same slug, get the first one
        category = Category.objects.only('id', 'name', 'slug').filter(slug=slug).first()
        if category:
            logger.info(f"Using first category: ID {category.pk}, Name: '{category.name}', Slug: '{category.slug}'")
    
    if not category:
        # Log for debugging - this should never happen if URLs are correct
        available_slugs = list(Category.objects.only('slug').values_list('slug', flat=True))
        logger.error(f"Category with slug '{slug}' not found. Request path: {request.path}. Available slugs: {available_slugs}")
        from django.http import Http404
        raise Http404(f"Category with slug '{slug}' not found")
    
    # Double-check we got the right category
    if category.slug != slug:
        logger.error(f"Category slug mismatch! Requested: '{slug}', Found: '{category.slug}' (ID: {category.pk}, Name: '{category.name}')")
        from django.http import Http404
        raise Http404(f"Category slug mismatch")
    
    logger.info(f"Final category selected: ID {category.pk}, Name: '{category.name}', Slug: '{category.slug}'")
    sort = request.GET.get("sort")

    # Get all subcategories for this category (categories with this category as parent)
    subcategories = Category.objects.filter(parent=category).values_list('id', flat=True)
    subcategory_ids = list(subcategories)
    
    # Build query to include products from the category AND its subcategories
    category_filter = Q(categories=category) | Q(category=category)
    
    # Add subcategories to the filter
    if subcategory_ids:
        subcategory_filter = Q(categories__id__in=subcategory_ids) | Q(category_id__in=subcategory_ids)
        category_filter = category_filter | subcategory_filter
        logger.info(f"Found {len(subcategory_ids)} subcategories: {subcategory_ids}")
    
    # Use categories ManyToManyField if available, fallback to category ForeignKey
    # Prefetch images
    products = Product.objects.filter(category_filter).select_related("category").prefetch_related(
        Prefetch(
            "images", 
            queryset=ProductImage.objects.order_by('id')
        )
    ).distinct()
    
    # Log product count for debugging
    product_count = products.count()
    logger.info(f"Found {product_count} products for category '{category.name}' (including {len(subcategory_ids)} subcategories)")
    
    products = products.annotate(_eff_price=Coalesce("discount_price", "price"))
    if sort == "price_asc":
        products = products.order_by("_eff_price")
    elif sort == "price_desc":
        products = products.order_by("-_eff_price")
    elif sort == "most_sold":
        products = products.order_by("-recently_sold", "-id")
    elif sort == "newest":
        products = products.order_by("-id")

    recently_viewed_ids = request.session.get("recently_viewed", [])
    recently_viewed = Product.objects.filter(id__in=recently_viewed_ids)


    cache_key_popular = 'popular_products'
    popular_products = cache.get(cache_key_popular)
    if popular_products is None:
        popular_products = list(
            Product.objects
            .select_related("category")
            .prefetch_related(Prefetch("images", queryset=ProductImage.objects.all()))
            .annotate(_pop=Coalesce("cart_add_count", Value(0)))
            .order_by("-_pop", "-id")[:15]
        )
        cache.set(cache_key_popular, popular_products, 1800)
    favorite_ids = (
        list(Favorite.objects.filter(user=request.user).values_list("product_id", flat=True))
        if request.user.is_authenticated
        else []
    )

    # Get sub-categories for the selected category using parent field
    subcategories = []
    if category:
        # Get all categories that have this category as parent
        subcategories = list(Category.objects.filter(parent=category).order_by('name'))
    
    context = {
        "products": products,
        "categories": _get_categories(),
        "selected_category": category,
        "subcategories": subcategories,
        "recently_viewed": recently_viewed,
        "popular_products": popular_products,
        "favorite_ids": favorite_ids,
        "sort": sort,
    }

    return render(request, "home.html", context)


def product_list(request: HttpRequest) -> HttpResponse:
    selected_category = None
    category_id = request.GET.get("category")
    sort = request.GET.get("sort")

    if category_id:
        selected_category = get_object_or_404(Category, id=category_id)
        products = (
            Product.objects
            .filter(category=selected_category)
            .select_related("category")
            .prefetch_related(Prefetch("images", queryset=ProductImage.objects.all()))
        )
    else:
        products = (
            Product.objects
            .all()
            .select_related("category")
            .prefetch_related(Prefetch("images", queryset=ProductImage.objects.all()))
        )

    products = products.annotate(_eff_price=Coalesce("discount_price", "price"))
    if sort == "price_asc":
        products = products.order_by("_eff_price")
    elif sort == "price_desc":
        products = products.order_by("-_eff_price")
    elif sort == "most_sold":
        products = products.order_by("-recently_sold", "-id")
    elif sort == "newest":
        products = products.order_by("-id")

    categories = _get_categories()
    recently_viewed_ids = request.session.get("recently_viewed", [])
    recently_viewed = (
        Product.objects
        .filter(id__in=recently_viewed_ids)
        .select_related("category")
        .prefetch_related(Prefetch("images", queryset=ProductImage.objects.all()))
    )
    recently_viewed = sorted(recently_viewed, key=lambda x: recently_viewed_ids.index(x.id))

    return render(
        request,
        "product_list.html",
        {
            "products": products,
            "categories": categories,
            "selected_category": selected_category,
            "recently_viewed": recently_viewed,
            "sort": sort,
        },
    )


def product_detail(request: HttpRequest, slug: str) -> HttpResponse:
    # Check if variant_type field exists (for backward compatibility before migration)
    try:
        # Try to order by variant_type and size if field exists
        variants_qs = ProductVariant.objects.order_by("variant_type", "size")
    except Exception:
        # Fallback to size only if variant_type doesn't exist yet
        variants_qs = ProductVariant.objects.order_by("size")
    
    product_qs = (
        Product.objects.select_related("category").prefetch_related(
            Prefetch("images", queryset=ProductImage.objects.all().order_by('id')),
            Prefetch("variants", queryset=variants_qs),
        )
    )
    product = get_object_or_404(product_qs, slug=slug)

    # Get all product images
    all_product_images = list(ProductImage.objects.filter(product=product).order_by('id'))
    
    logger.debug(f"Product {product.pk} ({product.name}): Found {len(all_product_images)} ProductImage records")
    
    # Handle main product image - add it if it doesn't exist in product images
    product_images = []
    if product.image:
        main_image_path = product.image.name
        # Check if main image exists in product images
        image_exists = any(
            hasattr(img, 'image') and img.image.name == main_image_path
            for img in all_product_images
        )
        if not image_exists:
            # Add main image as first image if it doesn't exist
            main_img_obj = SimpleNamespace(image=product.image)
            product_images.append(main_img_obj)
            logger.debug(f"Added main image {main_image_path} to product_images list")
    
    # Add all product images
    product_images.extend(all_product_images)
    
    # Filter out images without valid image attribute
    product_images = [img for img in product_images if hasattr(img, 'image') and img.image]
    
    logger.debug(f"Total images for product {product.pk}: {len(product_images)}")
    
    # Check if variant_type field exists for template (for backward compatibility)
    from django.db import connection
    try:
        with connection.cursor() as cursor:
            cursor.execute("SHOW COLUMNS FROM ecommerce_productvariant LIKE 'variant_type'")
            has_variant_type_field = cursor.fetchone() is not None
    except Exception:
        has_variant_type_field = False
    
    # Check if product has zodiac variants and filter variants accordingly
    has_zodiac_variants = False
    has_earring_hoop_variants = False
    filtered_variants = list(product.variants.all())
    
    if has_variant_type_field and product.variants.exists():
        try:
            # Check if product has different variant types
            zodiac_variants = product.variants.filter(variant_type='zodiac_sign')
            ring_size_variants = product.variants.filter(variant_type='ring_size')
            earring_hoop_variants = product.variants.filter(variant_type='earring_hoop_size')
            
            if zodiac_variants.exists():
                has_zodiac_variants = True
                # If product has zodiac variants, show only zodiac variants
                filtered_variants = list(zodiac_variants.order_by('size'))
            elif earring_hoop_variants.exists():
                has_earring_hoop_variants = True
                # If product has earring hoop size variants, show only earring hoop variants
                filtered_variants = list(earring_hoop_variants.order_by('size'))
            elif ring_size_variants.exists():
                # If product has ring size variants, show only ring size variants
                filtered_variants = list(ring_size_variants.order_by('size'))
        except Exception:
            # Fallback: use all variants if variant_type check fails
            filtered_variants = list(product.variants.all().order_by('size'))
    
    categories = _get_categories()

    _ensure_session(request)
    session_key = request.session.session_key or ""

    product_reviews = list(
        ProductReview.objects.filter(product=product).order_by("-created_at")
    )
    review_agg = ProductReview.objects.filter(product=product).aggregate(
        avg=Avg("rating"),
        cnt=Count("id"),
    )
    average_rating = review_agg["avg"]
    review_count = review_agg["cnt"] or 0
    average_rating_pct = None
    if review_count and average_rating is not None:
        average_rating_pct = min(
            100.0,
            max(0.0, round(float(average_rating) / 5.0 * 100.0, 1)),
        )

    if request.user.is_authenticated:
        user_has_reviewed = ProductReview.objects.filter(
            product=product, user=request.user
        ).exists()
    else:
        user_has_reviewed = bool(session_key) and ProductReview.objects.filter(
            product=product, session_key=session_key
        ).exists()

    review_form = ProductReviewForm()
    review_form_has_errors = False
    if request.method == "POST" and request.POST.get("submit_product_review"):
        if (request.POST.get("website") or "").strip():
            messages.error(request, "Unable to submit review.")
        elif user_has_reviewed:
            messages.warning(
                request,
                "You have already rated this product.",
            )
            return redirect("product_detail", slug=product.slug)
        else:
            review_form = ProductReviewForm(request.POST)
            if review_form.is_valid():
                rev = review_form.save(commit=False)
                rev.product = product
                rev.comment_approved = False
                if request.user.is_authenticated:
                    rev.user = request.user
                    rev.session_key = ""
                else:
                    rev.user = None
                    rev.session_key = session_key
                try:
                    rev.save()
                except IntegrityError:
                    messages.warning(
                        request,
                        "You have already submitted a review for this product.",
                    )
                    return redirect("product_detail", slug=product.slug)
                if (rev.comment or "").strip():
                    messages.success(
                        request,
                        "Thank you! Your comment will appear after we approve it. Your rating is visible already.",
                    )
                else:
                    messages.success(request, "Thank you for your rating!")
                return redirect("product_detail", slug=product.slug)
            review_form_has_errors = True

    rv = [int(i) for i in request.session.get("recently_viewed", []) if str(i).isdigit()]
    pk_i = product.pk
    if pk_i in rv:
        rv.remove(pk_i)
    rv.insert(0, pk_i)
    request.session["recently_viewed"] = rv[:10]

    preserved = Case(*[When(id=pid, then=pos) for pos, pid in enumerate(rv)], output_field=IntegerField())
    recently_viewed_products = (
        Product.objects.filter(pk__in=rv)
        .exclude(pk=product.pk)
        .select_related("category")
        .order_by(preserved)
    )


    you_might_like = (
        Product.objects.filter(category=product.category)
        .exclude(pk=product.pk)
        .annotate(_pop=Coalesce("cart_add_count", Value(0, output_field=IntegerField())))
        .order_by("-_pop", "-id")[:10]
    )


    manual_links = (
        ProductBundleItem.objects
        .filter(product=product, is_active=True)
        .select_related("item__category")
        .order_by("position", "id")
    )
    bundle_ids = [lnk.item_id for lnk in manual_links]

    bundle_items = []
    bundle_items_data = []
    if bundle_ids:
        preserved_bundle = Case(
            *[When(id=pid, then=pos) for pos, pid in enumerate(bundle_ids)],
            output_field=IntegerField()
        )
        bundle_qs = (
            Product.objects
            .filter(pk__in=bundle_ids)
            .select_related("category")
            .prefetch_related(Prefetch("variants", queryset=ProductVariant.objects.order_by("size")))
            .order_by(preserved_bundle)
        )

        for p in bundle_qs:
            qa = {"can": False, "variant_id": None, "needs_size": False, "disabled": False, "reason": ""}

            variants_mgr = getattr(p, "variants", None)
            variants = list(variants_mgr.all()) if variants_mgr is not None else []

            if variants:
                in_stock = [v for v in variants if (getattr(v, "stock", 0) or 0) > 0]
                if len(in_stock) == 1:
                    qa["can"] = True
                    qa["variant_id"] = in_stock[0].id
                elif len(in_stock) == 0:
                    qa["disabled"] = True
                    qa["reason"] = "Out of stock"
                else:
                    qa["needs_size"] = True
            else:

                if (p.stock or 0) > 0:
                    qa["can"] = True
                else:
                    qa["disabled"] = True
                    qa["reason"] = "Out of stock"

            bundle_items.append(p)
            bundle_items_data.append({"product": p, "qa": qa})

    show_bundle = bool(bundle_items)

    # Check if product is in Sale category and has expiration time
    # Also automatically remove from Sale if expired
    is_sale = False
    sale_expires_at = None
    if hasattr(product, 'sale_expires_at') and product.sale_expires_at:
        # Check if sale has expired
        from django.utils import timezone
        now = timezone.now()
        if product.sale_expires_at < now:
            # Sale has expired - remove from Sale category
            sale_category = Category.objects.filter(name__iexact='Sale').first()
            if not sale_category:
                sale_category = Category.objects.filter(name__icontains='разпродажба').first()
            if sale_category and sale_category in product.categories.all():
                product.categories.remove(sale_category)
                logger.info(f"Automatically removed expired product {product.id} from Sale category")
        else:
            # Sale is still active - check if product is in Sale category
            sale_categories = product.categories.filter(name__iexact='Sale')
            if not sale_categories.exists():
                # Try Bulgarian name
                sale_categories = product.categories.filter(name__icontains='разпродажба')
            if sale_categories.exists():
                is_sale = True
                sale_expires_at = product.sale_expires_at

    stripe_public_key = getattr(settings, 'STRIPE_PUBLISHABLE_KEY', None) or ""

    context = {
        "product": product,
        "product_images": product_images,
        "has_zodiac_variants": has_zodiac_variants,
        "has_earring_hoop_variants": has_earring_hoop_variants,
        "filtered_variants": filtered_variants,
        "product_reviews": product_reviews,
        "review_form": review_form,
        "review_form_has_errors": review_form_has_errors,
        "user_has_reviewed": user_has_reviewed,
        "review_count": review_count,
        "favorite_ids": (
            list(Favorite.objects.filter(user=request.user).values_list("product_id", flat=True))
            if request.user.is_authenticated
            else []
        ),
        "average_rating": average_rating,
        "average_rating_pct": average_rating_pct,
        "recently_viewed_products": recently_viewed_products,
        "you_might_like": you_might_like,
        "bundle_items": bundle_items,
        "bundle_items_data": bundle_items_data,
        "show_bundle": show_bundle,
        "categories": categories,
        "selected_category": product.category,
        "is_sale": is_sale,
        "sale_expires_at": sale_expires_at,
        "stripe_public_key": stripe_public_key,
    }
    return render(request, "product_detail.html", context)


@ensure_csrf_cookie
def register_view(request: HttpRequest) -> HttpResponse:
    """Simple registration view without email sending to prevent blocking."""
    logger.info("Register view accessed: method=%s, path=%s", request.method, request.path)
    
    # Ensure CSRF token is available in context
    if request.method == "GET":
        csrf_token = get_token(request)  # Force CSRF token generation
        logger.info("CSRF token generated for GET request: %s", csrf_token[:20] + "..." if csrf_token else "NONE")
    
    try:
        if request.method == "POST":
            logger.info("POST request received for registration")
            logger.info("CSRF Token in POST: %s", request.POST.get('csrfmiddlewaretoken', 'NOT FOUND'))
            logger.info("CSRF Token in headers: %s", request.META.get('HTTP_X_CSRFTOKEN', 'NOT FOUND'))
            logger.info("Origin: %s", request.META.get('HTTP_ORIGIN', 'NOT FOUND'))
            logger.info("Referer: %s", request.META.get('HTTP_REFERER', 'NOT FOUND'))
            logger.info("Host: %s", request.get_host())
            logger.info("CSRF_TRUSTED_ORIGINS: %s", settings.CSRF_TRUSTED_ORIGINS)
            logger.info("CSRF_COOKIE_SECURE: %s", settings.CSRF_COOKIE_SECURE)
            logger.info("CSRF_COOKIE_SAMESITE: %s", settings.CSRF_COOKIE_SAMESITE)
            
            # Bot protection: Check honeypot field
            honeypot = request.POST.get("website", "").strip()
            if honeypot:
                logger.warning("Bot detected: honeypot field filled. IP: %s", request.META.get('REMOTE_ADDR', 'Unknown'))
                messages.error(request, "Registration failed. Please try again.")
                return render(request, "register.html")
            
            # Bot protection: Rate limiting - check IP address
            ip_address = request.META.get('REMOTE_ADDR', 'Unknown')
            cache_key = f"register_attempts_{ip_address}"
            attempts = cache.get(cache_key, 0)
            
            # Allow max 3 registrations per IP per hour
            if attempts >= 3:
                logger.warning("Rate limit exceeded for IP: %s (attempts: %s)", ip_address, attempts)
                messages.error(request, "Too many registration attempts. Please try again later.")
                return render(request, "register.html")
            
            # Bot protection: Time-based validation - check if form submitted too quickly
            import time
            form_load_time = request.POST.get("form_load_time", "")
            if form_load_time:
                try:
                    load_time = int(form_load_time)
                    current_time = int(time.time() * 1000)  # Convert to milliseconds
                    time_spent = current_time - load_time
                    # If form submitted in less than 3 seconds, likely a bot
                    if time_spent < 3000:
                        logger.warning("Bot detected: form submitted too quickly (%s ms). IP: %s", time_spent, ip_address)
                        messages.error(request, "Registration failed. Please try again.")
                        return render(request, "register.html")
                except (ValueError, TypeError):
                    pass
            
            # Increment rate limit counter
            cache.set(cache_key, attempts + 1, 3600)  # Cache for 1 hour
            
            username = (request.POST.get("username") or "").strip()
            email = (request.POST.get("email") or "").strip()
            password = request.POST.get("password") or ""
            confirm_password = request.POST.get("confirm_password") or ""

            if not all([username, email, password, confirm_password]):
                messages.error(request, "All fields are required.")
            elif password != confirm_password:
                messages.error(request, "Passwords do not match.")
            elif len(password) < 8:
                messages.error(request, "Password must be at least 8 characters long.")
            elif User.objects.filter(username=username).exists():
                messages.error(request, "Username already exists.")
            elif User.objects.filter(email=email).exists():
                messages.error(request, "Email already exists.")
            else:
                try:
                    validate_email(email)
                except ValidationError:
                    messages.error(request, "Invalid email address.")
                else:
                    try:
                        user = User.objects.create_user(username=username, email=email, password=password)
                        logger.info("User created successfully: %s", username)
                        
                        # Reset rate limit on successful registration
                        cache.delete(cache_key)
                        
                        # Send user_registered signal to trigger welcome email (in background thread)
                        try:
                            logger.info("Sending user_registered signal for user: %s", username)
                            user_registered.send(sender=User, user=user, request=request)
                            logger.info("user_registered signal sent successfully")
                        except Exception as signal_error:
                            logger.exception("Error sending user_registered signal: %s", signal_error)
                            # Continue anyway - email sending is not critical
                        
                        # Automatically log in the user after registration
                        try:
                            # Set backend explicitly before login
                            backend = 'django.contrib.auth.backends.ModelBackend'
                            user.backend = backend
                            logger.info("Attempting to log in user %s with backend %s", username, backend)
                            
                            # Log in the user
                            login(request, user, backend=backend)
                            
                            # Verify login was successful
                            if request.user.is_authenticated:
                                logger.info("✅ User %s successfully logged in after registration", username)
                                logger.info("   Authenticated user: %s", request.user.username)
                            else:
                                logger.error("❌ Login failed - user is not authenticated after login() call")
                                raise Exception("Login failed - user not authenticated")
                                
                        except Exception as login_error:
                            logger.exception("Error logging in user after registration: %s", login_error)
                            # If login fails, redirect to login page instead
                            messages.warning(request, "Account created successfully. Please log in.")
                            return redirect("login")
                        
                        messages.success(request, "Registration successful! Welcome to Marbaras!")
                        logger.info("Redirecting to home page for user: %s", username)
                        return redirect("home")
                    except Exception as e:
                        logger.exception("Error creating user: %s", e)
                        error_msg = str(e)
                        logger.error("Registration error details: %s", error_msg)
                        messages.error(request, f"An error occurred during registration: {error_msg}. Please try again.")
                        return render(request, "register.html")

        return render(request, "register.html")
    except Exception as e:
        logger.exception("Unexpected error in register_view: %s", e)
        messages.error(request, "An unexpected error occurred. Please try again.")
        return render(request, "register.html")


def login_view(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")

        user = authenticate(request, username=username, password=password)
        if user:
            if user.is_active:
                login(request, user)
                return redirect("home")
            else:
                messages.error(request, "Your account is disabled.")
        else:
            messages.error(request, "Invalid username or password.")

    return render(request, "login.html")


def logout_view(request: HttpRequest) -> HttpResponse:
    logout(request)
    return redirect("home")


class CustomLoginView(LoginView):
    template_name = "login.html"






@login_required
def favorites_list(request: HttpRequest) -> HttpResponse:
    favorites = Favorite.objects.filter(user=request.user).select_related("product")
    return render(request, "favorites.html", {"favorites": favorites})


@login_required
def toggle_favorite(request: HttpRequest, pk: int) -> HttpResponse:
    import logging
    logger = logging.getLogger(__name__)

    if request.method == "POST":
        try:
            logger.info(f"Toggle favorite request: user={request.user.username}, product_id={pk}, is_ajax={request.headers.get('X-Requested-With') == 'XMLHttpRequest'}")

            product = get_object_or_404(Product, pk=pk)
            favorite, created = Favorite.objects.get_or_create(user=request.user, product=product)


            if not created:
                favorite.delete()
                is_favorite = False
                logger.info(f"Removed favorite: user={request.user.username}, product_id={pk}")
            else:
                is_favorite = True
                logger.info(f"Added favorite: user={request.user.username}, product_id={pk}")


            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                response_data = {
                    "success": True,
                    "is_favorite": is_favorite,
                    "message": "Added to favorites" if is_favorite else "Removed from favorites"
                }
                logger.info(f"Returning JSON response: {response_data}")
                return JsonResponse(response_data)

            return redirect("product_detail", slug=product.slug)
        except Exception as e:
            logger.error(f"Error toggling favorite: {e}", exc_info=True)

            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    "success": False,
                    "error": str(e)
                }, status=500)

            return redirect("product_detail", slug=product.slug)

    return JsonResponse({"error": "Invalid request"}, status=400)






@ensure_csrf_cookie
def cart_view(request: HttpRequest) -> HttpResponse:
    cart_items = _cart_items_for(request)
    subtotal = _compute_subtotal(cart_items)
    cart_count = sum(getattr(it, "quantity", 0) for it in cart_items)
    categories = _get_categories()
    stripe_public_key = settings.STRIPE_PUBLISHABLE_KEY

    context = {
        "cart_items": cart_items,
        "subtotal": subtotal,
        "is_guest": not request.user.is_authenticated,
        "categories": categories,
        "selected_category": None,
        "cart_count": cart_count,
        "show_sidebars": True,
        "stripe_public_key": stripe_public_key,
    }
    return render(request, "cart.html", context)


@require_POST
def update_cart_quantity(request: HttpRequest, pk: int) -> HttpResponse:
    try:
        new_qty = int(request.POST.get("quantity"))
        if new_qty < 1:
            raise ValueError
    except (ValueError, TypeError):
        messages.error(request, "Invalid quantity.")
        return redirect("cart_view")

    owner = _owner_filter(request)
    try:
        cart_item = CartItem.objects.filter(pk=pk, **owner).select_related("product", "variant").get()
    except CartItem.DoesNotExist:
        messages.error(request, "Item not found in cart.")
        return redirect("cart_view")

    product = cart_item.product
    variant_id = cart_item.variant_id if cart_item.variant else None
    available = _available_stock(product, variant_id=variant_id)
    capped = _cap_quantity(new_qty, available)

    if capped == 0:
        cart_item.delete()
        messages.warning(request, "Item removed (out of stock).")
        return redirect("cart_view")

    cart_item.quantity = capped
    cart_item.save(update_fields=["quantity"])

    if capped < new_qty:
        messages.warning(request, f"Only {available} available. Quantity set to {capped}.")
    else:
        messages.success(request, "Cart updated.")

    return redirect("cart_view")


@require_POST
def remove_from_cart(request: HttpRequest, pk: int) -> HttpResponse:
    owner = _owner_filter(request)
    try:
        cart_item = CartItem.objects.filter(pk=pk, **owner).get()
        cart_item.delete()
        messages.success(request, "Item removed.")
    except CartItem.DoesNotExist:
        messages.error(request, "Item not found in cart.")
    return redirect("cart_view")


def _meta_pixel_add_to_cart_payload(
    product: Product, variant: Optional[ProductVariant], quantity: int
) -> dict:
    """Parameters for Meta Pixel AddToCart (matches ViewContent style)."""
    if quantity < 1:
        quantity = 1
    if variant is not None:
        unit_dec = Decimal(str(variant.effective_price))
        try:
            extra = (variant.display_name or variant.size or "").strip()
        except Exception:
            extra = (variant.size or "").strip()
        content_name = f"{product.name} — {extra}" if extra else product.name
    else:
        unit_dec = Decimal(str(product.get_discounted_price()))
        content_name = product.name
    cid = (
        str(product.serial_number).strip()
        if getattr(product, "serial_number", None)
        else ""
    ) or str(product.id)
    cur = getattr(settings, "META_PIXEL_CURRENCY", "EUR")
    item_price = float(unit_dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    val = float(
        (unit_dec * Decimal(quantity)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )
    return {
        "content_ids": [cid],
        "content_name": content_name,
        "content_type": "product",
        "value": val,
        "currency": cur,
        "contents": [{"id": cid, "quantity": int(quantity), "item_price": item_price}],
    }


def _tiktok_pixel_add_to_cart_payload(
    product: Product, variant: Optional[ProductVariant], quantity: int
) -> dict:
    """Flat params for TikTok Pixel ttq.track('AddToCart', ...)."""
    if quantity < 1:
        quantity = 1
    if variant is not None:
        unit_dec = Decimal(str(variant.effective_price))
        try:
            extra = (variant.display_name or variant.size or "").strip()
        except Exception:
            extra = (variant.size or "").strip()
        content_name = f"{product.name} — {extra}" if extra else product.name
    else:
        unit_dec = Decimal(str(product.get_discounted_price()))
        content_name = product.name
    cid = (
        str(product.serial_number).strip()
        if getattr(product, "serial_number", None)
        else ""
    ) or str(product.id)
    cur = getattr(settings, "TIKTOK_PIXEL_CURRENCY", "EUR")
    item_price = float(unit_dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    val = float(
        (unit_dec * Decimal(quantity)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )
    return {
        "content_id": cid,
        "content_type": "product",
        "content_name": content_name,
        "currency": cur,
        "value": val,
        "quantity": int(quantity),
        "price": item_price,
    }


def _initiate_checkout_pixel_payloads(
    cart_items: Iterable[CartItem], subtotal: Decimal
) -> Optional[dict]:
    """
    Serializable payload for Meta + TikTok InitiateCheckout on checkout page (GET).
    Uses cart subtotal as value (shipping finalized on submit).
    """
    cur_meta = getattr(settings, "META_PIXEL_CURRENCY", "EUR")
    cur_tt = getattr(settings, "TIKTOK_PIXEL_CURRENCY", "EUR")
    content_ids: list[str] = []
    contents: list[dict] = []
    tiktok_contents: list[dict] = []
    num_items = 0
    for item in cart_items:
        qty = int(getattr(item, "quantity", 0) or 0)
        if qty <= 0:
            continue
        p = item.product
        v = getattr(item, "variant", None)
        if v is not None:
            unit_dec = Decimal(str(v.effective_price))
            try:
                extra = (v.display_name or v.size or "").strip()
            except Exception:
                extra = (v.size or "").strip()
            content_name = f"{p.name} — {extra}" if extra else p.name
        else:
            unit_dec = Decimal(str(p.get_discounted_price()))
            content_name = p.name
        cid = (
            str(p.serial_number).strip()
            if getattr(p, "serial_number", None)
            else ""
        ) or str(p.id)
        num_items += qty
        item_price = float(unit_dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        content_ids.append(cid)
        contents.append({"id": cid, "quantity": qty, "item_price": item_price})
        tiktok_contents.append(
            {
                "content_id": cid,
                "content_type": "product",
                "content_name": content_name,
                "quantity": qty,
                "price": item_price,
            }
        )
    if not content_ids:
        return None
    val = float(subtotal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return {
        "meta": {
            "content_type": "product",
            "content_ids": content_ids,
            "contents": contents,
            "num_items": num_items,
            "value": val,
            "currency": cur_meta,
        },
        "tiktok": {
            "contents": tiktok_contents,
            "value": val,
            "currency": cur_tt,
        },
    }


def _purchase_pixel_payload_from_order(order: Order) -> Optional[dict]:
    """Meta + TikTok Purchase payload from a completed Order (line items + total)."""
    cur_meta = getattr(settings, "META_PIXEL_CURRENCY", "EUR")
    cur_tt = getattr(settings, "TIKTOK_PIXEL_CURRENCY", "EUR")
    items = list(order.items.select_related("product", "variant").all())
    if not items:
        return None
    content_ids: list[str] = []
    contents: list[dict] = []
    tiktok_contents: list[dict] = []
    num_items = 0
    for oi in items:
        qty = int(oi.quantity or 0)
        if qty <= 0:
            continue
        p = oi.product
        v = oi.variant
        if v is not None:
            unit_dec = Decimal(str(v.effective_price))
            try:
                extra = (v.display_name or v.size or "").strip()
            except Exception:
                extra = (v.size or "").strip()
            content_name = f"{p.name} — {extra}" if extra else p.name
        else:
            unit_dec = Decimal(str(p.get_discounted_price()))
            content_name = p.name
        cid = (
            str(p.serial_number).strip()
            if getattr(p, "serial_number", None)
            else ""
        ) or str(p.id)
        num_items += qty
        item_price = float(unit_dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        content_ids.append(cid)
        contents.append({"id": cid, "quantity": qty, "item_price": item_price})
        tiktok_contents.append(
            {
                "content_id": cid,
                "content_type": "product",
                "content_name": content_name,
                "quantity": qty,
                "price": item_price,
            }
        )
    if not content_ids:
        return None
    val = float(order.total_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    oid = str(order.pk)
    return {
        "meta": {
            "content_type": "product",
            "content_ids": content_ids,
            "contents": contents,
            "num_items": num_items,
            "value": val,
            "currency": cur_meta,
            "order_id": oid,
        },
        "meta_event_id": f"purchase-{oid}",
        "tiktok_event_id": f"purchase-{oid}",
        "tiktok": {
            "contents": tiktok_contents,
            "value": val,
            "currency": cur_tt,
            "content_type": "product",
            "order_id": oid,
        },
    }


def _finalize_checkout_purchase_tracking(request: HttpRequest, order: Order) -> None:
    """Store browser pixel payload in session and schedule server-side CAPI / TikTok Events API."""
    try:
        px = _purchase_pixel_payload_from_order(order)
        if px:
            request.session["marbaras_purchase_pixel"] = px
            request.session.modified = True
    except Exception:
        logger.warning("marbaras_purchase_pixel failed", exc_info=True)
        return
    if not px:
        return
    if not (
        (getattr(settings, "META_CAPI_ACCESS_TOKEN", "") or "").strip()
        or (getattr(settings, "TIKTOK_EVENTS_API_ACCESS_TOKEN", "") or "").strip()
    ):
        return
    try:
        from ecommerce.utils.capi import schedule_purchase_capi

        schedule_purchase_capi(
            order_id=order.pk,
            purchase_pixel=px,
            client_ip=_client_ip(request),
            user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:2048],
            fbp=request.COOKIES.get("_fbp", "") or "",
            fbc=request.COOKIES.get("_fbc", "") or "",
            email=(order.email or "").strip(),
        )
    except Exception:
        logger.warning("schedule_purchase_capi failed", exc_info=True)


_ORDER_SUCCESS_TOKEN_SALT = "marbaras-order-success"
_ORDER_SUCCESS_TOKEN_MAX_AGE = 60 * 60 * 24 * 14  # 14 days


def _order_success_query_token(order_id: int) -> str:
    return TimestampSigner(salt=_ORDER_SUCCESS_TOKEN_SALT).sign(str(order_id))


def _order_id_from_success_token(request: HttpRequest) -> Optional[int]:
    raw = (request.GET.get("o") or "").strip()
    if not raw:
        return None
    try:
        signer = TimestampSigner(salt=_ORDER_SUCCESS_TOKEN_SALT)
        oid_str = signer.unsign(raw, max_age=_ORDER_SUCCESS_TOKEN_MAX_AGE)
        return int(oid_str)
    except SignatureExpired:
        logger.warning(
            "order_success: signed param o= expired (max_age=%s days)",
            _ORDER_SUCCESS_TOKEN_MAX_AGE // 86400,
        )
        return None
    except BadSignature:
        logger.warning(
            "order_success: signed param o= invalid (truncated URL, proxy stripped query, "
            "or SECRET_KEY changed since order was placed)",
        )
        return None
    except ValueError:
        logger.warning("order_success: signed param o= could not be parsed as order id")
        return None


def _redirect_order_success(order: Order) -> HttpResponse:
    q = urlencode({"o": _order_success_query_token(order.pk)})
    return redirect(f"{reverse('order_success')}?{q}")


def _notify_order_confirmed(request: HttpRequest, order: Order) -> None:
    """
    Send buyer order confirmation email after the checkout DB transaction has committed.
    Runs in the request thread (not transaction.on_commit) so SMTP reliably finishes
    before the worker returns the redirect/JSON response.
    """
    base_url = ""
    if request is not None:
        try:
            base_url = request.build_absolute_uri("/").rstrip("/")
        except Exception:
            pass
    if not (base_url or "").strip():
        base_url = (getattr(settings, "PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    if not base_url:
        base_url = "https://www.marbaras.com"

    try:
        order_db = (
            Order.objects.select_related("coupon", "shipping_option", "user")
            .prefetch_related(
                Prefetch("items", queryset=OrderItem.objects.select_related("product", "variant"))
            )
            .get(pk=order.pk)
        )
    except Order.DoesNotExist:
        logger.error("_notify_order_confirmed: order %s not found", getattr(order, "pk", None))
        return

    try:
        ok = send_order_confirmation_email(order_db, base_url, notify_admin=True)
        if not ok:
            logger.warning(
                "_notify_order_confirmed: send_order_confirmation_email returned False for order #%s",
                order_db.pk,
            )
    except Exception:
        logger.exception("_notify_order_confirmed: email failed for order #%s", order_db.pk)

    try:
        order_submitted.send(sender=Order, order=order_db, request=request, base_url=base_url)
    except Exception:
        logger.exception("_notify_order_confirmed: order_submitted signal failed for order #%s", order_db.pk)

    _schedule_auto_shipping_label(order_db.pk)


def _schedule_auto_shipping_label(order_id: int) -> None:
    """
    Fire-and-forget automatic shipping label creation for a freshly placed
    order. Runs in a daemon thread so the customer response is not delayed
    by the carrier API. The label ends up on the Print Queue page once the
    carrier returns it (usually a few seconds later).

    Controlled by ``settings.SHIPPING_AUTO_CREATE_LABEL`` (default True).
    Set to False to require manual "Create label" from the admin instead.
    """
    if not order_id:
        return
    if not bool(getattr(settings, "SHIPPING_AUTO_CREATE_LABEL", True)):
        logger.info(
            "_schedule_auto_shipping_label: auto-create disabled, skipping order #%s",
            order_id,
        )
        return
    try:
        import threading
        from ecommerce.utils.shipping import auto_create_shipping_label

        thread = threading.Thread(
            target=auto_create_shipping_label,
            args=(order_id,),
            name=f"auto-label-order-{order_id}",
            daemon=True,
        )
        thread.start()
        logger.info(
            "_schedule_auto_shipping_label: background thread started for order #%s",
            order_id,
        )
    except Exception:
        logger.exception(
            "_schedule_auto_shipping_label: failed to start background thread for order #%s",
            order_id,
        )


@require_POST
def add_to_cart(request: HttpRequest, pk: int) -> HttpResponse:

    _ensure_session(request)

    product = get_object_or_404(Product, pk=pk)
    raw_variant = request.POST.get("variant_id")
    try:
        variant_id = int(raw_variant) if raw_variant not in (None, "", "None") else None
    except (TypeError, ValueError):
        variant_id = None

    if product.variants.exists() and variant_id is None:
        msg = "Please open the product page and select size or option before adding to cart."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": False, "message": msg}, status=400)
        messages.error(request, msg)
        return redirect("product_detail", slug=product.slug)

    try:
        quantity = max(1, int(request.POST.get("quantity", 1)))
    except (TypeError, ValueError):
        quantity = 1

    owner = _owner_filter(request)


    if not request.user.is_authenticated:
        if not owner.get('session_key') or not request.session.session_key:

            _ensure_session(request)
            request.session.save()
            owner = {"session_key": request.session.session_key}
            logger.debug(f"Session recreated - new session_key: {owner['session_key']}")


    logger.debug(f"Adding to cart - owner: {owner}, product: {product.pk}, variant: {variant_id}, quantity: {quantity}")


    lookup_filter = {"product": product}
    if variant_id:

        try:
            variant_obj = ProductVariant.objects.get(id=variant_id, product=product)
            lookup_filter["variant"] = variant_obj
        except ProductVariant.DoesNotExist:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'message': 'Invalid variant selected.'}, status=400)
            messages.error(request, "Invalid variant selected.")
            return redirect("product_detail", slug=product.slug)
    else:
        lookup_filter["variant"] = None


    if not request.user.is_authenticated:
        request.session.save()

        if not request.session.session_key:
            logger.error("Session key is None after save!")
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'message': 'Session error. Please refresh the page.'}, status=500)
            messages.error(request, "Session error. Please try again.")
            return redirect("product_detail", slug=product.slug)

    try:
        cart_item, created = CartItem.objects.get_or_create(defaults={"quantity": 0}, **owner, **lookup_filter)
        logger.debug(f"Cart item {'created' if created else 'retrieved'}: {cart_item.id}, owner: {owner}, quantity before: {cart_item.quantity}")
    except Exception as e:
        logger.error(f"Error creating cart item: {e}, owner: {owner}, lookup: {lookup_filter}, session_key: {request.session.session_key}")
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'message': f'Error adding to cart: {str(e)}'}, status=500)
        messages.error(request, f"Error adding to cart: {str(e)}")
        return redirect("product_detail", slug=product.slug)

    available = _available_stock(product, variant_id=variant_id)
    current = int(cart_item.quantity or 0)


    is_bundle_add = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    if is_bundle_add:


        quantity_to_add = min(quantity, available - current) if available != UNLIMITED_STOCK else quantity
        if quantity_to_add <= 0:
            if is_bundle_add:
                return JsonResponse({'success': False, 'message': 'This item is out of stock or you already have the maximum in your cart.'}, status=400)
            messages.error(request, "This item is out of stock.")
            return redirect("product_detail", slug=product.slug)
        requested_total = current + quantity_to_add
    else:

        requested_total = current + quantity

    capped_total = _cap_quantity(requested_total, available)

    if capped_total == 0:
        if is_bundle_add:
            return JsonResponse({'success': False, 'message': 'This item is out of stock.'}, status=400)
        messages.error(request, "This item is out of stock.")
        return redirect("product_detail", slug=product.slug)


    cart_item.quantity = capped_total
    cart_item.save(update_fields=["quantity"])


    actually_added = capped_total - current
    if actually_added > 0:
        product.cart_add_count = (product.cart_add_count or 0) + actually_added
        product.save(update_fields=["cart_add_count"])

        # Clear all time-slot based cache keys for popular products (last 2 slots = 10 minutes)
        current_timestamp = int(time.time())
        five_minutes = 300
        for i in range(2):
            time_slot = ((current_timestamp // five_minutes) - i) * five_minutes
            cache.delete(f'popular_products_{time_slot}')
            cache.delete(f'editors_choice_products_{time_slot}')


    if is_bundle_add:

        variant_for_pixel = lookup_filter.get("variant")
        if actually_added < quantity:
            messages.warning(
                request,
                f"Maximum {available} available. Quantity set to {capped_total}.",
            )
            payload = {
                "success": True,
                "message": f"Maximum {available} available. Quantity set to {capped_total}.",
                "warning": True,
                "cart_count": _cart_total_quantity(request),
            }
            if getattr(settings, "META_PIXEL_ID", "") and actually_added > 0:
                payload["meta_pixel"] = _meta_pixel_add_to_cart_payload(
                    product, variant_for_pixel, actually_added
                )
            if getattr(settings, "TIKTOK_PIXEL_ID", "") and actually_added > 0:
                payload["tiktok_pixel"] = _tiktok_pixel_add_to_cart_payload(
                    product, variant_for_pixel, actually_added
                )
            return JsonResponse(payload)
        messages.success(request, "✅ Added to cart.")
        payload = {
            "success": True,
            "message": "✅ Added to cart.",
            "cart_count": _cart_total_quantity(request),
        }
        if getattr(settings, "META_PIXEL_ID", "") and actually_added > 0:
            payload["meta_pixel"] = _meta_pixel_add_to_cart_payload(
                product, variant_for_pixel, actually_added
            )
        if getattr(settings, "TIKTOK_PIXEL_ID", "") and actually_added > 0:
            payload["tiktok_pixel"] = _tiktok_pixel_add_to_cart_payload(
                product, variant_for_pixel, actually_added
            )
        return JsonResponse(payload)


    bundle_items = request.POST.getlist('bundle_items')
    if bundle_items:
        bundle_added = []
        bundle_failed = []
        for bundle_item_str in bundle_items:
            try:

                parts = bundle_item_str.split(':')
                bundle_product_id = int(parts[0])
                bundle_variant_id = int(parts[1]) if len(parts) > 1 and parts[1] else None

                bundle_product = get_object_or_404(Product, pk=bundle_product_id)
                bundle_owner = _owner_filter(request)

                bundle_lookup = {"product": bundle_product}
                if bundle_variant_id:
                    try:
                        bundle_variant_obj = ProductVariant.objects.get(id=bundle_variant_id, product=bundle_product)
                        bundle_lookup["variant"] = bundle_variant_obj
                    except ProductVariant.DoesNotExist:
                        bundle_failed.append(bundle_product.name)
                        continue
                else:
                    bundle_lookup["variant"] = None

                bundle_cart_item, bundle_created = CartItem.objects.get_or_create(
                    defaults={"quantity": 0},
                    **bundle_owner,
                    **bundle_lookup
                )

                bundle_available = _available_stock(bundle_product, variant_id=bundle_variant_id)
                bundle_current = int(bundle_cart_item.quantity or 0)
                bundle_quantity_to_add = min(1, bundle_available - bundle_current) if bundle_available != UNLIMITED_STOCK else 1

                if bundle_quantity_to_add > 0:
                    bundle_cart_item.quantity = bundle_current + bundle_quantity_to_add
                    bundle_cart_item.save(update_fields=["quantity"])
                    bundle_added.append(bundle_product.name)
                else:
                    bundle_failed.append(bundle_product.name)
            except (ValueError, Product.DoesNotExist) as e:
                logger.error(f"Error adding bundle item {bundle_item_str}: {e}")
                continue

        if bundle_added:
            messages.success(request, f"✅ Added to cart: {', '.join(bundle_added)}")
        if bundle_failed:
            messages.warning(request, f"⚠️ Could not add: {', '.join(bundle_failed)}")


    if capped_total < requested_total:
        messages.warning(request, f"Maximum {available} available. Quantity set to {capped_total}.")
    else:
        messages.success(request, "✅ Added to cart.")

    return redirect("product_detail", slug=product.slug)






@require_POST
def checkout_refresh_payment_intent(request: HttpRequest) -> JsonResponse:
    """Recreate Stripe PaymentIntent when coupon, country, or shipping changes (must match server total)."""
    if not getattr(settings, "STRIPE_SECRET_KEY", ""):
        return JsonResponse({"error": "Stripe is not configured"}, status=503)
    _ensure_default_shipping_options()
    cart_items = _cart_items_for(request)
    if not cart_items.exists():
        return JsonResponse({"error": "Cart is empty"}, status=400)
    if not _checkout_payment_intent_rate_allow(request):
        return JsonResponse({"error": "Too many requests"}, status=429)

    coupon = (request.POST.get("coupon") or "").strip()
    country_raw = (request.POST.get("country") or "").strip()
    shipping_option_id = request.POST.get("shipping_option") or None
    country_full = _normalize_checkout_country(country_raw)
    if country_raw and not country_full:
        return JsonResponse({"error": "Invalid country"}, status=400)

    total, _so, _ca, coupon_err, fatal = _checkout_compute_total(
        cart_items,
        coupon_code=coupon,
        country_full=country_full,
        shipping_option_id=shipping_option_id,
        apply_coupon_usage=False,
    )
    if fatal == "coupon":
        return JsonResponse({"error": coupon_err or "Invalid coupon"}, status=400)
    if fatal == "shipping_option":
        return JsonResponse({"error": "Invalid shipping option"}, status=400)
    if total is None:
        return JsonResponse({"error": "Could not compute total"}, status=400)

    charge_currency = _normalize_checkout_charge_currency(request.POST.get("charge_currency"))
    fx_usd, fx_gbp = _checkout_fx_from_http_post(request)
    salt = f"rf|{coupon}|{shipping_option_id or ''}|{country_raw}|{total}|{charge_currency}|{fx_usd}|{fx_gbp}"
    intent = _create_stripe_intent(
        total,
        request.session.session_key,
        is_guest=not request.user.is_authenticated,
        idempotency_salt=salt,
        charge_currency=charge_currency,
        fx_usd=fx_usd,
        fx_gbp=fx_gbp,
    )
    if not intent:
        return JsonResponse({"error": "Could not create payment"}, status=500)
    return JsonResponse(
        {
            "client_secret": intent.client_secret,
            "amount_cents": intent.amount,
            "currency": intent.currency,
        }
    )


@require_http_methods(["GET", "POST"])
def checkout_view(request: HttpRequest) -> HttpResponse:
    cart_items = _cart_items_for(request)
    if not cart_items.exists():
        messages.warning(request, "Your cart is empty.")
        return redirect("cart_view")


    categories = _get_categories()
    cart_count = sum(getattr(it, "quantity", 0) for it in cart_items)
    subtotal = _compute_subtotal(cart_items)
    discount = Decimal("0.00")
    coupon_applied = None
    coupon_error = None
    shipping_cost = Decimal("0.00")
    total = None
    coupon_code = ""

    if request.method == "POST":
        if (request.POST.get("checkout_website_url") or "").strip():
            logger.warning("checkout honeypot triggered path=checkout ip=%s", _client_ip(request))
            return redirect("cart_view")
        if request.POST.get("checkout_human_confirm") != "1":
            messages.error(
                request,
                "Please confirm you are placing a real order using the checkbox above the Pay button.",
            )
            return redirect("checkout")

        full_name = (request.POST.get("full_name") or "").strip()
        address = (request.POST.get("address") or "").strip()
        city = (request.POST.get("city") or "").strip()
        postal_code = (request.POST.get("postal_code") or "").strip()
        phone = (request.POST.get("phone") or "").strip()
        country = (request.POST.get("country") or "").strip()
        shipping_option_id = request.POST.get("shipping_option")
        post_email = (request.POST.get("email") or "").strip()
        if request.user.is_authenticated:
            email = post_email or (getattr(request.user, "email", None) or "").strip()
            if not email:
                try:
                    prof = UserProfile.objects.get(user=request.user)
                    email = (prof.email or "").strip()
                except UserProfile.DoesNotExist:
                    email = ""
        else:
            email = post_email
        coupon_code = (request.POST.get("coupon") or "").strip().upper()

        if not all([full_name, address, city, postal_code, phone, country]):
            messages.error(request, "All fields are required.")
            return redirect("checkout")

        country_full = _normalize_checkout_country(country)
        if not country_full:
            messages.error(request, "Sorry, we don't ship to this country. Please select a country from the list.")
            return redirect("checkout")
        country = country_full

        if not email:
            messages.error(request, "Email is required so we can send your order confirmation.")
            return redirect("checkout")

        try:
            validate_email(email)
        except ValidationError:
            messages.error(request, "Invalid email address.")
            return redirect("checkout")

        _ensure_default_shipping_options()
        total, shipping_option, coupon_applied, coupon_error, fatal = _checkout_compute_total(
            cart_items,
            coupon_code=coupon_code,
            country_full=country,
            shipping_option_id=shipping_option_id,
            apply_coupon_usage=True,
        )
        if fatal == "coupon":
            messages.error(request, coupon_error or "Invalid promo code.")
            return redirect("checkout")
        if fatal == "shipping_option":
            messages.error(request, "Invalid shipping option.")
            return redirect("checkout")
        if total is None:
            messages.error(request, "Could not calculate order total.")
            return redirect("checkout")

        pi_id = (request.POST.get("payment_intent_id") or "").strip()
        charge_currency = _normalize_checkout_charge_currency(request.POST.get("currency"))
        fx_usd, fx_gbp = _checkout_fx_from_http_post(request)
        charged_amount, expected_minor, charge_cur = _checkout_total_eur_to_charge(
            total,
            charge_currency,
            fx_usd=fx_usd,
            fx_gbp=fx_gbp,
        )
        pi_ok, pi_err = _verify_stripe_payment_intent_for_checkout(
            pi_id, expected_minor, charge_cur
        )
        if not pi_ok:
            logger.warning("checkout payment verification failed err=%s pi=%s", pi_err, pi_id)
            messages.error(
                request,
                "Payment could not be verified for this order total. Please refresh the page, re-enter card details, and try again.",
            )
            return redirect("checkout")

        try:
            with transaction.atomic():
                _consume_cart_stock(cart_items)

                order = Order.objects.create(
                    user=request.user if request.user.is_authenticated else None,
                    email=email,
                    full_name=full_name,
                    address=address,
                    city=city,
                    postal_code=postal_code,
                    phone=phone,
                    country=country,
                    shipping_option=shipping_option,
                    total_price=charged_amount,
                    currency=charge_cur.upper(),
                )

                order_items = []
                for item in cart_items:
                    if item.quantity <= 0:
                        logger.warning(f"Skipping cart item with invalid quantity: {item.id}")
                        continue
                    order_items.append(
                        OrderItem(
                            order=order,
                            product=item.product,
                            variant=item.variant,
                            quantity=item.quantity
                        )
                    )

                if order_items:
                    OrderItem.objects.bulk_create(order_items)
                else:
                    messages.error(request, "No valid items in cart.")
                    return redirect("cart_view")

                cart_items.delete()
        except InsufficientStockError as exc:
            logger.error(
                "OUT-OF-STOCK during checkout after Stripe PI %s was confirmed. "
                "Customer %s charged but order NOT created. MANUAL REFUND REQUIRED. Details: %s",
                pi_id, email, exc,
            )
            messages.error(
                request,
                f"Sorry — «{exc.product_name}» sold out a moment ago "
                f"(only {exc.available} left in stock, you requested {exc.requested}). "
                "Please adjust your cart and try again. If you were already charged, "
                "we will refund you within 5–7 business days."
            )
            return redirect("cart_view")

        _notify_order_confirmed(request, order)
        _finalize_checkout_purchase_tracking(request, order)

        return _redirect_order_success(order)

    if not _checkout_payment_intent_rate_allow(request):
        messages.error(
            request,
            "Too many checkout attempts from your network. Please try again later or contact us.",
        )
        return redirect("cart_view")

    stripe_public_key = settings.STRIPE_PUBLISHABLE_KEY
    if not stripe_public_key or not stripe_public_key.startswith(('pk_test_', 'pk_live_')):
        logger.error(f"Invalid Stripe publishable key format: {stripe_public_key[:20] if stripe_public_key else 'empty'}... (length: {len(stripe_public_key) if stripe_public_key else 0})")
        messages.error(request, "Payment system configuration error. Please contact support.")
        return redirect("cart_view")

    # Get user profile data for pre-filling checkout form
    profile_data = {}
    if request.user.is_authenticated:
        try:
            profile = UserProfile.objects.get(user=request.user)
            profile_data = {
                "phone": profile.phone or "",
                "email": profile.email or request.user.email or "",
                "address": profile.address or "",
                "city": profile.city or "",
                "postal_code": profile.postal_code or "",
                "country": profile.country or "",
            }
        except UserProfile.DoesNotExist:
            profile_data = {
                "phone": "",
                "email": request.user.email or "",
                "address": "",
                "city": "",
                "postal_code": "",
                "country": "",
            }
    else:
        profile_data = {
            "phone": "",
            "email": "",
            "address": "",
            "city": "",
            "postal_code": "",
            "country": "",
        }

    _ensure_default_shipping_options()
    prof_country = _normalize_checkout_country((profile_data.get("country") or "").strip())
    total_pi, _, _, _, fatal_pi = _checkout_compute_total(
        cart_items,
        coupon_code="",
        country_full=prof_country,
        shipping_option_id=None,
        apply_coupon_usage=False,
    )
    if fatal_pi or total_pi is None:
        total_pi = subtotal
    init_charge_cur = _normalize_checkout_charge_currency(request.GET.get("charge_currency"))
    pi_salt = f"init|{total_pi}|{(profile_data.get('country') or '')[:40]}|{init_charge_cur}"
    intent = _create_stripe_intent(
        total_pi,
        request.session.session_key,
        is_guest=not request.user.is_authenticated,
        idempotency_salt=pi_salt,
        charge_currency=init_charge_cur,
        fx_usd=None,
        fx_gbp=None,
    )
    if not intent:
        messages.error(request, "Payment system error. Please try again.")
        return redirect("cart_view")

    initiate_checkout_pixel = None
    if getattr(settings, "META_PIXEL_ID", "") or getattr(settings, "TIKTOK_PIXEL_ID", ""):
        initiate_checkout_pixel = _initiate_checkout_pixel_payloads(cart_items, subtotal)

    return render(
        request,
        "checkout.html",
        {
            "categories": categories,
            "selected_category": None,
            "cart_count": cart_count,
            "cart_items": cart_items,
            "subtotal": subtotal,
            "discount": discount,
            "total": total,
            "shipping_cost": shipping_cost,
            "coupon_applied": coupon_applied,
            "coupon_error": coupon_error,
            "coupon_code": coupon_code,
            "shipping_options": list(ShippingOption.objects.all().order_by("price", "name").distinct()),
            "client_secret": intent.client_secret,
            "stripe_public_key": stripe_public_key,
            "stripe_checkout_currency": init_charge_cur,
            "is_guest": not request.user.is_authenticated,
            "profile_data": profile_data,
            "initiate_checkout_pixel": initiate_checkout_pixel,
        },
    )




@require_http_methods(["GET", "POST"])
def guest_checkout_view(request: HttpRequest) -> HttpResponse:
    """Guest URL kept for bookmarks; Stripe-enabled shops use the main checkout (card / wallets)."""
    _ensure_session(request)
    pk = getattr(settings, "STRIPE_PUBLISHABLE_KEY", "") or ""
    if getattr(settings, "STRIPE_SECRET_KEY", "") and pk.startswith(("pk_test_", "pk_live_")):
        messages.info(request, "Please complete your order on the secure checkout page.")
        return redirect("checkout")

    cart_qs = CartItem.objects.filter(session_key=request.session.session_key).select_related(
        "product", "variant"
    )
    cart_items = cart_qs

    if not cart_qs.exists():
        messages.warning(request, "Your cart is empty.")
        return redirect("cart_view")

    subtotal = _compute_subtotal(cart_qs)
    discount = Decimal("0.00")
    coupon_applied = None
    coupon_error = None
    shipping_cost = Decimal("0.00")
    total = None
    coupon_code = ""

    if request.method == "POST":
        if (request.POST.get("checkout_website_url") or "").strip():
            logger.warning("checkout honeypot triggered path=guest_checkout ip=%s", _client_ip(request))
            return redirect("cart_view")
        if request.POST.get("checkout_human_confirm") != "1":
            messages.error(
                request,
                "Please confirm you are placing a real order using the checkbox before completing the order.",
            )
            return redirect("guest_checkout")

        full_name = (request.POST.get("full_name") or "").strip()
        email = (request.POST.get("email") or "").strip()
        address = (request.POST.get("address") or "").strip()
        city = (request.POST.get("city") or "").strip()
        postal_code = (request.POST.get("postal_code") or "").strip()
        phone = (request.POST.get("phone") or "").strip()
        country = (request.POST.get("country") or "").strip()
        shipping_option_id = request.POST.get("shipping_option")
        coupon_code = (request.POST.get("coupon") or "").strip().upper()

        if not all([full_name, email, address, city, postal_code, phone, country]):
            messages.error(request, "All fields are required.")
            return redirect("guest_checkout")

        country_full = _normalize_checkout_country(country)
        if not country_full:
            messages.error(request, "Sorry, we don't ship to this country. Please select a country from the list.")
            return redirect("guest_checkout")
        country = country_full

        try:
            validate_email(email)
        except ValidationError:
            messages.error(request, "Invalid email address.")
            return redirect("guest_checkout")

        _ensure_default_shipping_options()
        total, shipping_option, coupon_applied, coupon_error, fatal = _checkout_compute_total(
            cart_items,
            coupon_code=coupon_code,
            country_full=country,
            shipping_option_id=shipping_option_id,
            apply_coupon_usage=True,
        )
        if fatal == "coupon":
            messages.error(request, coupon_error or "Invalid promo code.")
            return redirect("guest_checkout")
        if fatal == "shipping_option":
            messages.error(request, "Invalid shipping option.")
            return redirect("guest_checkout")
        if total is None:
            messages.error(request, "Could not calculate order total.")
            return redirect("guest_checkout")

        currency = _get_currency_from_request(request)

        try:
            with transaction.atomic():
                _consume_cart_stock(cart_qs)

                order = Order.objects.create(
                    user=None,
                    email=email,
                    full_name=full_name,
                    address=address,
                    city=city,
                    postal_code=postal_code,
                    phone=phone,
                    country=country,
                    shipping_option=shipping_option,
                    total_price=total,
                    currency=currency,
                )

                order_items = []
                for item in cart_qs:
                    if item.quantity <= 0:
                        logger.warning(f"Skipping cart item with invalid quantity: {item.id}")
                        continue
                    order_items.append(
                        OrderItem(
                            order=order,
                            product=item.product,
                            variant=item.variant,
                            quantity=item.quantity
                        )
                    )

                if order_items:
                    OrderItem.objects.bulk_create(order_items)
                else:
                    messages.error(request, "No valid items in cart.")
                    return redirect("cart_view")

                cart_qs.delete()
        except InsufficientStockError as exc:
            logger.error(
                "OUT-OF-STOCK during guest checkout for %s. Order NOT created. Details: %s",
                email, exc,
            )
            messages.error(
                request,
                f"Sorry — «{exc.product_name}» sold out a moment ago "
                f"(only {exc.available} left in stock, you requested {exc.requested}). "
                "Please adjust your cart and try again."
            )
            return redirect("cart_view")

        _notify_order_confirmed(request, order)
        _finalize_checkout_purchase_tracking(request, order)

        return _redirect_order_success(order)

    _ensure_default_shipping_options()
    initiate_checkout_pixel = None
    if getattr(settings, "META_PIXEL_ID", "") or getattr(settings, "TIKTOK_PIXEL_ID", ""):
        initiate_checkout_pixel = _initiate_checkout_pixel_payloads(cart_qs, subtotal)

    return render(
        request,
        "guest_checkout.html",
        {
            "cart_items": cart_qs,
            "subtotal": subtotal,
            "discount": discount,
            "total": total,
            "shipping_cost": shipping_cost,
            "coupon_applied": coupon_applied,
            "coupon_error": coupon_error,
            "coupon_code": coupon_code,
            "shipping_options": list(ShippingOption.objects.all().order_by("price", "name").distinct()),
            "initiate_checkout_pixel": initiate_checkout_pixel,
        },
    )






@csrf_exempt
def stripe_webhook(request: HttpRequest) -> HttpResponse:
    endpoint_secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", None)
    if not endpoint_secret:
        logger.error("STRIPE_WEBHOOK_SECRET not configured")
        return HttpResponse(status=400)

    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except ValueError as e:
        logger.error("Invalid payload: %s", str(e))
        return HttpResponse(status=400)
    except stripe_error.SignatureVerificationError as e:
        logger.error("Invalid Stripe signature: %s", str(e))
        return HttpResponse(status=400)

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        customer_email = session.get("customer_email")
        if not customer_email:
            logger.warning("No customer email in Stripe session.")
            return HttpResponse(status=200)

        user = User.objects.filter(email=customer_email).first()
        if not user:
            logger.info("Stripe session completed for unknown email %s (guest flow).", customer_email)
            return HttpResponse(status=200)







        logger.warning(
            "Order created from Stripe webhook without OrderItems for user %s. "
            "Consider storing cart data in session metadata or using line_items from session.",
            user.username
        )

        Order.objects.create(
            user=user,
            stripe_checkout_id=session["id"],
            total_price=Decimal(session["amount_total"]) / Decimal("100"),
        )
        logger.info("✅ Order created from Stripe webhook for %s", user.username)

    return HttpResponse(status=200)






def payment_success(request: HttpRequest) -> HttpResponse:
    return render(request, "payment_success.html")


def _json_order_success_redirect(order: Order) -> JsonResponse:
    ok_path = f"{reverse('order_success')}?{urlencode({'o': _order_success_query_token(order.pk)})}"
    return JsonResponse(
        {
            "success": True,
            "order_id": order.id,
            "redirect_url": ok_path,
        }
    )


def _product_quick_checkout_build_order(
    request: HttpRequest,
    *,
    product: Product,
    variant: Optional[ProductVariant],
    quantity: int,
    payer_name: str,
    payer_email: str,
    payer_phone: str,
    shipping_address: dict,
    total: Decimal,
    country: str,
    shipping_option: Optional[ShippingOption],
    currency: str,
) -> Order:
    with transaction.atomic():
        fake_line = SimpleNamespace(
            product_id=product.pk,
            product=product,
            variant_id=variant.pk if variant else None,
            variant=variant,
            quantity=quantity,
        )
        _consume_cart_stock([fake_line])

        order = Order.objects.create(
            user=request.user if request.user.is_authenticated else None,
            email=payer_email,
            full_name=payer_name,
            address=", ".join(shipping_address.get("addressLine", []))
            if shipping_address.get("addressLine")
            else "",
            city=shipping_address.get("city", ""),
            postal_code=shipping_address.get("postalCode", ""),
            phone=payer_phone,
            country=country,
            shipping_option=shipping_option,
            total_price=total,
            currency=currency,
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            variant=variant,
            quantity=quantity,
        )
        owner = _owner_filter(request)
        CartItem.objects.filter(**owner, product=product, variant=variant).delete()
    return order


_PI_ORDER_CACHE_PREFIX = "marbaras_order_pi:"
_PI_FINALIZE_LOCK_PREFIX = "marbaras_pi_finalize_lock:"


def _cached_order_response_for_pi(payment_intent_id: str) -> Optional[JsonResponse]:
    oid = cache.get(f"{_PI_ORDER_CACHE_PREFIX}{payment_intent_id}")
    if not oid:
        return None
    order = Order.objects.filter(pk=oid).first()
    if not order:
        return None
    return _json_order_success_redirect(order)


def _remember_pi_order_mapping(payment_intent_id: str, order_id: int) -> None:
    cache.set(f"{_PI_ORDER_CACHE_PREFIX}{payment_intent_id}", order_id, 86400 * 30)


def _wallet_shipping_address_to_order_fields(shipping_address: dict) -> tuple[str, str, str]:
    raw_lines = shipping_address.get("addressLine")
    if isinstance(raw_lines, list) and raw_lines:
        address = ", ".join(str(x).strip() for x in raw_lines if (x or "").strip())
    else:
        a1 = (shipping_address.get("line1") or "").strip()
        a2 = (shipping_address.get("line2") or "").strip()
        address = ", ".join(x for x in (a1, a2) if x)
    city = (shipping_address.get("city") or "").strip()
    postal = (
        shipping_address.get("postalCode") or shipping_address.get("postal_code") or ""
    ).strip()
    return address, city, postal


def _wallet_pr_shipping_id_to_db_option_id(
    country_full: str, wallet_sid: str
) -> Optional[str]:
    """Map Payment Request shipping option id (free|express|standard) to ShippingOption PK."""
    _ensure_default_shipping_options()
    w = (wallet_sid or "").strip().lower() or "free"
    ca_like = country_full in ("Canada", "Australia", "New Zealand", "Norway")
    so: Optional[ShippingOption] = None
    if ca_like:
        if w == "express":
            so = ShippingOption.objects.filter(price=Decimal("19.99")).first()
        else:
            so = ShippingOption.objects.filter(price=Decimal("5.99")).first()
    else:
        if w == "standard":
            w = "free"
        if w == "express":
            so = ShippingOption.objects.filter(price=Decimal("19.99")).first()
        else:
            so = ShippingOption.objects.filter(price=Decimal("0.00")).first()
    return str(so.pk) if so else None


def _cart_checkout_build_order_from_wallet(
    request: HttpRequest,
    *,
    cart_items,
    payer_name: str,
    payer_email: str,
    payer_phone: str,
    shipping_address: dict,
    total: Decimal,
    country: str,
    shipping_option: Optional[ShippingOption],
    currency: str,
) -> Order:
    address, city, postal = _wallet_shipping_address_to_order_fields(shipping_address)
    with transaction.atomic():
        _consume_cart_stock(cart_items)

        order = Order.objects.create(
            user=request.user if request.user.is_authenticated else None,
            email=payer_email,
            full_name=payer_name,
            address=address,
            city=city,
            postal_code=postal,
            phone=payer_phone,
            country=country,
            shipping_option=shipping_option,
            total_price=total,
            currency=currency,
        )
        bulk = []
        for item in cart_items:
            if item.quantity <= 0:
                logger.warning("Skipping cart item with invalid quantity: %s", item.id)
                continue
            bulk.append(
                OrderItem(
                    order=order,
                    product=item.product,
                    variant=item.variant,
                    quantity=item.quantity,
                )
            )
        if not bulk:
            raise ValueError("No valid cart line items")
        OrderItem.objects.bulk_create(bulk)
        cart_items.delete()
    return order


@require_http_methods(["POST"])
def create_order_from_cart_wallet(request: HttpRequest) -> HttpResponse:
    """Pay full cart via Apple Pay / Google Pay without visiting checkout."""
    import json

    try:
        data = json.loads(request.body)
        _ensure_session(request)
        if not _checkout_payment_intent_rate_allow(request):
            return JsonResponse(
                {
                    "success": False,
                    "error": "Too many payment attempts. Please try again later.",
                },
                status=429,
            )
        cart_items = _cart_items_for(request)
        if not cart_items.exists():
            return JsonResponse({"success": False, "error": "Cart is empty"}, status=400)

        payment_method_id = (data.get("payment_method_id") or "").strip()
        completed_pi_id = (data.get("completed_payment_intent_id") or "").strip()
        payer_name = (data.get("payer_name") or "").strip()
        payer_email = (data.get("payer_email") or "").strip()
        payer_phone = (data.get("payer_phone") or "").strip()
        shipping_address = data.get("shipping_address") or {}
        wallet_sid = (data.get("wallet_shipping_option_id") or "").strip().lower()
        coupon_code = (data.get("coupon_code") or "").strip()

        if completed_pi_id:
            if not all([payer_name, payer_email, payer_phone]):
                return JsonResponse(
                    {"success": False, "error": "Missing required fields"},
                    status=400,
                )
        else:
            if not all([payment_method_id, payer_name, payer_email, payer_phone]):
                return JsonResponse(
                    {"success": False, "error": "Missing required fields"},
                    status=400,
                )
        try:
            validate_email(payer_email)
        except ValidationError:
            return JsonResponse(
                {"success": False, "error": "Invalid email address"},
                status=400,
            )

        country_raw = (shipping_address.get("country") or "").strip()
        country = _normalize_checkout_country(country_raw)
        if not country:
            return JsonResponse(
                {
                    "success": False,
                    "error": "We do not ship to this country or the address is incomplete.",
                },
                status=400,
            )

        shipping_option_id_str = _wallet_pr_shipping_id_to_db_option_id(country, wallet_sid)
        if not shipping_option_id_str:
            return JsonResponse(
                {"success": False, "error": "Could not resolve shipping option"},
                status=400,
            )

        total, shipping_option, _ca, coupon_error, fatal = _checkout_compute_total(
            cart_items,
            coupon_code=coupon_code,
            country_full=country,
            shipping_option_id=shipping_option_id_str,
            apply_coupon_usage=True,
        )
        if fatal == "coupon":
            return JsonResponse(
                {"success": False, "error": coupon_error or "Invalid coupon"},
                status=400,
            )
        if fatal == "shipping_option":
            return JsonResponse(
                {"success": False, "error": "Invalid shipping option"},
                status=400,
            )
        if total is None:
            return JsonResponse(
                {"success": False, "error": "Could not calculate order total"},
                status=400,
            )

        charge_currency = _normalize_checkout_charge_currency(
            data.get("charge_currency") or data.get("currency")
        )
        fx_usd, fx_gbp = _checkout_fx_from_wallet_json(data)
        charged_amount, expected_minor, want_cur = _checkout_total_eur_to_charge(
            total,
            charge_currency,
            fx_usd=fx_usd,
            fx_gbp=fx_gbp,
        )

        cached = _cached_order_response_for_pi(completed_pi_id) if completed_pi_id else None
        if cached:
            return cached

        if completed_pi_id:
            lock_key = f"{_PI_FINALIZE_LOCK_PREFIX}{completed_pi_id}"
            if not cache.add(lock_key, 1, timeout=120):
                for _ in range(60):
                    time.sleep(0.05)
                    again = _cached_order_response_for_pi(completed_pi_id)
                    if again:
                        return again
                return JsonResponse(
                    {
                        "success": False,
                        "error": "Order is being created. Please wait a moment and try again.",
                    },
                    status=409,
                )
            try:
                try:
                    pi_done = stripe.PaymentIntent.retrieve(completed_pi_id)
                except stripe_error.StripeError as e:
                    return JsonResponse(
                        {"success": False, "error": str(e)},
                        status=400,
                    )
                if (pi_done.currency or "").lower() != want_cur:
                    return JsonResponse(
                        {"success": False, "error": "Currency mismatch"},
                        status=400,
                    )
                if pi_done.status != "succeeded":
                    return JsonResponse(
                        {
                            "success": False,
                            "error": f"Payment not completed ({pi_done.status})",
                        },
                        status=400,
                    )
                if int(pi_done.amount or 0) != expected_minor:
                    return JsonResponse(
                        {"success": False, "error": "Payment amount does not match order"},
                        status=400,
                    )

                currency = (
                    getattr(pi_done, "currency", "").upper()
                    or want_cur.upper()
                    or data.get("currency", "").upper()
                    or _get_currency_from_request(request)
                )
                order = _cart_checkout_build_order_from_wallet(
                    request,
                    cart_items=cart_items,
                    payer_name=payer_name,
                    payer_email=payer_email,
                    payer_phone=payer_phone,
                    shipping_address=shipping_address,
                    total=charged_amount,
                    country=country,
                    shipping_option=shipping_option,
                    currency=currency,
                )
                _remember_pi_order_mapping(completed_pi_id, order.pk)
                _notify_order_confirmed(request, order)
                _finalize_checkout_purchase_tracking(request, order)
                return _json_order_success_redirect(order)
            finally:
                cache.delete(lock_key)

        try:
            intent_params = {
                "amount": expected_minor,
                "currency": want_cur,
                "payment_method": payment_method_id,
                "automatic_payment_methods": {
                    "enabled": True,
                    "allow_redirects": "never",
                },
                "confirm": True,
            }
            if payer_email:
                intent_params["receipt_email"] = payer_email

            session_hash = (
                hashlib.md5(request.session.session_key.encode("utf-8")).hexdigest()
                if request.session.session_key
                else "nouser"
            )
            item_sigs = ",".join(
                f"{it.product_id}:{it.variant_id or 0}:{it.quantity}"
                for it in cart_items.order_by("pk")
            )
            cart_key = hashlib.sha256(item_sigs.encode("utf-8")).hexdigest()[:20]
            pm_key = hashlib.sha256(
                (payment_method_id or "").encode("utf-8")
            ).hexdigest()[:24]
            idempotency_key = f"pi-cart-{session_hash}-{cart_key}-{expected_minor}-{pm_key}"

            intent = stripe.PaymentIntent.create(
                **intent_params,
                idempotency_key=idempotency_key,
            )

            if intent.status == "requires_action":
                return JsonResponse(
                    {
                        "success": False,
                        "requires_action": True,
                        "client_secret": intent.client_secret,
                    }
                )

            if intent.status != "succeeded":
                return JsonResponse(
                    {
                        "success": False,
                        "error": f"Payment status: {intent.status}",
                    },
                    status=400,
                )

        except stripe_error.StripeError as e:
            logger.error("Stripe cart wallet error: %s", e)
            return JsonResponse({"success": False, "error": str(e)}, status=400)

        dup = _cached_order_response_for_pi(intent.id)
        if dup:
            return dup

        currency = (
            getattr(intent, "currency", "").upper()
            or want_cur.upper()
            or data.get("currency", "").upper()
            or _get_currency_from_request(request)
        )
        order = _cart_checkout_build_order_from_wallet(
            request,
            cart_items=cart_items,
            payer_name=payer_name,
            payer_email=payer_email,
            payer_phone=payer_phone,
            shipping_address=shipping_address,
            total=charged_amount,
            country=country,
            shipping_option=shipping_option,
            currency=currency,
        )
        _remember_pi_order_mapping(intent.id, order.pk)
        _notify_order_confirmed(request, order)
        _finalize_checkout_purchase_tracking(request, order)
        return _json_order_success_redirect(order)

    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)
    except Exception as e:
        logger.error("create_order_from_cart_wallet: %s", e, exc_info=True)
        return JsonResponse({"success": False, "error": str(e)}, status=500)


@require_http_methods(["POST"])
def create_order_from_product(request: HttpRequest) -> HttpResponse:
    """Create order directly from product detail page with Apple Pay / Google Pay."""
    import json

    try:
        data = json.loads(request.body)
        _ensure_session(request)
        if not _checkout_payment_intent_rate_allow(request):
            return JsonResponse(
                {
                    "success": False,
                    "error": "Too many payment attempts. Please try again later.",
                },
                status=429,
            )
        product_id = data.get("product_id")
        variant_id = data.get("variant_id")
        try:
            quantity = max(1, int(data.get("quantity", 1)))
        except (TypeError, ValueError):
            quantity = 1
        payment_method_id = (data.get("payment_method_id") or "").strip()
        completed_pi_id = (data.get("completed_payment_intent_id") or "").strip()

        payer_name = data.get("payer_name", "").strip()
        payer_email = data.get("payer_email", "").strip()
        payer_phone = data.get("payer_phone", "").strip()
        shipping_address = data.get("shipping_address") or {}

        if completed_pi_id:
            if not all([product_id, payer_name, payer_email, payer_phone]):
                return JsonResponse(
                    {"success": False, "error": "Missing required fields"},
                    status=400,
                )
        else:
            if not all(
                [product_id, payment_method_id, payer_name, payer_email, payer_phone]
            ):
                return JsonResponse(
                    {"success": False, "error": "Missing required fields"},
                    status=400,
                )
        try:
            validate_email(payer_email)
        except ValidationError:
            return JsonResponse(
                {"success": False, "error": "Invalid email address"},
                status=400,
            )

        product = get_object_or_404(Product, pk=product_id)

        variant = None
        if variant_id:
            try:
                variant = ProductVariant.objects.get(id=variant_id, product=product)
            except ProductVariant.DoesNotExist:
                return JsonResponse({"success": False, "error": "Invalid variant"}, status=400)

        available = _available_stock(product, variant_id=variant_id if variant else None)
        if available != UNLIMITED_STOCK and quantity > available:
            return JsonResponse(
                {"success": False, "error": f"Only {available} available"},
                status=400,
            )

        product_price = product.get_discounted_price()
        subtotal = product_price * quantity

        country_raw = (shipping_address.get("country") or "").strip()
        country = _normalize_checkout_country(country_raw)
        if not country:
            return JsonResponse(
                {
                    "success": False,
                    "error": "We do not ship to this country or the address is incomplete.",
                },
                status=400,
            )

        _ensure_default_shipping_options()

        shipping_cost = Decimal("0.00")
        shipping_option = None
        if country in ["Canada", "Australia", "New Zealand", "Norway"]:
            standard_shipping = ShippingOption.objects.filter(
                price=Decimal("5.99")
            ).first()
            if standard_shipping:
                shipping_option = standard_shipping
                shipping_cost = standard_shipping.price
            else:
                shipping_cost = Decimal("5.99")

        total = (subtotal + shipping_cost).quantize(Decimal("0.01"))
        charge_currency = _normalize_checkout_charge_currency(
            data.get("charge_currency") or data.get("currency")
        )
        fx_usd, fx_gbp = _checkout_fx_from_wallet_json(data)
        charged_amount, expected_minor, want_cur = _checkout_total_eur_to_charge(
            total,
            charge_currency,
            fx_usd=fx_usd,
            fx_gbp=fx_gbp,
        )

        cached = _cached_order_response_for_pi(completed_pi_id) if completed_pi_id else None
        if cached:
            return cached

        if completed_pi_id:
            lock_key = f"{_PI_FINALIZE_LOCK_PREFIX}{completed_pi_id}"
            if not cache.add(lock_key, 1, timeout=120):
                for _ in range(60):
                    time.sleep(0.05)
                    again = _cached_order_response_for_pi(completed_pi_id)
                    if again:
                        return again
                return JsonResponse(
                    {
                        "success": False,
                        "error": "Order is being created. Please wait a moment and try again.",
                    },
                    status=409,
                )
            try:
                try:
                    pi_done = stripe.PaymentIntent.retrieve(completed_pi_id)
                except stripe_error.StripeError as e:
                    return JsonResponse(
                        {"success": False, "error": str(e)},
                        status=400,
                    )
                if (pi_done.currency or "").lower() != want_cur:
                    return JsonResponse(
                        {"success": False, "error": "Currency mismatch"},
                        status=400,
                    )
                if pi_done.status != "succeeded":
                    return JsonResponse(
                        {
                            "success": False,
                            "error": f"Payment not completed ({pi_done.status})",
                        },
                        status=400,
                    )
                if int(pi_done.amount or 0) != expected_minor:
                    return JsonResponse(
                        {"success": False, "error": "Payment amount does not match order"},
                        status=400,
                    )

                currency = (
                    getattr(pi_done, "currency", "").upper()
                    or want_cur.upper()
                    or data.get("currency", "").upper()
                    or _get_currency_from_request(request)
                )
                order = _product_quick_checkout_build_order(
                    request,
                    product=product,
                    variant=variant,
                    quantity=quantity,
                    payer_name=payer_name,
                    payer_email=payer_email,
                    payer_phone=payer_phone,
                    shipping_address=shipping_address,
                    total=charged_amount,
                    country=country,
                    shipping_option=shipping_option,
                    currency=currency,
                )
                _remember_pi_order_mapping(completed_pi_id, order.pk)
                _notify_order_confirmed(request, order)
                _finalize_checkout_purchase_tracking(request, order)
                return _json_order_success_redirect(order)
            finally:
                cache.delete(lock_key)

        try:
            intent_params = {
                "amount": expected_minor,
                "currency": want_cur,
                "payment_method": payment_method_id,
                "automatic_payment_methods": {
                    "enabled": True,
                    "allow_redirects": "never",
                },
                "confirm": True,
            }
            if payer_email:
                intent_params["receipt_email"] = payer_email

            session_hash = (
                hashlib.md5(request.session.session_key.encode("utf-8")).hexdigest()
                if request.session.session_key
                else "nouser"
            )
            vid = int(variant_id) if variant_id else 0
            pm_key = hashlib.sha256(
                (payment_method_id or "").encode("utf-8")
            ).hexdigest()[:24]
            idempotency_key = (
                f"pi-prod-{product_id}-{vid}-{session_hash}-{expected_minor}-{pm_key}"
            )

            intent = stripe.PaymentIntent.create(
                **intent_params,
                idempotency_key=idempotency_key,
            )

            if intent.status == "requires_action":
                return JsonResponse(
                    {
                        "success": False,
                        "requires_action": True,
                        "client_secret": intent.client_secret,
                    }
                )

            if intent.status != "succeeded":
                return JsonResponse(
                    {
                        "success": False,
                        "error": f"Payment status: {intent.status}",
                    },
                    status=400,
                )

        except stripe_error.StripeError as e:
            logger.error("Stripe payment error: %s", e)
            return JsonResponse({"success": False, "error": str(e)}, status=400)

        dup = _cached_order_response_for_pi(intent.id)
        if dup:
            return dup

        currency = (
            getattr(intent, "currency", "").upper()
            or want_cur.upper()
            or data.get("currency", "").upper()
            or _get_currency_from_request(request)
        )
        order = _product_quick_checkout_build_order(
            request,
            product=product,
            variant=variant,
            quantity=quantity,
            payer_name=payer_name,
            payer_email=payer_email,
            payer_phone=payer_phone,
            shipping_address=shipping_address,
            total=charged_amount,
            country=country,
            shipping_option=shipping_option,
            currency=currency,
        )
        _remember_pi_order_mapping(intent.id, order.pk)
        _notify_order_confirmed(request, order)
        _finalize_checkout_purchase_tracking(request, order)
        return _json_order_success_redirect(order)

    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)
    except Exception as e:
        logger.error("Error creating order from product: %s", e, exc_info=True)
        return JsonResponse({"success": False, "error": str(e)}, status=500)


def notify(request: HttpRequest, level: int, msg: str) -> HttpResponse:
    messages.add_message(request, level, msg)
    return redirect("home")


def sitemap_xml(request: HttpRequest) -> HttpResponse:
    """Generate sitemap.xml for search engines."""
    from django.urls import NoReverseMatch, reverse
    from django.utils import timezone
    from datetime import timedelta

    def _reverse_or_skip(viewname: str, *, kwargs: dict | None = None) -> str | None:
        try:
            return reverse(viewname, kwargs=kwargs or {})
        except NoReverseMatch:
            logger.warning(
                "sitemap: skip %s kwargs=%s (invalid or missing slug)",
                viewname,
                kwargs,
            )
            return None
    
    products = Product.objects.filter(stock__gt=0).order_by('-id')
    categories = Category.objects.all()
    blog_posts = BlogPost.objects.filter(published=True) if hasattr(BlogPost, 'published') else BlogPost.objects.all()
    
    base_url = f"{request.scheme}://{request.get_host()}"
    lastmod = timezone.now().strftime('%Y-%m-%d')
    
    xml = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    
    # Homepage
    xml.append(f'  <url><loc>{base_url}/</loc><lastmod>{lastmod}</lastmod><changefreq>daily</changefreq><priority>1.0</priority></url>')
    
    # Products
    for product in products:
        path = _reverse_or_skip("product_detail", kwargs={"slug": product.slug})
        if not path:
            continue
        url = f"{base_url}{path}"
        # Use current date if product doesn't have created_at field
        product_date = lastmod
        if hasattr(product, 'created_at') and product.created_at:
            product_date = product.created_at.strftime("%Y-%m-%d")
        xml.append(f'  <url><loc>{url}</loc><lastmod>{product_date}</lastmod><changefreq>weekly</changefreq><priority>0.8</priority></url>')
    
    # Categories
    for category in categories:
        path = _reverse_or_skip("products_by_category", kwargs={"slug": category.slug})
        if not path:
            continue
        url = f"{base_url}{path}"
        xml.append(f'  <url><loc>{url}</loc><lastmod>{lastmod}</lastmod><changefreq>weekly</changefreq><priority>0.7</priority></url>')
    
    # Blog posts
    for post in blog_posts:
        path = _reverse_or_skip("blog_detail", kwargs={"slug": post.slug})
        if not path:
            continue
        url = f"{base_url}{path}"
        post_date = lastmod
        if hasattr(post, 'created_at') and post.created_at:
            post_date = post.created_at.strftime("%Y-%m-%d")
        xml.append(
            f'  <url><loc>{url}</loc><lastmod>{post_date}</lastmod><changefreq>monthly</changefreq><priority>0.6</priority></url>'
        )
    
    # Static pages
    static_pages = [
        ('products', 0.8),
        ('cart_view', 0.5),
        ('terms', 0.4),
        ('privacy', 0.4),
        ('refund_returns', 0.4),
        ('shipping_policy', 0.4),
        ('cookie_policy', 0.4),
        ('contact', 0.5),
    ]
    
    for page_name, priority in static_pages:
        try:
            url = f"{base_url}{reverse(page_name)}"
            xml.append(f'  <url><loc>{url}</loc><lastmod>{lastmod}</lastmod><changefreq>monthly</changefreq><priority>{priority}</priority></url>')
        except:
            pass
    
    xml.append('</urlset>')
    
    response = HttpResponse('\n'.join(xml), content_type='application/xml')
    return response


@csrf_exempt
@require_http_methods(["POST"])
def subscribe_email(request: HttpRequest) -> JsonResponse:
    """Handle email subscription from popup and send welcome email with promo code from existing coupons."""
    from django.utils import timezone
    from ecommerce.models import Coupon, EmailSubscription
    from ecommerce.utils.emailing import send_welcome_email_with_promo
    
    email = request.POST.get('email', '').strip().lower()
    
    if not email:
        return JsonResponse({'success': False, 'message': 'Email is required'}, status=400)
    
    # Validate email format
    from django.core.validators import validate_email
    from django.core.exceptions import ValidationError
    try:
        validate_email(email)
    except ValidationError:
        return JsonResponse({'success': False, 'message': 'Invalid email address'}, status=400)
    
    # Check if email already subscribed
    existing_subscription = EmailSubscription.objects.filter(email=email).first()
    if existing_subscription:
        # Email already subscribed - return the coupon code they already received
        coupon = existing_subscription.coupon
        if coupon:
            discount_display = ""
            if coupon.percent_off:
                discount_display = f"{coupon.percent_off}% OFF"
            elif coupon.amount_off:
                discount_display = f"${coupon.amount_off} OFF"
            
            return JsonResponse({
                'success': False, 
                'message': f'You have already subscribed! Check your email for your discount code ({coupon.code}).'
            }, status=400)
        else:
            return JsonResponse({
                'success': False, 
                'message': 'You have already subscribed with this email address.'
            }, status=400)
    
    # Find an available coupon (active, not expired, not fully used)
    # IMPORTANT: Use coupons from database, NOT hardcoded "WELCOME5"
    # Use ANY available coupon regardless of discount percentage
    import logging
    logger = logging.getLogger(__name__)
    now = timezone.now()
    
    # Find any available coupon (EXCLUDE "WELCOME5" - it's a legacy code)
    # PREFER 5% discount coupons for email subscriptions
    # Prefer coupons that haven't been used yet (used_count = 0)
    
    # First, let's check all coupons for debugging
    all_coupons = Coupon.objects.exclude(code__iexact="WELCOME5")
    logger.info(f"Total coupons in DB (excluding WELCOME5): {all_coupons.count()}")
    
    # Check each filter condition separately
    active_coupons = all_coupons.filter(active=True)
    logger.info(f"Active coupons: {active_coupons.count()}")
    
    valid_date_coupons = active_coupons.filter(
        Q(starts_at__isnull=True) | Q(starts_at__lte=now),
        Q(ends_at__isnull=True) | Q(ends_at__gte=now)
    )
    logger.info(f"Coupons with valid dates: {valid_date_coupons.count()}")
    
    # FIRST: Try to find 5% discount coupons (preferred for email subscriptions)
    # Exclude coupons that are already assigned to other email subscriptions
    already_assigned_coupon_ids = EmailSubscription.objects.exclude(
        coupon__isnull=True
    ).values_list('coupon_id', flat=True)
    
    available_5_percent = valid_date_coupons.filter(
        percent_off=5.00
    ).exclude(
        id__in=already_assigned_coupon_ids  # Exclude already assigned coupons
    ).filter(
        Q(usage_limit__isnull=True) | Q(used_count__lt=F('usage_limit'))
    ).order_by('used_count', '-id')
    
    logger.info(f"Available 5% coupons (not assigned): {available_5_percent.count()}")
    
    coupon = available_5_percent.first()
    
    # If no 5% coupon found, try any available coupon (but still exclude assigned ones)
    if not coupon:
        available_coupons = valid_date_coupons.exclude(
            id__in=already_assigned_coupon_ids  # Exclude already assigned coupons
        ).filter(
            Q(usage_limit__isnull=True) | Q(used_count__lt=F('usage_limit'))
        ).order_by('used_count', '-id')
        logger.info(f"Coupons with available usage (any %, not assigned): {available_coupons.count()}")
        coupon = available_coupons.first()
    
    # Log details of first 10 coupons for debugging
    for c in all_coupons[:10]:
        discount = f"{c.percent_off}%" if c.percent_off else f"${c.amount_off}"
        is_active = c.active
        is_valid_date = (not c.starts_at or c.starts_at <= now) and (not c.ends_at or c.ends_at >= now)
        is_available = not c.usage_limit or c.used_count < c.usage_limit
        is_assigned = c.id in already_assigned_coupon_ids
        logger.info(f"  - Coupon: {c.code} (ID: {c.id}, discount: {discount}, active: {is_active}, valid_date: {is_valid_date}, available: {is_available}, assigned: {is_assigned}, used: {c.used_count}/{c.usage_limit or 'unlimited'}, starts: {c.starts_at}, ends: {c.ends_at})")
    
    if coupon:
        logger.info(f"✅ Selected coupon: {coupon.code} (ID: {coupon.id}, discount: {coupon.percent_off}%)")
    else:
        logger.warning(f"⚠️ No coupon selected yet, will try expired coupons...")
    
    if not coupon:
        logger.warning(f"⚠️ No coupons with valid dates found. Trying to auto-update expired coupons...")
        
        # Try to find active coupons that are expired but not used
        expired_but_unused = active_coupons.filter(
            Q(ends_at__lt=now) | Q(ends_at__isnull=True),
            Q(usage_limit__isnull=True) | Q(used_count__lt=F('usage_limit'))
        ).exclude(code__iexact="WELCOME5")
        
        if expired_but_unused.exists():
            # Auto-update dates for expired but unused coupons
            new_ends_at = now + timezone.timedelta(days=365)
            updated_count = expired_but_unused.update(
                starts_at=now,
                ends_at=new_ends_at
            )
            logger.info(f"✅ Auto-updated {updated_count} expired coupons with new dates")
            
            # Now try to find available coupons again
            all_available = Coupon.objects.filter(
                active=True
            ).exclude(
                code__iexact="WELCOME5"
            ).filter(
                Q(starts_at__isnull=True) | Q(starts_at__lte=now),
                Q(ends_at__isnull=True) | Q(ends_at__gte=now)
            ).filter(
                Q(usage_limit__isnull=True) | Q(used_count__lt=F('usage_limit'))
            ).order_by('used_count', '-id')
            
            coupon = all_available.first()
            
            if coupon:
                logger.info(f"✅ Found coupon after auto-update: {coupon.code}")
            else:
                logger.error(f"❌ Still no available coupon after auto-update")
                return JsonResponse({
                    'success': False, 
                    'message': 'Sorry, no promo codes available at the moment. Please try again later.'
                }, status=404)
        else:
            # Try to find any coupon without usage limit as fallback
            fallback_coupons = Coupon.objects.filter(
                active=True,
                usage_limit__isnull=True
            ).exclude(
                code__iexact="WELCOME5"
            ).order_by('-id')
            
            if fallback_coupons.exists():
                coupon = fallback_coupons.first()
                # Update dates for fallback coupon
                coupon.starts_at = now
                coupon.ends_at = now + timezone.timedelta(days=365)
                coupon.save(update_fields=['starts_at', 'ends_at'])
                logger.info(f"✅ Using fallback coupon (no usage limit) and updated dates: {coupon.code}")
            else:
                logger.error(f"❌ No available coupon found for email subscription: {email}")
                logger.error(f"   Active coupons: {active_coupons.count()}")
                logger.error(f"   Valid date coupons: {valid_date_coupons.count()}")
                logger.error(f"   Available usage coupons: {available_coupons.count()}")
                logger.error(f"   Current time: {now}")
                return JsonResponse({
                    'success': False, 
                    'message': 'Sorry, no promo codes available at the moment. Please try again later.'
                }, status=404)
    
    logger.info(f"Selected coupon for {email}: {coupon.code} (ID: {coupon.id})")
    
    # Create email subscription record
    try:
        subscription = EmailSubscription.objects.create(
            email=email,
            coupon=coupon
        )
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error creating email subscription: {e}")
        return JsonResponse({'success': False, 'message': 'Error processing subscription'}, status=500)
    
    # Create a temporary user object for email sending
    class EmailUser:
        def __init__(self, email):
            self.email = email
            self.username = email.split('@')[0]
    
    email_user = EmailUser(email)
    base_url = request.build_absolute_uri('/').rstrip('/')
    
    # Get discount percentage or amount for display
    discount_display = ""
    if coupon.percent_off:
        discount_display = f"{coupon.percent_off}% OFF"
    elif coupon.amount_off:
        discount_display = f"${coupon.amount_off} OFF"
    
    # Send welcome email with promo code
    # IMPORTANT: Use coupon.code from database, not hardcoded "WELCOME5"
    promo_code_to_send = coupon.code
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"=== SENDING WELCOME EMAIL ===")
    logger.info(f"Email: {email}")
    logger.info(f"Coupon ID: {coupon.id}")
    logger.info(f"Coupon Code from DB: '{coupon.code}'")
    logger.info(f"Coupon Code Type: {type(coupon.code)}")
    logger.info(f"Promo code to send: '{promo_code_to_send}'")
    logger.info(f"Discount display: '{discount_display}'")
    logger.info(f"Base URL: {base_url}")
    
    # Double-check we're using the right code
    if promo_code_to_send == "WELCOME5":
        logger.warning(f"⚠️ WARNING: Using 'WELCOME5' - this might be from database!")
        logger.warning(f"   Coupon object: {coupon}")
        logger.warning(f"   Coupon.__dict__: {coupon.__dict__}")
    
    try:
        result = send_welcome_email_with_promo(email_user, base_url, promo_code_to_send, discount_display)
        if result:
            logger.info(f"Successfully sent welcome email to {email} with code: {promo_code_to_send}")
            return JsonResponse({
                'success': True, 
                'message': f'Check your email! Your discount code ({promo_code_to_send}) has been sent.'
            })
        else:
            # Email failed but subscription was created - still return success with the code
            logger.warning(f"Email failed for {email} but subscription was created with coupon {promo_code_to_send}")
            return JsonResponse({
                'success': True, 
                'message': f'Your promo code is: {promo_code_to_send}. Use it at checkout for {discount_display}!'
            })
    except Exception as e:
        logger.error(f"Error sending welcome email to {email}: {e}")
        logger.exception("Full error traceback:")
        # Still return success with the code since subscription was created
        return JsonResponse({
            'success': True, 
            'message': f'Your promo code is: {promo_code_to_send}. Use it at checkout for {discount_display}!'
        })


@csrf_exempt
@require_http_methods(["POST"])
def validate_coupon(request: HttpRequest) -> JsonResponse:
    """AJAX endpoint to validate coupon code and return discount info without applying usage."""
    from decimal import Decimal
    
    coupon_code = (request.POST.get('coupon_code', '') or '').strip().upper()
    subtotal_str = request.POST.get('subtotal', '0')
    
    try:
        subtotal = Decimal(str(subtotal_str))
    except (ValueError, TypeError):
        return JsonResponse({
            'success': False,
            'error': 'Invalid subtotal'
        }, status=400)
    
    if not coupon_code:
        return JsonResponse({
            'success': False,
            'error': 'Coupon code is required'
        }, status=400)
    
    # Get cart items to check for Sale category products
    cart_items = _cart_items_for(request)
    
    # Process coupon without applying usage (just for preview)
    # IMPORTANT: Pass cart_items to check for Sale category products
    new_subtotal, discount, coupon_applied, coupon_error = _process_coupon(coupon_code, subtotal, apply_usage=False, cart_items=cart_items)
    
    if coupon_error:
        return JsonResponse({
            'success': False,
            'error': coupon_error
        }, status=400)
    
    if coupon_applied:
        coupon = Coupon.objects.filter(code=coupon_applied).first()
        discount_display = ""
        if coupon and coupon.percent_off:
            discount_display = f"{coupon.percent_off}% OFF"
        elif coupon and coupon.amount_off:
            discount_display = f"${coupon.amount_off} OFF"
        
        return JsonResponse({
            'success': True,
            'coupon_code': coupon_applied,
            'discount': str(discount),
            'new_subtotal': str(new_subtotal),
            'discount_display': discount_display
        })
    
    return JsonResponse({
        'success': False,
        'error': 'Invalid coupon code'
    }, status=400)


def robots_txt(request: HttpRequest) -> HttpResponse:
    """Generate robots.txt file."""
    base_url = f"{request.scheme}://{request.get_host()}"
    content = f"""User-agent: *
Allow: /
Disallow: /admin/
Disallow: /accounts/
Disallow: /checkout/
Disallow: /cart/
Disallow: /webhook/
Disallow: /test-emails/
Disallow: /health/

Sitemap: {base_url}/sitemap.xml
"""
    return HttpResponse(content, content_type='text/plain')


def order_success(request: HttpRequest) -> HttpResponse:
    purchase_pixel = request.session.pop("marbaras_purchase_pixel", None)
    if purchase_pixel is None:
        oid = _order_id_from_success_token(request)
        if oid:
            order = (
                Order.objects.filter(pk=oid)
                .prefetch_related(
                    Prefetch(
                        "items",
                        queryset=OrderItem.objects.select_related("product", "variant"),
                    )
                )
                .first()
            )
            if not order:
                logger.warning(
                    "order_success: token decoded to oid=%s but no Order row (wrong DB?)",
                    oid,
                )
            else:
                try:
                    purchase_pixel = _purchase_pixel_payload_from_order(order)
                    if purchase_pixel is None:
                        logger.warning(
                            "order_success: order #%s has no billable line items for pixel payload",
                            oid,
                        )
                except Exception:
                    logger.warning(
                        "order_success purchase_pixel fallback failed oid=%s",
                        oid,
                        exc_info=True,
                    )
                    purchase_pixel = None
        elif (request.GET.get("o") or "").strip():
            logger.warning(
                "order_success: query has o= but token did not decode to an order id",
            )
    if purchase_pixel is None:
        logger.warning(
            "order_success: no purchase_pixel (browser Purchase will not fire) path=%s has_o=%s",
            request.path,
            bool((request.GET.get("o") or "").strip()),
        )
    return render(
        request,
        "order_success.html",
        {"purchase_pixel": purchase_pixel},
    )


def _legal_pages_shop_context() -> dict:
    """Sidebar categories (same as cart/home) for legal and contact pages."""
    return {"categories": _get_categories(), "selected_category": None}


def terms(request: HttpRequest) -> HttpResponse:
    from .models import LegalPage
    shop_ctx = _legal_pages_shop_context()
    legal_page = LegalPage.objects.filter(page_type='terms').first()

    # Fallback to template if no legal page exists in database
    if not legal_page:
        return render(request, "legal/terms.html", shop_ctx)

    context = {
        **shop_ctx,
        "legal_page": legal_page,
        "page_title": legal_page.title or "Terms & Conditions",
    }
    return render(request, "legal/legal_page.html", context)


def privacy(request: HttpRequest) -> HttpResponse:
    from .models import LegalPage
    shop_ctx = _legal_pages_shop_context()
    legal_page = LegalPage.objects.filter(page_type='privacy').first()

    # Fallback to template if no legal page exists in database
    if not legal_page:
        return render(request, "legal/privacy.html", shop_ctx)

    context = {
        **shop_ctx,
        "legal_page": legal_page,
        "page_title": legal_page.title or "Privacy Policy",
    }
    return render(request, "legal/legal_page.html", context)


def refund_returns(request: HttpRequest) -> HttpResponse:
    """Refund & returns policy (static template)."""
    return render(request, "legal/refund_returns.html", _legal_pages_shop_context())


def shipping_policy(request: HttpRequest) -> HttpResponse:
    """Shipping policy (static template)."""
    return render(request, "legal/shipping_policy.html", _legal_pages_shop_context())


def cookie_policy(request: HttpRequest) -> HttpResponse:
    """Cookie policy (static template)."""
    return render(request, "legal/cookie_policy.html", _legal_pages_shop_context())


def contact(request: HttpRequest) -> HttpResponse:
    from django.contrib import messages
    from django.core.mail import EmailMessage
    from django.conf import settings

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        email = (request.POST.get("email") or "").strip()
        subject = (request.POST.get("subject") or "").strip()
        message = (request.POST.get("message") or "").strip()
        website = (request.POST.get("website") or "").strip()
        fax = (request.POST.get("fax") or "").strip()

        if website or fax:
            logger.warning(
                "Contact honeypot triggered (website=%r fax=%r) ip=%s",
                bool(website),
                bool(fax),
                _client_ip(request),
            )
            return render(request, "legal/contact.html", _legal_pages_shop_context())

        ip = _client_ip(request)
        raw_key = f"contact_raw_{ip}"
        raw_count = cache.get(raw_key, 0)
        if raw_count >= CONTACT_BOT_RAW_POST_LIMIT:
            logger.warning("Contact rate limit (raw posts) ip=%s", ip)
            messages.error(
                request,
                "Too many submission attempts from your network. Please try again in an hour.",
            )
            return render(request, "legal/contact.html", _legal_pages_shop_context())
        cache.set(raw_key, raw_count + 1, CONTACT_BOT_WINDOW_SEC)

        form_load_time = (request.POST.get("form_load_time") or "").strip()
        if form_load_time:
            try:
                load_ms = int(form_load_time)
                now_ms = int(time.time() * 1000)
                if now_ms - load_ms < CONTACT_BOT_MIN_SUBMIT_MS:
                    logger.warning(
                        "Contact form submitted too quickly ip=%s delta_ms=%s",
                        ip,
                        now_ms - load_ms,
                    )
                    messages.error(
                        request,
                        "Please wait a few seconds before sending your message.",
                    )
                    return render(request, "legal/contact.html", _legal_pages_shop_context())
            except (ValueError, TypeError):
                pass

        errors = []
        if not name:
            errors.append("Name is required.")
        elif len(name) > CONTACT_NAME_MAX_LEN:
            errors.append("Name is too long.")
        if not email:
            errors.append("Email is required.")
        else:
            try:
                validate_email(email)
            except ValidationError:
                errors.append("Please enter a valid email address.")
        if len(subject) > CONTACT_SUBJECT_MAX_LEN:
            errors.append("Subject is too long.")
        if not message:
            errors.append("Message is required.")
        elif len(message) < CONTACT_MESSAGE_MIN_LEN:
            errors.append(
                f"Please write at least {CONTACT_MESSAGE_MIN_LEN} characters in your message."
            )
        elif len(message) > CONTACT_MESSAGE_MAX_LEN:
            errors.append("Message is too long.")

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            sent_key = f"contact_sent_{ip}"
            sent_count = cache.get(sent_key, 0)
            if sent_count >= CONTACT_BOT_SENT_PER_HOUR:
                logger.warning("Contact hourly send cap ip=%s", ip)
                messages.error(
                    request,
                    "You have sent several messages recently. Please try again later or email us directly.",
                )
            else:
                contact_email = "marbaras.store@gmail.com"
                email_subject = (
                    f"Contact Form: {subject}" if subject else f"Contact Form Message from {name}"
                )

                email_body = f"""
New contact form submission from Marbaras website:

Name: {name}
Email: {email}
Subject: {subject if subject else '(No subject)'}

Message:
{message}

---
This message was sent from the contact form on marbaras.com
You can reply directly to this email to respond to {name} ({email})
"""

                try:
                    email_msg = EmailMessage(
                        subject=email_subject,
                        body=email_body,
                        from_email=getattr(
                            settings, "DEFAULT_FROM_EMAIL", "support@marbaras.com"
                        ),
                        to=[contact_email],
                        reply_to=[email],
                    )
                    email_msg.send(fail_silently=False)
                    cache.set(sent_key, sent_count + 1, CONTACT_BOT_WINDOW_SEC)
                    messages.success(
                        request,
                        "Thank you for your message! We'll get back to you within 1-2 business days.",
                    )
                except Exception as e:
                    logger.error("Failed to send contact form email: %s", e)
                    messages.error(
                        request,
                        "Sorry, there was an error sending your message. Please try again later or email us directly at marbaras.store@gmail.com",
                    )

    return render(request, "legal/contact.html", _legal_pages_shop_context())

@login_required
def profile_dashboard(request: HttpRequest) -> HttpResponse:

    fav_count = Favorite.objects.filter(user=request.user).count()
    order_qs = Order.objects.filter(user=request.user).order_by("-created_at")
    order_count = order_qs.count()
    total_spent = order_qs.aggregate(s=Sum("total_price"))["s"] or 0
    recent_orders = (
        order_qs
        .select_related("shipping_option", "coupon")
        [:5]
    )


    categories = _get_categories()
    cart_items = _cart_items_for(request)
    cart_count = sum(getattr(it, "quantity", 0) for it in cart_items)

    return render(request, "dashboard.html", {
        "fav_count": fav_count,
        "order_count": order_count,
        "total_spent": total_spent,
        "recent_orders": recent_orders,
        "active_tab": "dashboard",


        "categories": categories,
        "cart_count": cart_count,
    })

@login_required
def profile_favorites(request: HttpRequest) -> HttpResponse:

    qs = (
        Product.objects
        .filter(favorited_by__user=request.user)
        .select_related("category")
        .prefetch_related("images")
        .annotate(last_fav_at=Max("favorited_by__created_at"))
        .order_by("-last_fav_at", "-id")
    )

    paginator = Paginator(qs, 12)
    products_page = paginator.get_page(request.GET.get("page"))

    favorite_ids = set(
        Favorite.objects.filter(user=request.user)
        .values_list("product_id", flat=True)
    )


    categories = _get_categories()
    try:
        cart_items = _cart_items_for(request)
        cart_count = sum(getattr(it, "quantity", 0) for it in cart_items)
    except NameError:

        cart_count = 0

    return render(request, "favorites.html", {
        "products_page": products_page,
        "favorite_ids": favorite_ids,
        "categories": categories,
        "cart_count": cart_count,
        "active_tab": "favorites",
    })


@login_required
def profile_orders(request: HttpRequest) -> HttpResponse:
    try:
        orders = (
            Order.objects
            .filter(user=request.user)
            .select_related("shipping_option", "coupon")
            .order_by("-created_at")
        )
        paginator = Paginator(orders, 10)
        page = request.GET.get("page")
        orders_page = paginator.get_page(page)

        # Get item counts for orders on current page
        counts_map = {}
        if orders_page.object_list:
            item_counts = (
                OrderItem.objects
                .filter(order__in=orders_page.object_list)
                .values("order_id")
                .annotate(c=Sum("quantity"))
            )
            counts_map = {row["order_id"]: row["c"] for row in item_counts}

        return render(request, "orders.html", {
            "orders_page": orders_page,
            "counts_map": counts_map,
            "active_tab": "orders",
        })
    except Exception as e:
        logger.exception("Error in profile_orders: %s", e)
        return render(request, "orders.html", {
            "orders_page": None,
            "counts_map": {},
            "active_tab": "orders",
            "error": "An error occurred while loading your orders.",
        })


@login_required
@login_required
def profile_details(request: HttpRequest) -> HttpResponse:
    """Account details page - allows users to save phone, email, and address."""
    user = request.user
    profile, created = UserProfile.objects.get_or_create(user=user)
    
    if request.method == "POST":
        # Update profile with form data
        profile.phone = request.POST.get("phone", "").strip() or None
        profile.email = request.POST.get("email", "").strip() or None
        profile.address = request.POST.get("address", "").strip() or None
        profile.city = request.POST.get("city", "").strip() or None
        profile.postal_code = request.POST.get("postal_code", "").strip() or None
        profile.country = request.POST.get("country", "").strip() or None
        profile.save()
        messages.success(request, "Account details updated successfully!")
        return redirect("profile_details")
    
    return render(request, "account_details.html", {
        "user": user,
        "profile": profile,
        "active_tab": "details",
    })

@login_required
@require_POST
def remove_from_favorites(request: HttpRequest, pk: int) -> HttpResponse:
    product = get_object_or_404(Product, pk=pk)
    Favorite.objects.filter(user=request.user, product=product).delete()


    back = request.META.get("HTTP_REFERER")
    if back:

        from django.utils.http import url_has_allowed_host_and_scheme
        if url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
            return redirect(back)


    return redirect("profile_favorites")






def health_check(request: HttpRequest) -> JsonResponse:
    """Health check endpoint for monitoring."""
    from django.db import connection
    from django.core.cache import cache
    
    status = {
        "status": "healthy",
        "timestamp": timezone.now().isoformat(),
        "checks": {}
    }
    
    # Database check
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            status["checks"]["database"] = "ok"
    except Exception as e:
        status["status"] = "unhealthy"
        status["checks"]["database"] = f"error: {str(e)}"
    
    # Cache check (if Redis/Memcached is configured)
    try:
        cache.set("health_check", "ok", 10)
        if cache.get("health_check") == "ok":
            status["checks"]["cache"] = "ok"
        else:
            status["checks"]["cache"] = "not_configured"
    except Exception:
        status["checks"]["cache"] = "not_configured"
    
    # Stripe check
    try:
        if settings.STRIPE_SECRET_KEY:
            status["checks"]["stripe"] = "configured"
        else:
            status["checks"]["stripe"] = "not_configured"
    except Exception:
        status["checks"]["stripe"] = "error"
    
    http_status = 200 if status["status"] == "healthy" else 503
    return JsonResponse(status, status=http_status)


# =============================================================================
# Error Handlers
# =============================================================================

def handler404(request: HttpRequest, exception) -> HttpResponse:
    """Custom 404 error handler."""

    logger.warning(f"404 error: {request.path} from {request.get_host()}, allowed hosts: {settings.ALLOWED_HOSTS}")
    return render(request, '404.html', status=404)

def handler500(request: HttpRequest) -> HttpResponse:
    """Custom 500 error handler."""
    return render(request, '500.html', status=500)


# =============================================================================
# Email Testing Endpoint (Temporary - Remove after testing)
# =============================================================================

def blog_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """Blog post detail page."""
    import re
    from django.utils.safestring import mark_safe
    
    post = get_object_or_404(BlogPost, slug=slug, is_published=True)
    categories = _get_categories()
    
    # Format content into paragraphs
    formatted_content = post.content
    if formatted_content:
        value_str = str(formatted_content)
        
        # Check if content has HTML tags
        has_html_tags = bool(re.search(r'<[^>]+>', value_str))
        
        if not has_html_tags:
            # Plain text - convert to HTML paragraphs
            value_str = re.sub(r'\r\n', '\n', value_str)
            value_str = re.sub(r'\r', '\n', value_str)
            value_str = value_str.strip()
            
            # Split by double newlines or by sentences
            if '\n\n' in value_str or value_str.count('\n') >= 3:
                paragraphs = re.split(r'\n\s*\n+', value_str)
            else:
                # Split by sentences
                parts = re.split(r'([.!?])\s+([A-Z])', value_str)
                
                if len(parts) > 3:
                    sentences = []
                    i = 0
                    while i < len(parts):
                        if i + 2 < len(parts):
                            sentence = parts[i] + parts[i+1] + ' ' + parts[i+2]
                            sentences.append(sentence.strip())
                            i += 3
                        else:
                            if parts[i].strip():
                                sentences.append(parts[i].strip())
                            i += 1
                    
                    # Group sentences into paragraphs (2-3 sentences per paragraph)
                    paragraphs = []
                    current_para = []
                    
                    for sentence in sentences:
                        sentence = sentence.strip()
                        if not sentence:
                            continue
                        
                        current_para.append(sentence)
                        sentence_length = len(sentence)
                        if len(current_para) >= 3 or (len(current_para) >= 2 and sentence_length > 150):
                            paragraphs.append(' '.join(current_para))
                            current_para = []
                    
                    if current_para:
                        paragraphs.append(' '.join(current_para))
                else:
                    paragraphs = [value_str]
            
            # Format paragraphs
            formatted_paragraphs = []
            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue
                
                lines = [line.strip() for line in para.split('\n') if line.strip()]
                if len(lines) > 1:
                    para_content = '<br>'.join(lines)
                else:
                    para_content = lines[0] if lines else para
                
                formatted_paragraphs.append(f'<p>{para_content}</p>')
            
            formatted_content = mark_safe('\n\n'.join(formatted_paragraphs))
        else:
            # Already HTML, just mark as safe
            formatted_content = mark_safe(formatted_content)
    
    # Convert video URL to embed format if needed (only if no video_file is uploaded)
    video_embed_url = None
    if not post.video_file and post.video_url:
        video_url = post.video_url.strip()
        # YouTube watch URL: https://www.youtube.com/watch?v=VIDEO_ID
        if 'youtube.com/watch' in video_url:
            video_id = video_url.split('v=')[1].split('&')[0]
            video_embed_url = f'https://www.youtube.com/embed/{video_id}'
        # YouTube short URL: https://youtu.be/VIDEO_ID
        elif 'youtu.be/' in video_url:
            video_id = video_url.split('youtu.be/')[1].split('?')[0]
            video_embed_url = f'https://www.youtube.com/embed/{video_id}'
        # Vimeo URL: https://vimeo.com/VIDEO_ID
        elif 'vimeo.com/' in video_url:
            video_id = video_url.split('vimeo.com/')[1].split('?')[0]
            video_embed_url = f'https://player.vimeo.com/video/{video_id}'
        # Already embed format or other platform
        else:
            video_embed_url = video_url
    
    context = {
        "post": post,
        "formatted_content": formatted_content,
        "video_embed_url": video_embed_url,
        "categories": categories,
        "cart_count": _cart_total_quantity(request),
    }
    return render(request, "blog_detail.html", context)


@require_POST
def remove_from_sale(request: HttpRequest, pk: int) -> JsonResponse:
    """Remove product from Sale category when promotion expires."""
    try:
        product = get_object_or_404(Product, pk=pk)
        sale_category = Category.objects.filter(name__iexact='Sale').first()
        if not sale_category:
            sale_category = Category.objects.filter(name__icontains='разпродажба').first()
        
        if sale_category and sale_category in product.categories.all():
            product.categories.remove(sale_category)
            logger.info(f"Removed product {product.id} from Sale category via AJAX")
            return JsonResponse({'success': True, 'message': 'Product removed from sale'})
        else:
            return JsonResponse({'success': False, 'message': 'Product not in sale category'})
    except Exception as e:
        logger.exception(f"Error removing product from sale: {e}")
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


def test_emails_view(request: HttpRequest) -> HttpResponse:
    """Test email sending functionality - accessible from browser."""
    # Check if user is staff (allow unauthenticated for testing, but check staff if logged in)
    if request.user.is_authenticated and not request.user.is_staff:
        messages.error(request, "You must be a staff user to test emails.")
        return redirect("home")

    default_email = (request.user.email or "") if request.user.is_authenticated else ""
    email = (request.GET.get("email") or default_email).strip()
    email_type = request.GET.get('type', 'all')
    
    if not email:
        return render(request, 'test_emails.html', {
            'error': 'Please provide an email address: ?email=your@email.com'
        })
    
    results = []
    base_url = request.build_absolute_uri('/').rstrip('/')
    
    # Test Welcome Email
    if email_type in ['welcome', 'all']:
        try:
            user, created = User.objects.get_or_create(
                username=f'test_user_{uuid.uuid4().hex[:8]}',
                defaults={'email': email, 'first_name': 'Test', 'last_name': 'User'}
            )
            if not created:
                user.email = email
                user.save()
            
            if send_welcome_email(user, base_url):
                results.append({'type': 'Welcome Email', 'status': 'success', 'message': f'Sent to {email}'})
            else:
                results.append({'type': 'Welcome Email', 'status': 'error', 'message': 'Send failed'})
        except Exception as e:
            results.append({'type': 'Welcome Email', 'status': 'error', 'message': str(e)})
    
    # Test Order Confirmation Email
    if email_type in ['order', 'all']:
        try:
            user, _ = User.objects.get_or_create(
                username=f'test_user_{uuid.uuid4().hex[:8]}',
                defaults={'email': email}
            )
            if user.email != email:
                user.email = email
                user.save()
            
            shipping, _ = ShippingOption.objects.get_or_create(
                name='Standard',
                defaults={'price': Decimal('5.00'), 'delivery_time': '3-5 days'}
            )
            
            product = Product.objects.first()
            if not product:
                results.append({'type': 'Order Confirmation', 'status': 'error', 'message': 'No products in database'})
            else:
                order = Order.objects.create(
                    user=user,
                    email=email,
                    full_name='Test Customer',
                    address='123 Test Street',
                    city='London',
                    postal_code='1000',
                    phone='+359888123456',
                    shipping_option=shipping,
                    total_price=Decimal('99.99'),
                )
                
                variant = product.variants.first()
                OrderItem.objects.create(
                    order=order,
                    product=product,
                    variant=variant,
                    quantity=2,
                )
                
                if send_order_confirmation_email(order, base_url, notify_admin=False):
                    results.append({'type': 'Order Confirmation', 'status': 'success', 'message': f'Sent to {email}'})
                else:
                    results.append({'type': 'Order Confirmation', 'status': 'error', 'message': 'Send failed'})
        except Exception as e:
            results.append({'type': 'Order Confirmation', 'status': 'error', 'message': str(e)})
    
    # Test Simple Email
    if email_type in ['simple', 'all']:
        try:
            send_mail(
                'Test email from Marbaras',
                'This is a test message. If you receive it, your email configuration works.',
                settings.DEFAULT_FROM_EMAIL,
                [email],
                fail_silently=False
            )
            results.append({'type': 'Simple Test Email', 'status': 'success', 'message': f'Sent to {email}'})
        except Exception as e:
            results.append({'type': 'Simple Test Email', 'status': 'error', 'message': str(e)})
    
    return render(request, 'test_emails.html', {
        'results': results,
        'email': email,
        'email_type': email_type,
        'email_settings': {
            'EMAIL_HOST': settings.EMAIL_HOST,
            'EMAIL_PORT': settings.EMAIL_PORT,
            'EMAIL_USE_TLS': settings.EMAIL_USE_TLS,
            'DEFAULT_FROM_EMAIL': settings.DEFAULT_FROM_EMAIL,
        }
    })