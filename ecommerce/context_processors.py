
from django.urls import resolve, reverse
from django.db.models import Sum
from ecommerce.models import Product, Category, CartItem


def breadcrumbs(request):
    """
    Build breadcrumbs for all pages:
    - Home (always first)
    - Category (when on products_by_category or product_detail)
    - Product (when on product_detail)
    - Cart, Checkout, Profile pages, etc.
    """
    try:
        home_url = reverse("home")
    except Exception:
        home_url = "/"

    trail = [{"name": "Home", "url": home_url}]

    try:
        match = resolve(request.path_info)
    except Exception:

        return {"breadcrumbs": trail}

    url_name = match.url_name


    if url_name == "home":
        return {"breadcrumbs": trail}


    if url_name == "products_by_category":
        cat_slug = match.kwargs.get("slug")
        category = Category.objects.filter(slug=cat_slug).first()
        if category:
            trail.append({"name": category.name, "url": request.path})
        return {"breadcrumbs": trail}


    if url_name == "product_detail":
        product_slug = match.kwargs.get("slug")
        product = (
            Product.objects.select_related("category")
            .filter(slug=product_slug)
            .first()
        )
        if product:
            if product.category:
                trail.append({
                    "name": product.category.name,
                    "url": reverse("products_by_category", kwargs={"slug": product.category.slug})
                })

            product_name = product.name
            if len(product_name) > 50:
                product_name = product_name[:47] + "..."
            trail.append({"name": product_name, "url": request.path})
        return {"breadcrumbs": trail}


    if url_name == "cart_view":
        trail.append({"name": "Cart", "url": request.path})
        return {"breadcrumbs": trail}


    if url_name in ("checkout", "guest_checkout"):
        trail.append({"name": "Checkout", "url": request.path})
        return {"breadcrumbs": trail}


    if url_name in ("order_success", "success", "payment_success"):
        trail.append({"name": "Order Success", "url": request.path})
        return {"breadcrumbs": trail}


    if url_name in ("favorites_list", "profile_favorites"):
        trail.append({"name": "Favorites", "url": request.path})
        return {"breadcrumbs": trail}


    if url_name == "profile_dashboard":
        trail.append({"name": "Profile", "url": request.path})
        return {"breadcrumbs": trail}

    if url_name == "profile_orders":
        trail.append({"name": "Profile", "url": reverse("profile_dashboard")})
        trail.append({"name": "Orders", "url": request.path})
        return {"breadcrumbs": trail}

    if url_name == "profile_details":
        trail.append({"name": "Profile", "url": reverse("profile_dashboard")})
        trail.append({"name": "Details", "url": request.path})
        return {"breadcrumbs": trail}


    if url_name in ("account_login", "login"):
        trail.append({"name": "Login", "url": request.path})
        return {"breadcrumbs": trail}

    if url_name == "account_signup":
        trail.append({"name": "Register", "url": request.path})
        return {"breadcrumbs": trail}

    if url_name == "register":
        trail.append({"name": "Register", "url": request.path})
        return {"breadcrumbs": trail}


    if url_name == "terms":
        trail.append({"name": "Terms", "url": request.path})
        return {"breadcrumbs": trail}

    if url_name == "privacy":
        trail.append({"name": "Privacy", "url": request.path})
        return {"breadcrumbs": trail}

    if url_name == "contact":
        trail.append({"name": "Contact", "url": request.path})
        return {"breadcrumbs": trail}


    if url_name == "product_list":
        trail.append({"name": "Products", "url": request.path})
        return {"breadcrumbs": trail}


    return {"breadcrumbs": trail}


def cart_count(request):
    """
    Return the TOTAL quantity of items in the cart for the current visitor.

    - Authenticated users: sum quantities for rows tied to the user.
    - Anonymous users: ensure a session exists, then sum quantities for rows tied to session_key.
    - Never raises; falls back to 0 if anything goes wrong.
    """
    try:
        if request.user.is_authenticated:
            total = (
                CartItem.objects
                .filter(user=request.user)
                .aggregate(c=Sum("quantity"))
                .get("c") or 0
            )
            return {"cart_count": int(total)}


        if not request.session.session_key:
            request.session.create()

        total = (
            CartItem.objects
            .filter(session_key=request.session.session_key)
            .aggregate(c=Sum("quantity"))
            .get("c") or 0
        )
        return {"cart_count": int(total)}

    except Exception:

        return {"cart_count": 0}


def categories(request):
    """
    Return all categories for use in templates (e.g., mobile menu, sidebar).
    Uses cached categories if available.
    """
    from django.core.cache import cache
    from .views import _get_categories

    try:
        categories_list = _get_categories()
        return {"categories": categories_list}
    except Exception:

        return {"categories": []}


def meta_pixel_id(request):
    """
    Return Meta Pixel ID and store currency for templates (PageView, ViewContent, etc.).
    """
    from django.conf import settings
    return {
        "META_PIXEL_ID": getattr(settings, "META_PIXEL_ID", ""),
        "META_PIXEL_CURRENCY": getattr(settings, "META_PIXEL_CURRENCY", "EUR"),
    }


def tiktok_pixel_id(request):
    """TikTok Pixel ID and currency for ttq (PageView, ViewContent, AddToCart)."""
    from django.conf import settings
    return {
        "TIKTOK_PIXEL_ID": getattr(settings, "TIKTOK_PIXEL_ID", ""),
        "TIKTOK_PIXEL_CURRENCY": getattr(settings, "TIKTOK_PIXEL_CURRENCY", "EUR"),
    }


def google_analytics_id(request):
    """
    Return Google Analytics ID (GA4) from settings for use in templates.
    """
    from django.conf import settings
    return {"GOOGLE_ANALYTICS_ID": getattr(settings, "GOOGLE_ANALYTICS_ID", "")}


def show_welcome_discount_button(request):
    """
    Check if welcome discount button should be shown.
    Returns True if user hasn't received a welcome promo code yet.
    """
    from ecommerce.models import EmailSubscription
    
    # If user is authenticated, check by email
    if request.user.is_authenticated and request.user.email:
        has_subscription = EmailSubscription.objects.filter(email=request.user.email.lower()).exists()
        return {"show_welcome_discount_button": not has_subscription}
    
    # For anonymous users, check cookie
    # Cookie is set when user subscribes via popup
    cookies = request.COOKIES
    has_subscribed = cookies.get('email_popup_subscribed') == 'true'
    
    return {"show_welcome_discount_button": not has_subscribed}


def cloudinary_config(request):
    """
    Return Cloudinary configuration for use in templates.
    """
    from django.conf import settings
    return {"CLOUDINARY_CLOUD_NAME": getattr(settings, "CLOUDINARY_CLOUD_NAME", "")}
