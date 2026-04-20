
from django.contrib import admin
from django.db.models import Count, Sum, Case, When, Value
from django.http import HttpResponse, HttpResponseRedirect
from django.contrib import messages
from django.utils.html import format_html, escape
from django.shortcuts import redirect, render
from django import forms
from django.conf import settings
from django.utils import timezone
from django.urls import path
from decimal import Decimal
import csv
import json

from .models import (
    BlogPost,
    CustomerReview,
    BannerImage,
    LegalPage,
    EmailSubscription,
    Category,
    Product,
    ProductImage,
    ProductVariant,
    CartItem,
    Order,
    OrderItem,
    Favorite,
    Discount,
    ShippingOption,
    Coupon,
    ProductBundleItem,
    ProductReview,
    MarketplaceOrder,
)
from .utils.emailing import send_order_shipped_email


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1
    max_num = 10
    fields = ("image",)
    verbose_name = "Product Image"
    verbose_name_plural = "Product Images"

class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1
    fields = ("variant_type", "size", "stock", "price_override", "sku")

class ProductBundleItemInline(admin.TabularInline):
    model = ProductBundleItem
    fk_name = "product"
    extra = 1
    autocomplete_fields = ["item"]
    fields = ("item", "position", "is_active", "note")
    ordering = ("position",)


    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "item":

            obj_id = request.resolver_match.kwargs.get("object_id")
            if obj_id:
                kwargs["queryset"] = Product.objects.exclude(pk=obj_id)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class CategorySubcategoryInline(admin.TabularInline):
    """Inline for adding sub-categories to a category."""
    model = Category
    fk_name = "parent"
    extra = 1
    fields = ("name", "slug", "image")  # Only show these fields, parent is automatically set via fk_name
    verbose_name = "Sub-category"
    verbose_name_plural = "Sub-categories"


class PriceIncreaseForm(forms.Form):
    """Form for global price increase."""
    percentage = forms.DecimalField(
        label="Percentage Increase (%)",
        help_text="Enter the percentage to increase all prices (e.g., 5 for 5% increase)",
        min_value=0,
        max_value=1000,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'step': '0.01', 'class': 'vTextField'})
    )
    apply_to_discount_price = forms.BooleanField(
        label="Also increase discount prices",
        help_text="If checked, discount prices will also be increased",
        required=False,
        initial=True
    )
    apply_to_variants = forms.BooleanField(
        label="Also increase variant prices",
        help_text="If checked, product variant price overrides will also be increased",
        required=False,
        initial=True
    )


class PriceDecreaseForm(forms.Form):
    """Form for global price decrease."""
    percentage = forms.DecimalField(
        label="Percentage Decrease (%)",
        help_text="Enter the percentage to decrease all prices (e.g., 5 for 5% decrease)",
        min_value=0,
        max_value=100,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'step': '0.01', 'class': 'vTextField'})
    )
    apply_to_discount_price = forms.BooleanField(
        label="Also decrease discount prices",
        help_text="If checked, discount prices will also be decreased",
        required=False,
        initial=True
    )
    apply_to_variants = forms.BooleanField(
        label="Also decrease variant prices",
        help_text="If checked, product variant price overrides will also be decreased",
        required=False,
        initial=True
    )

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    inlines = [ProductImageInline, ProductVariantInline, ProductBundleItemInline]
    actions = [
        "export_products_csv_action",
        "increase_prices_action",
        "decrease_prices_action",
        "create_zodiac_variants_action",
        "create_earring_hoop_variants_action",
    ]
    readonly_fields = (
        "inventory_total_units_display",
        "inventory_total_weight_display",
    )

    def inventory_total_units_display(self, obj):
        if obj is None or not getattr(obj, "pk", None):
            return "—"
        return obj.total_stock_units()

    inventory_total_units_display.short_description = "Общо бройки"

    def inventory_total_weight_display(self, obj):
        if obj is None or not getattr(obj, "pk", None):
            return "—"
        tw = obj.total_weight_grams_value()
        if tw is None:
            return "—"
        q = tw.quantize(Decimal("0.001"))
        return f"{q} g"

    inventory_total_weight_display.short_description = "Общ грамаж"

    def export_products_csv_action(self, request, queryset):
        """Download selected products as UTF-8 CSV (Excel-friendly with BOM)."""
        queryset = queryset.select_related("category").prefetch_related("variants", "categories")
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="products_export.csv"'
        response.write("\ufeff")
        writer = csv.writer(response)
        header = [
            "id",
            "name",
            "slug",
            "serial_number",
            "brand",
            "category",
            "categories_all",
            "price",
            "discount_price",
            "stock",
            "total_units",
            "unit_weight_grams",
            "total_weight_grams",
            "variants_size_stock",
            "recently_sold",
            "cart_add_count",
        ]
        if hasattr(Product, "sale_expires_at"):
            header.append("sale_expires_at")
        writer.writerow(header)

        for p in queryset:
            cats_all = ", ".join(c.name for c in p.categories.all())
            tw = p.total_weight_grams_value()
            tw_str = str(tw.quantize(Decimal("0.001"))) if tw is not None else ""
            variants_bits = []
            for v in p.variants.all():
                label = getattr(v, "display_name", None) or v.size or str(v.pk)
                variants_bits.append(f"{label}={v.stock}")
            variants_col = "; ".join(variants_bits)
            row = [
                p.pk,
                p.name,
                p.slug or "",
                p.serial_number or "",
                p.brand or "",
                p.category.name if p.category else "",
                cats_all,
                str(p.price),
                str(p.discount_price) if p.discount_price is not None else "",
                p.stock,
                p.total_stock_units(),
                str(p.unit_weight_grams) if p.unit_weight_grams is not None else "",
                tw_str,
                variants_col,
                p.recently_sold,
                p.cart_add_count,
            ]
            if hasattr(Product, "sale_expires_at"):
                row.append(
                    timezone.localtime(p.sale_expires_at).strftime("%Y-%m-%d %H:%M")
                    if p.sale_expires_at
                    else ""
                )
            writer.writerow(row)

        self.message_user(
            request,
            f"Exported {queryset.count()} product(s) to CSV.",
            messages.SUCCESS,
        )
        return response

    export_products_csv_action.short_description = "Export selected products to CSV"

    def get_list_display(self, request):
        """Changelist columns including inventory totals (not only the edit form)."""
        base_fields = (
            "name",
            "serial_number",
            "price",
            "discount_price",
            "category",
            "brand",
            "inventory_total_units_display",
            "unit_weight_grams",
            "inventory_total_weight_display",
            "cart_add_count",
        )
        try:
            from ecommerce.models import Product
            if hasattr(Product, "sale_expires_at"):
                return base_fields + ("sale_expires_at",)
        except Exception:
            pass
        return base_fields

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.prefetch_related("variants")

    list_filter = ("category", "brand", "categories")
    filter_horizontal = ("categories",)
    
    def get_fieldsets(self, request, obj=None):
        """Dynamically get fieldsets to handle missing sale_expires_at field."""
        fieldsets = (
            ("Basic Information", {
                "fields": ("name", "description", "category", "categories", "brand", "serial_number")
            }),
            ("Pricing", {
                "fields": ("price", "discount_price")
            }),
            ("Inventory", {
                "fields": (
                    "stock",
                    "recently_sold",
                    "cart_add_count",
                    "unit_weight_grams",
                    "inventory_total_units_display",
                    "inventory_total_weight_display",
                ),
                "description": "Общите бройки и общият грамаж се изчисляват автоматично. При продукти с варианти се сумира наличността от таба „Product variants“.",
            }),
            ("Images", {
                "fields": ("image",)
            }),
        )
        
        # Add Sale Settings only if sale_expires_at field exists
        try:
            from ecommerce.models import Product
            if hasattr(Product, 'sale_expires_at'):
                # Insert Sale Settings after Pricing
                fieldsets_list = list(fieldsets)
                fieldsets_list.insert(2, (
                    "Sale Settings", {
                        "fields": ("sale_expires_at",),
                        "description": "Set expiration time for Sale category. Product will be automatically removed from Sale when time expires.",
                        "classes": ("collapse",),
                    }
                ))
                return tuple(fieldsets_list)
        except:
            pass
        
        return fieldsets

    search_fields = ("=serial_number", "^name", "name", "serial_number", "brand", "category__name")
    search_help_text = "Search by exact serial (best), name, brand, or category."

    list_select_related = ("category",)

    class Media:
        js = ("admin/js/sale_timer.js",)
        css = {
            'all': ('admin/css/sale_timer.css',)
        }

    def get_search_results(self, request, queryset, search_term):
        qs, use_distinct = super().get_search_results(request, queryset, search_term)
        if search_term:
            exact = queryset.model.objects.filter(serial_number__iexact=search_term)
            qs = exact | qs
        return qs, use_distinct
    
    def get_urls(self):
        """Add custom URLs for price increase/decrease forms and inventory scanner."""
        urls = super().get_urls()
        custom_urls = [
            path('increase-prices/', self.admin_site.admin_view(self.increase_prices_view), name='ecommerce_product_increase_prices'),
            path('decrease-prices/', self.admin_site.admin_view(self.decrease_prices_view), name='ecommerce_product_decrease_prices'),
            path('inventory-scanner/', self.admin_site.admin_view(self.inventory_scanner_view), name='ecommerce_product_inventory_scanner'),
        ]
        return custom_urls + urls
    
    def increase_prices_action(self, request, queryset):
        """Admin action to redirect to price increase form."""
        # Store selected product IDs in session
        product_ids = list(queryset.values_list('id', flat=True))
        request.session['price_increase_product_ids'] = product_ids
        return redirect('admin:ecommerce_product_increase_prices')
    increase_prices_action.short_description = "📈 Increase prices by percentage (selected products)"
    
    def create_zodiac_variants_action(self, request, queryset):
        """Admin action to create 12 zodiac sign variants for selected products."""
        from ecommerce.models import ProductVariant
        
        zodiac_signs = [
            "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
            "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"
        ]
        
        created_count = 0
        for product in queryset:
            for sign in zodiac_signs:
                # Check if variant already exists
                variant, created = ProductVariant.objects.get_or_create(
                    product=product,
                    variant_type='zodiac_sign',
                    size=sign,
                    defaults={
                        'stock': product.stock if hasattr(product, 'stock') else 0,
                    }
                )
                if created:
                    created_count += 1
        
        if created_count > 0:
            self.message_user(request, f"Successfully created {created_count} zodiac sign variants for {queryset.count()} product(s).", messages.SUCCESS)
        else:
            self.message_user(request, "All zodiac variants already exist for selected products.", messages.INFO)
    create_zodiac_variants_action.short_description = "♈ Create 12 zodiac sign variants (selected products)"
    
    def create_earring_hoop_variants_action(self, request, queryset):
        """Admin action to create earring hoop size variants for selected products."""
        from ecommerce.models import ProductVariant, EARRING_HOOP_SIZE_CHOICES
        
        created_count = 0
        for product in queryset:
            for size_value, size_display in EARRING_HOOP_SIZE_CHOICES:
                # Check if variant already exists
                variant, created = ProductVariant.objects.get_or_create(
                    product=product,
                    variant_type='earring_hoop_size',
                    size=size_value,
                    defaults={
                        'stock': product.stock if hasattr(product, 'stock') else 0,
                    }
                )
                if created:
                    created_count += 1
        
        if created_count > 0:
            self.message_user(request, f"Successfully created {created_count} earring hoop size variants for {queryset.count()} product(s).", messages.SUCCESS)
        else:
            self.message_user(request, "All earring hoop size variants already exist for selected products.", messages.INFO)
    create_earring_hoop_variants_action.short_description = "💍 Create earring hoop size variants (selected products)"
    
    def increase_prices_view(self, request):
        """View for price increase form and processing."""
        # Get product IDs from session or use all products
        product_ids = request.session.get('price_increase_product_ids', None)
        if product_ids:
            queryset = Product.objects.filter(id__in=product_ids)
            del request.session['price_increase_product_ids']
        else:
            queryset = Product.objects.all()
        
        if request.method == 'POST':
            form = PriceIncreaseForm(request.POST)
            if form.is_valid():
                percentage = form.cleaned_data['percentage']
                apply_to_discount = form.cleaned_data['apply_to_discount_price']
                apply_to_variants = form.cleaned_data['apply_to_variants']
                
                # Calculate multiplier (e.g., 5% = 1.05)
                multiplier = Decimal('1') + (percentage / Decimal('100'))
                
                updated_count = 0
                updated_variants = 0
                
                # Update product prices
                for product in queryset:
                    # Update regular price
                    old_price = product.price
                    new_price = (old_price * multiplier).quantize(Decimal('0.01'))
                    product.price = new_price
                    
                    # Update discount price if requested
                    if apply_to_discount and product.discount_price:
                        old_discount = product.discount_price
                        new_discount = (old_discount * multiplier).quantize(Decimal('0.01'))
                        product.discount_price = new_discount
                    
                    product.save(update_fields=['price', 'discount_price'])
                    updated_count += 1
                    
                    # Update variant prices if requested
                    if apply_to_variants:
                        for variant in product.variants.all():
                            if variant.price_override:
                                old_variant_price = variant.price_override
                                new_variant_price = (old_variant_price * multiplier).quantize(Decimal('0.01'))
                                variant.price_override = new_variant_price
                                variant.save(update_fields=['price_override'])
                                updated_variants += 1
                
                messages.success(
                    request,
                    f"✅ Successfully increased prices by {percentage}% for {updated_count} product(s). "
                    f"{f'Updated {updated_variants} variant prices.' if updated_variants > 0 else ''}"
                )
                return redirect('admin:ecommerce_product_changelist')
        else:
            form = PriceIncreaseForm()
        
        context = {
            'form': form,
            'title': 'Increase Prices by Percentage',
            'product_count': queryset.count(),
            'opts': self.model._meta,
            'has_view_permission': self.has_view_permission(request, None),
        }
        
        return render(request, 'admin/ecommerce/product/increase_prices.html', context)
    
    def decrease_prices_action(self, request, queryset):
        """Admin action to redirect to price decrease form."""
        # Store selected product IDs in session
        product_ids = list(queryset.values_list('id', flat=True))
        request.session['price_decrease_product_ids'] = product_ids
        return redirect('admin:ecommerce_product_decrease_prices')
    decrease_prices_action.short_description = "📉 Decrease prices by percentage (selected products)"
    
    def decrease_prices_view(self, request):
        """View for price decrease form and processing."""
        # Get product IDs from session or use all products
        product_ids = request.session.get('price_decrease_product_ids', None)
        if product_ids:
            queryset = Product.objects.filter(id__in=product_ids)
            del request.session['price_decrease_product_ids']
        else:
            queryset = Product.objects.all()
        
        if request.method == 'POST':
            form = PriceDecreaseForm(request.POST)
            if form.is_valid():
                percentage = form.cleaned_data['percentage']
                apply_to_discount = form.cleaned_data['apply_to_discount_price']
                apply_to_variants = form.cleaned_data['apply_to_variants']
                
                # Calculate multiplier (e.g., 5% decrease = 0.95)
                multiplier = Decimal('1') - (percentage / Decimal('100'))
                
                updated_count = 0
                updated_variants = 0
                
                # Update product prices
                for product in queryset:
                    # Update regular price
                    old_price = product.price
                    new_price = (old_price * multiplier).quantize(Decimal('0.01'))
                    # Ensure price doesn't go below 0
                    if new_price < Decimal('0'):
                        new_price = Decimal('0')
                    product.price = new_price
                    
                    # Update discount price if requested
                    if apply_to_discount and product.discount_price:
                        old_discount = product.discount_price
                        new_discount = (old_discount * multiplier).quantize(Decimal('0.01'))
                        if new_discount < Decimal('0'):
                            new_discount = Decimal('0')
                        product.discount_price = new_discount
                    
                    product.save(update_fields=['price', 'discount_price'])
                    updated_count += 1
                    
                    # Update variant prices if requested
                    if apply_to_variants:
                        for variant in product.variants.all():
                            if variant.price_override:
                                old_variant_price = variant.price_override
                                new_variant_price = (old_variant_price * multiplier).quantize(Decimal('0.01'))
                                if new_variant_price < Decimal('0'):
                                    new_variant_price = Decimal('0')
                                variant.price_override = new_variant_price
                                variant.save(update_fields=['price_override'])
                                updated_variants += 1
                
                messages.success(
                    request,
                    f"✅ Successfully decreased prices by {percentage}% for {updated_count} product(s). "
                    f"{f'Updated {updated_variants} variant prices.' if updated_variants > 0 else ''}"
                )
                return redirect('admin:ecommerce_product_changelist')
        else:
            form = PriceDecreaseForm()
        
        context = {
            'form': form,
            'title': 'Decrease Prices by Percentage',
            'product_count': queryset.count(),
            'opts': self.model._meta,
            'has_view_permission': self.has_view_permission(request, None),
            'action_type': 'decrease',
        }
        
        return render(request, 'admin/ecommerce/product/increase_prices.html', context)
    
    def inventory_scanner_view(self, request):
        """View for inventory scanner - view, add, or remove stock."""
        from django.http import JsonResponse
        from django.db import transaction
        from .models import ProductVariant
        
        if request.method == 'POST':
            # Handle barcode scan
            barcode = request.POST.get('barcode', '').strip()
            action = request.POST.get('action', 'view').strip()  # 'view', 'add', 'remove'
            quantity = int(request.POST.get('quantity', 1))  # Quantity to add/remove
            
            if not barcode:
                return JsonResponse({'success': False, 'error': 'No barcode provided'}, status=400)
            
            if action != 'view' and (quantity < 1):
                return JsonResponse({'success': False, 'error': 'Quantity must be at least 1'}, status=400)
            
            # Try to find product by serial_number, SKU (from variants), or ID
            product = None
            variant = None
            
            # First try serial_number (exact match)
            try:
                product = Product.objects.get(serial_number__iexact=barcode)
            except Product.DoesNotExist:
                pass
            
            # If not found, try SKU from variants
            if not product:
                try:
                    variant = ProductVariant.objects.select_related('product').get(sku__iexact=barcode)
                    product = variant.product
                except ProductVariant.DoesNotExist:
                    pass
            
            # If still not found, try product ID
            if not product:
                try:
                    product_id = int(barcode)
                    product = Product.objects.get(pk=product_id)
                except (ValueError, Product.DoesNotExist):
                    pass
            
            if not product:
                return JsonResponse({
                    'success': False,
                    'error': f'Product not found for barcode: {barcode}'
                }, status=404)
            
            # Get current stock
            if variant:
                current_stock = variant.stock
            else:
                current_stock = product.stock
            
            # Check if product has variants (ring sizes or zodiac signs)
            # Check if variant_type field exists (for backward compatibility)
            from django.db import connection
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SHOW COLUMNS FROM ecommerce_productvariant LIKE 'variant_type'")
                    has_variant_type_field = cursor.fetchone() is not None
            except Exception:
                has_variant_type_field = False
            
            if has_variant_type_field:
                # Get ring sizes, earring hoop sizes, and zodiac signs
                ring_size_variants = product.variants.filter(variant_type='ring_size').order_by('size')
                earring_hoop_variants = product.variants.filter(variant_type='earring_hoop_size').order_by('size')
                zodiac_variants = product.variants.filter(variant_type='zodiac_sign').order_by('size')
                # Combine all types, prioritizing ring sizes first, then earring hoop sizes, then zodiac signs
                variants = list(ring_size_variants) + list(earring_hoop_variants) + list(zodiac_variants)
                if zodiac_variants.exists() and not ring_size_variants.exists() and not earring_hoop_variants.exists():
                    variant_type = 'zodiac_sign'
                elif earring_hoop_variants.exists() and not ring_size_variants.exists():
                    variant_type = 'earring_hoop_size'
                else:
                    variant_type = 'ring_size'
            else:
                # Fallback: if variant_type doesn't exist, get all variants (assuming they're ring sizes)
                variants = list(product.variants.all().order_by('size'))
                variant_type = 'ring_size'
            has_variants = len(variants) > 0
            
            # Handle different actions
            if action == 'view':
                # Just return current stock info
                from django.urls import reverse
                product_url = reverse('admin:ecommerce_product_change', args=[product.id])
                
                # If product has variants, return variant information
                variants_data = []
                if has_variants:
                    for v in variants:
                        # Get display name for zodiac signs
                        display_name = v.size or ''
                        if has_variant_type_field and hasattr(v, 'variant_type') and v.variant_type == 'zodiac_sign':
                            try:
                                display_name = v.display_name or v.size or ''
                            except Exception:
                                display_name = v.size or ''
                        
                        variants_data.append({
                            'id': v.id,
                            'size': v.size,
                            'display_name': display_name,
                            'stock': v.stock,
                            'sku': v.sku or '',
                            'variant_type': getattr(v, 'variant_type', 'ring_size') if has_variant_type_field else 'ring_size'
                        })
                
                return JsonResponse({
                    'success': True,
                    'action': 'view',
                    'product_id': product.id,
                    'product_name': product.name,
                    'product_url': product_url,
                    'variant_size': variant.size if variant else None,
                    'current_stock': current_stock,
                    'quantity': 0,
                    'has_variants': has_variants,
                    'variant_type': variant_type if has_variants else None,
                    'variants': variants_data,
                    'message': f'Current stock: {current_stock}'
                })
            
            elif action == 'add':
                # Check if we need to handle variant_id from request
                variant_id = request.POST.get('variant_id', None)
                target_variant = None
                
                if variant_id:
                    try:
                        target_variant = ProductVariant.objects.get(id=variant_id, product=product)
                    except ProductVariant.DoesNotExist:
                        pass
                elif variant:
                    target_variant = variant
                
                # Increase stock by quantity
                with transaction.atomic():
                    if target_variant:
                        target_variant.stock += quantity
                        target_variant.save(update_fields=['stock'])
                        new_stock = target_variant.stock
                        variant_size = target_variant.size
                    else:
                        product.stock += quantity
                        product.save(update_fields=['stock'])
                        new_stock = product.stock
                        variant_size = None
                
                # Get updated variants list
                variants_data = []
                try:
                    variants = product.variants.filter(variant_type='ring_size').order_by('size')
                except Exception:
                    # Fallback: if variant_type doesn't exist, get all variants
                    variants = product.variants.all().order_by('size')
                for v in variants:
                    variants_data.append({
                        'id': v.id,
                        'size': v.size,
                        'stock': v.stock,
                        'sku': v.sku or ''
                    })
                
                from django.urls import reverse
                product_url = reverse('admin:ecommerce_product_change', args=[product.id])
                return JsonResponse({
                    'success': True,
                    'action': 'add',
                    'product_id': product.id,
                    'product_name': product.name,
                    'product_url': product_url,
                    'variant_size': variant_size,
                    'current_stock': current_stock,
                    'new_stock': new_stock,
                    'quantity': quantity,
                    'has_variants': variants.exists(),
                    'variants': variants_data,
                    'message': f'Stock increased by {quantity}! New stock: {new_stock}'
                })
            
            elif action == 'remove':
                # Check if we need to handle variant_id from request
                variant_id = request.POST.get('variant_id', None)
                target_variant = None
                
                if variant_id:
                    try:
                        target_variant = ProductVariant.objects.get(id=variant_id, product=product)
                    except ProductVariant.DoesNotExist:
                        pass
                elif variant:
                    target_variant = variant
                
                # Decrease stock by quantity
                with transaction.atomic():
                    if target_variant:
                        if target_variant.stock >= quantity:
                            target_variant.stock -= quantity
                            target_variant.save(update_fields=['stock'])
                            new_stock = target_variant.stock
                            variant_size = target_variant.size
                        else:
                            return JsonResponse({
                                'success': False,
                                'error': f'Product "{product.name}" (Size: {target_variant.size}) has only {target_variant.stock} in stock, cannot remove {quantity}!'
                            }, status=400)
                    else:
                        if product.stock >= quantity:
                            product.stock -= quantity
                            product.save(update_fields=['stock'])
                            new_stock = product.stock
                            variant_size = None
                        else:
                            return JsonResponse({
                                'success': False,
                                'error': f'Product "{product.name}" has only {product.stock} in stock, cannot remove {quantity}!'
                            }, status=400)
                
                # Get updated variants list
                variants_data = []
                from django.db import connection
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SHOW COLUMNS FROM ecommerce_productvariant LIKE 'variant_type'")
                        has_variant_type_field = cursor.fetchone() is not None
                except Exception:
                    has_variant_type_field = False
                
                if has_variant_type_field:
                    ring_size_variants = product.variants.filter(variant_type='ring_size').order_by('size')
                    zodiac_variants = product.variants.filter(variant_type='zodiac_sign').order_by('size')
                    variants = list(ring_size_variants) + list(zodiac_variants)
                else:
                    variants = list(product.variants.all().order_by('size'))
                
                for v in variants:
                    display_name = v.size or ''
                    if has_variant_type_field and hasattr(v, 'variant_type') and v.variant_type == 'zodiac_sign':
                        try:
                            display_name = v.display_name or v.size or ''
                        except Exception:
                            display_name = v.size or ''
                    
                    variants_data.append({
                        'id': v.id,
                        'size': v.size,
                        'display_name': display_name,
                        'stock': v.stock,
                        'sku': v.sku or '',
                        'variant_type': getattr(v, 'variant_type', 'ring_size') if has_variant_type_field else 'ring_size'
                    })
                
                from django.urls import reverse
                product_url = reverse('admin:ecommerce_product_change', args=[product.id])
                
                # Determine variant type for response
                response_variant_type = 'ring_size'
                if has_variant_type_field:
                    if product.variants.filter(variant_type='zodiac_sign').exists():
                        response_variant_type = 'zodiac_sign'
                
                return JsonResponse({
                    'success': True,
                    'action': 'remove',
                    'product_id': product.id,
                    'product_name': product.name,
                    'product_url': product_url,
                    'variant_size': variant_size,
                    'current_stock': current_stock,
                    'new_stock': new_stock,
                    'quantity': quantity,
                    'has_variants': len(variants) > 0,
                    'variant_type': response_variant_type if len(variants) > 0 else None,
                    'variants': variants_data,
                    'message': f'Stock decreased by {quantity}! New stock: {new_stock}'
                })
            else:
                return JsonResponse({
                    'success': False,
                    'error': f'Invalid action: {action}'
                }, status=400)
        
        # GET request - show scanner page
        return render(request, 'admin/inventory_scanner.html', {
            'title': 'Inventory Scanner - View, Add & Remove Stock'
        })


@admin.register(BlogPost)
class BlogPostAdmin(admin.ModelAdmin):
    list_display = ('title', 'order', 'is_published', 'created_at', 'has_image', 'has_video_file', 'has_video_url')
    list_filter = ('is_published', 'created_at')
    search_fields = ('title', 'content')
    prepopulated_fields = {'slug': ('title',)}
    ordering = ('order', '-created_at')
    fieldsets = (
        ('Basic Information', {
            'fields': ('title', 'slug', 'excerpt', 'order', 'is_published')
        }),
        ('Content', {
            'fields': ('content',)
        }),
        ('Media', {
            'fields': ('image', 'video_file', 'video_url'),
            'description': 'Add an image and/or video to enhance your blog post. You can upload a video file (MP4, WebM, OGG) or provide a YouTube/Vimeo URL. If both are provided, uploaded video takes priority.'
        }),
    )
    
    def has_image(self, obj):
        return bool(obj.image)
    has_image.boolean = True
    has_image.short_description = 'Has Image'
    
    def has_video_file(self, obj):
        return bool(obj.video_file)
    has_video_file.boolean = True
    has_video_file.short_description = 'Has Video File'
    
    def has_video_url(self, obj):
        return bool(obj.video_url)
    has_video_url.boolean = True
    has_video_url.short_description = 'Has Video URL'


@admin.register(CustomerReview)
class CustomerReviewAdmin(admin.ModelAdmin):
    list_display = (
        "customer_name",
        "rating",
        "related_product",
        "order",
        "is_published",
        "has_image",
        "created_at",
        "review_preview",
    )
    list_filter = ("is_published", "created_at")
    list_editable = ("order", "is_published")
    search_fields = ("customer_name", "review")
    ordering = ("order", "-created_at")
    autocomplete_fields = ("related_product",)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "customer_name",
                    "rating",
                    "review",
                    "related_product",
                    "image",
                    "order",
                    "is_published",
                )
            },
        ),
    )

    def has_image(self, obj):
        return bool(obj.image)

    has_image.boolean = True
    has_image.short_description = "Photo"

    def review_preview(self, obj):
        t = (obj.review or "").strip()
        if len(t) > 60:
            return t[:60] + "…"
        return t

    review_preview.short_description = "Review preview"


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('get_indented_name', 'slug', 'get_subcategories_count', 'get_products_count')
    list_filter = ('parent',)
    search_fields = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}
    inlines = [CategorySubcategoryInline]
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'slug', 'parent', 'image'),
            'description': 'To add sub-categories, use the "Sub-categories" section below. You can create a new parent category by clicking the "+" button next to the Parent field. Add an image for sub-categories to display them as cards on the parent category page.'
        }),
    )
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # Order by parent name first (None comes first), then by category name
        # This groups sub-categories under their parent categories
        return qs.select_related('parent').prefetch_related('subcategories').order_by(
            Case(
                When(parent=None, then=Value(0)),
                default=Value(1)
            ),
            'parent__name',
            'name'
        )
    
    @admin.display(description='Category Name')
    def get_indented_name(self, obj):
        """Display category name with indentation for sub-categories."""
        if obj.parent:
            # Show sub-category with indentation (2-3 spaces)
            return format_html('&nbsp;&nbsp;&nbsp;{}', obj.name.upper())
        return obj.name.upper()
    
    @admin.display(description='Sub-categories')
    def get_subcategories_count(self, obj):
        count = obj.subcategories.count()
        if count > 0:
            return f"{count} sub-category{'ies' if count != 1 else ''}"
        return "-"
    
    @admin.display(description='Products')
    def get_products_count(self, obj):
        count = obj.products.count()
        if count > 0:
            return count
        return "-"
    
    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        # Prevent circular references - exclude current category and its descendants from parent choices
        if obj:
            def get_descendant_ids(category):
                ids = [category.id]
                for subcat in category.subcategories.all():
                    ids.extend(get_descendant_ids(subcat))
                return ids
            
            excluded_ids = get_descendant_ids(obj)
            form.base_fields['parent'].queryset = Category.objects.exclude(id__in=excluded_ids)
        return form
admin.site.register(ProductImage)
admin.site.register(CartItem)


@admin.register(ProductReview)
class ProductReviewAdmin(admin.ModelAdmin):
    list_display = (
        "product",
        "rating",
        "user",
        "session_key_short",
        "reviewer_name",
        "comment_preview",
        "comment_approved",
        "created_at",
    )
    list_filter = ("comment_approved", "rating", "created_at")
    list_editable = ("comment_approved",)
    search_fields = ("product__name", "comment", "reviewer_name", "user__username", "session_key")
    readonly_fields = ("created_at",)
    ordering = ("-created_at",)
    autocomplete_fields = ("product",)

    @admin.display(description="Comment")
    def comment_preview(self, obj):
        t = (obj.comment or "").strip()
        if not t:
            return "—"
        return (t[:100] + "…") if len(t) > 100 else t

    @admin.display(description="Session")
    def session_key_short(self, obj):
        sk = (obj.session_key or "").strip()
        if not sk:
            return "—"
        return f"{sk[:6]}…" if len(sk) > 8 else sk


def order_item_variant_summary(obj: OrderItem) -> str:
    """Human-readable variant line for admin (ring size, zodiac, hoop, SKU)."""
    v = obj.variant
    if v is None:
        return "—"
    try:
        kind = v.get_variant_type_display()
    except Exception:
        kind = getattr(v, "variant_type", "") or ""
    try:
        detail = (v.display_name or "").strip() or (v.size or "").strip()
    except Exception:
        detail = (getattr(v, "size", None) or "").strip()
    bits = [kind] if kind else []
    if detail:
        bits.append(detail)
    sku = (getattr(v, "sku", None) or "").strip()
    if sku:
        bits.append(f"SKU {sku}")
    return " · ".join(bits) if bits else "—"


class OrderItemInline(admin.TabularInline):
    """Line items on the order change page — shows variant/size/zodiac clearly."""

    model = OrderItem
    extra = 0
    can_delete = False
    fields = ("product", "variant_summary", "quantity")
    readonly_fields = ("product", "variant_summary", "quantity")
    ordering = ("id",)
    verbose_name = "Order line"
    verbose_name_plural = "Order lines (products & variants)"

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="Variant / option")
    def variant_summary(self, obj):
        return order_item_variant_summary(obj)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("product", "variant")


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "product", "variant_summary", "quantity")
    list_select_related = ("order", "product", "variant")
    search_fields = (
        "order__id",
        "product__name",
        "product__serial_number",
        "variant__size",
        "variant__sku",
    )
    list_filter = ("order__created_at",)
    fields = ("order", "product", "variant", "variant_summary", "quantity")
    readonly_fields = ("order", "product", "variant_summary")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "variant":
            oid = request.resolver_match.kwargs.get("object_id")
            if oid:
                try:
                    oi = OrderItem.objects.select_related("product").get(pk=oid)
                    kwargs["queryset"] = ProductVariant.objects.filter(
                        product_id=oi.product_id
                    ).order_by("variant_type", "size")
                except OrderItem.DoesNotExist:
                    pass
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.display(description="Variant / option")
    def variant_summary(self, obj):
        return order_item_variant_summary(obj)


@admin.register(Favorite)
class FavoriteAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'product', 'product_serial_number', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('user__username', 'user__email', 'product__name', 'product__sku', 'product__serial_number')
    list_select_related = ('user', 'product')
    readonly_fields = ('created_at',)
    date_hierarchy = 'created_at'
    
    @admin.display(description='Serial Number')
    def product_serial_number(self, obj):
        if obj.product:
            return obj.product.serial_number or '-'
        return '-'


admin.site.register(Discount)
admin.site.register(ShippingOption)



@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    inlines = [OrderItemInline]

    list_display = (
        "id",
        "test_label_link",
        "order_status",
        "created_at",
        "full_name",
        "email",
        "total_price",
        "shipping_option",
        "shipping_carrier",
        "tracking_number",
        "coupon_code",
        "items_count",
        "print_label_link",
    )
    
    class Media:
        css = {
            'all': ('admin/css/order_admin.css',)
        }
        js = ('admin/js/order_admin.js',)

    search_fields = ("id", "full_name", "email", "phone", "address", "city", "postal_code", "tracking_number", "awb")
    list_filter = ("is_shipped", "shipping_option", "shipping_carrier", "coupon", "created_at")
    list_select_related = ("shipping_option", "coupon", "user")
    readonly_fields = ("created_at", "shipment_id", "shipping_label_url", "create_label_button", "fedex_copy_paste", "shipped_at", "awb_with_print_link")
    
    fieldsets = (
        ("Order Information", {
            "fields": ("user", "email", "created_at", "total_price", "coupon")
        }),
        ("Shipping", {
            "fields": ("shipping_option", "shipping_carrier", "create_label_button", "tracking_number", "awb_with_print_link", "shipment_id", "shipping_label_url", "is_shipped", "shipped_at")
        }),
        ("Customer Details", {
            "fields": ("full_name", "phone", "address", "city", "postal_code", "country")
        }),
        ("FedEx Copy-Paste", {
            "fields": ("fedex_copy_paste",),
            "description": "Copy all order details formatted for FedEx portal with one click"
        }),
    )

    actions = [
        "export_orders_csv",
        "export_dpi_bulk_csv",
        "send_shipped_email",
        "print_shipping_labels",
        "create_shipping_labels",
        "group_into_single_dpi_awb",
        "mark_as_shipped",
        "mark_as_not_shipped",
    ]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(_items_count=Count("items"))

    def changelist_view(self, request, extra_context=None):
        from django.conf import settings as dj_settings
        extra_context = extra_context or {}
        extra_context['dpi_status'] = {
            'test_mode': bool(getattr(dj_settings, 'GLOBAL_MAIL_TEST_MODE', True)),
            'auto_create': bool(getattr(dj_settings, 'SHIPPING_AUTO_CREATE_LABEL', True)),
        }
        return super().changelist_view(request, extra_context=extra_context)

    def get_row_css_class(self, obj, request):
        """Add CSS class to row based on order status."""
        css_class = super().get_row_css_class(obj, request) or ""
        if obj.is_shipped:
            css_class += " admin-order-shipped"
        return css_class.strip()

    @admin.display(description="Items")
    def items_count(self, obj):
        return getattr(obj, "_items_count", 0)

    @admin.display(description="Coupon")
    def coupon_code(self, obj):
        return obj.coupon.code if obj.coupon else "-"
    
    @admin.display(description="🏷️ Label")
    def test_label_link(self, obj):
        """Create a DPI label for this order.

        Behaviour depends on ``GLOBAL_MAIL_TEST_MODE``:
          * True  → sandbox (blue "Test label", no DB changes).
          * False → production (red "Create label — REAL", persists AWB
                    & tracking to the order).
        """
        from django.urls import reverse
        try:
            url = reverse('admin:print_test_label_for_order', args=[obj.pk])
        except Exception:
            return ""
        is_sandbox = bool(getattr(settings, "GLOBAL_MAIL_TEST_MODE", True))
        if is_sandbox:
            bg = "#3b82f6"
            label = "🧪 Test label"
            title = "Sandbox — nothing saved, no real shipment"
        else:
            bg = "#dc2626"
            label = "📦 Create label"
            title = (
                "PRODUCTION — real billable shipment. Tracking + AWB will "
                "be saved to this order."
            )
        return format_html(
            '<a href="{}" target="_blank" title="{}" style="background:{};color:#fff;'
            'padding:3px 8px;border-radius:4px;text-decoration:none;font-size:11px;'
            'white-space:nowrap;">{}</a>',
            url, title, bg, label,
        )

    @admin.display(description="Status", ordering="is_shipped")
    def order_status(self, obj):
        """Display order status with green background if shipped."""
        if obj.is_shipped:
            return format_html(
                '<span style="background-color: #10b981; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;">✓ Shipped</span>'
            )
        else:
            return format_html(
                '<span style="background-color: #f3f4f6; color: #6b7280; padding: 4px 12px; border-radius: 4px;">Pending</span>'
            )
    
    def mark_as_shipped(self, request, queryset):
        """Mark selected orders as shipped."""
        from django.utils import timezone
        updated = queryset.update(is_shipped=True, shipped_at=timezone.now())
        self.message_user(
            request,
            f"✅ Marked {updated} order(s) as shipped.",
            messages.SUCCESS
        )
    mark_as_shipped.short_description = "Mark selected orders as shipped"
    
    def mark_as_not_shipped(self, request, queryset):
        """Mark selected orders as not shipped."""
        updated = queryset.update(is_shipped=False, shipped_at=None)
        self.message_user(
            request,
            f"✅ Marked {updated} order(s) as not shipped.",
            messages.SUCCESS
        )
    mark_as_not_shipped.short_description = "Mark selected orders as not shipped"

    def send_shipped_email(self, request, queryset):
        """Admin action to send 'order shipped' email to selected orders."""
        base_url = f"{request.scheme}://{request.get_host()}"
        sent_count = 0
        failed_count = 0
        
        for order in queryset:
            recipient = getattr(order, "email", None) or getattr(getattr(order, "user", None), "email", None)
            if not recipient:
                failed_count += 1
                continue
            
            if send_order_shipped_email(order, base_url):
                sent_count += 1
            else:
                failed_count += 1
        
        if sent_count > 0:
            self.message_user(
                request,
                f"✅ Sent {sent_count} order shipped emails.",
                messages.SUCCESS
            )
        if failed_count > 0:
            self.message_user(
                request,
                f"⚠️ {failed_count} emails could not be sent (missing email address or error).",
                messages.WARNING
            )
    
    send_shipped_email.short_description = "Send 'Order Shipped' email"

    def export_orders_csv(self, request, queryset):
        """Export orders to CSV with all fields needed for accounting."""
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="orders_accounting.csv"'
        writer = csv.writer(response)
        
        # CSV headers as requested by accountant
        writer.writerow([
            "Order ID",
            "Date",
            "Number of Items",
            "Country",
            "SKU",
            "Currency",
            "Order Value",
            "Shipping",
            "Shipping Discount",
            "Discount Amount",
            "Ship From",
            "Ship To",
            "Customer Name",
            "Customer Email",
            "Customer Phone",
            "City",
            "Postal Code",
            "Coupon Code",
            "Tracking Number"
        ])

        # Get shop information for "Ship From"
        shop_country = getattr(settings, 'SHOP_COUNTRY', 'BG')
        shop_city = getattr(settings, 'SHOP_CITY', 'Sofia')
        shop_address = getattr(settings, 'SHOP_ADDRESS', '')
        ship_from = f"{shop_address}, {shop_city}, {shop_country}".strip(", ")
        
        # Get currency from order (default to EUR if not set)
        
        # Get item counts and SKUs per order
        order_items_data = {}
        for order in queryset:
            items = order.items.select_related("product", "variant").all()
            total_items = sum(item.quantity for item in items)
            skus = []
            for item in items:
                # Get SKU from variant if available, otherwise from product serial_number
                sku = ""
                if item.variant and item.variant.sku:
                    sku = item.variant.sku
                elif item.product.serial_number:
                    sku = item.product.serial_number
                else:
                    sku = f"PROD-{item.product.id}"
                # Add quantity to SKU if multiple
                if item.quantity > 1:
                    sku = f"{sku} (x{item.quantity})"
                skus.append(sku)
            
                order_items_data[order.id] = {
                'count': total_items,
                'skus': "; ".join(skus) if skus else "N/A"
            }

        for o in queryset.select_related("shipping_option", "coupon"):
            # Get currency from order (default to EUR if not set)
            currency = getattr(o, 'currency', 'EUR') or 'EUR'
            # Calculate shipping cost
            shipping_cost = o.shipping_option.price if o.shipping_option else Decimal("0.00")
            
            # Calculate discount amount from coupon
            discount_amount = Decimal("0.00")
            shipping_discount = Decimal("0.00")
            if o.coupon:
                # Calculate discount based on coupon type
                if o.coupon.percent_off:
                    # Percentage discount
                    discount_amount = (o.total_price * o.coupon.percent_off / 100).quantize(Decimal("0.01"))
                elif o.coupon.amount_off:
                    # Fixed amount discount
                    discount_amount = o.coupon.amount_off
            
            # Ship To address
            ship_to = f"{o.address}, {o.city}, {o.postal_code}, {o.country or ''}".strip(", ")
            
            writer.writerow([
                o.id,  # Order ID
                o.created_at.strftime("%Y-%m-%d %H:%M:%S"),  # Date
                order_items_data.get(o.id, {}).get('count', 0),  # Number of Items
                o.country or "",  # Country
                order_items_data.get(o.id, {}).get('skus', "N/A"),  # SKU
                currency,  # Currency
                str(o.total_price),  # Order Value
                str(shipping_cost),  # Shipping
                str(shipping_discount),  # Shipping Discount (currently not tracked separately)
                str(discount_amount),  # Discount Amount
                ship_from,  # Ship From
                ship_to,  # Ship To
                o.full_name,  # Customer Name
                o.email or "",  # Customer Email
                o.phone,  # Customer Phone
                o.city,  # City
                o.postal_code,  # Postal Code
                o.coupon.code if o.coupon else "",  # Coupon Code
                o.tracking_number or "",  # Tracking Number
            ])
        return response

    export_orders_csv.short_description = "Export selected orders to CSV (Accounting)"

    # ------------------------------------------------------------------
    # DPI / Deutsche Post International bulk-dispatch CSV export
    # ------------------------------------------------------------------
    def export_dpi_bulk_csv(self, request, queryset):
        """Export selected orders to a CSV in the DPI bulk dispatch
        format.

        Column layout matches the file DPI accepts for bulk upload
        (PRODUCT, SERVICE_LEVEL, CUST_EKP, AWB, REGISTERED_BARCODE, …).
        When an order has already been shipped via DPI, its AWB and
        item barcode are included so the file is ready for re-dispatch
        or manifesting; otherwise those columns stay empty.
        """
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            'attachment; filename="dpi_prelabeled_items.csv"'
        )
        response.write("\ufeff")
        writer = csv.writer(response, delimiter=",", quoting=csv.QUOTE_MINIMAL)

        columns = [
            "PRODUCT",
            "SERVICE_LEVEL",
            "CUST_EKP",
            "AWB",
            "REGISTERED_BARCODE",
            "CUST_REF",
            "NAME",
            "RECIPIENT_PHONE",
            "RECIPIENT_EMAIL",
            "ADDRESS_LINE_1",
            "ADDRESS_LINE_2",
            "ADDRESS_LINE_3",
            "CITY",
            "STATE",
            "POSTAL_CODE",
            "DESTINATION_COUNTRY",
            "WEIGHT",
            "CONTENT_TYPE",
            "TOTAL_VALUE",
            "CURRENCY",
            "HS_CODE",
            "ORIGIN_COUNTRY",
        ]
        writer.writerow(columns)

        import re as _re

        def _sanitize_phone(raw):
            s = (raw or "").strip()
            if not s:
                return ""
            s = _re.split(
                r"(?i)(?:\s*(?:ext\.?|extension)\b|(?<=\d)\s*x\s*(?=\d)|\bx\b)",
                s,
                maxsplit=1,
            )[0]
            has_plus = s.lstrip().startswith("+")
            s = _re.sub(r"[^\d\s\.\-\(\)]", "", s)
            if has_plus:
                s = "+" + s.lstrip()
            return s.strip()[:25]

        def _short(s, n):
            s = (s or "").strip()
            return s[:n]

        ekp = str(getattr(settings, "GLOBAL_MAIL_CUSTOMER_EKP", "") or "")
        default_product = getattr(settings, "GLOBAL_MAIL_PRODUCT_CODE", "GPT") or "GPT"
        default_service = (
            getattr(settings, "GLOBAL_MAIL_SERVICE_LEVEL", "PRIORITY") or "PRIORITY"
        )
        default_currency = getattr(settings, "GLOBAL_MAIL_CURRENCY", "EUR") or "EUR"
        origin_country = getattr(settings, "SHOP_COUNTRY", "BG") or "BG"
        default_hs = getattr(settings, "GLOBAL_MAIL_DEFAULT_HS_CODE", "711311") or "711311"
        default_nature = (
            getattr(settings, "GLOBAL_MAIL_NATURE_TYPE", "SALE_GOODS") or "SALE_GOODS"
        )

        _BUILTIN_NON_EU_PRODUCT_MAP = {
            "US": "GPP", "CA": "GPP", "AU": "GPP", "NZ": "GPP", "JP": "GPP",
            "KR": "GPP", "SG": "GPP", "HK": "GPP", "CN": "GPP", "IN": "GPP",
            "BR": "GPP", "MX": "GPP", "AE": "GPP", "IL": "GPP", "ZA": "GPP",
            "TR": "GPP", "CH": "GPP", "NO": "GPP", "IS": "GPP", "GB": "GPP",
        }
        user_map = getattr(settings, "GLOBAL_MAIL_PRODUCT_MAP", {}) or {}
        product_map = {**_BUILTIN_NON_EU_PRODUCT_MAP, **user_map}

        for o in queryset.order_by("id"):
            dest = ((getattr(o, "country", "") or "BG").strip() or "BG").upper()
            if len(dest) > 2:
                _iso = {
                    "BULGARIA": "BG", "GERMANY": "DE", "UNITED KINGDOM": "GB",
                    "GREAT BRITAIN": "GB", "UK": "GB", "USA": "US",
                    "UNITED STATES": "US", "FRANCE": "FR", "ITALY": "IT",
                    "SPAIN": "ES", "NETHERLANDS": "NL", "BELGIUM": "BE",
                    "AUSTRIA": "AT", "SWITZERLAND": "CH", "POLAND": "PL",
                }
                dest = _iso.get(dest, dest[:2])

            product = product_map.get(dest) or default_product
            try:
                total_weight_kg = max(
                    sum(int(it.quantity or 1) for it in o.items.all()) * 0.5, 0.1
                )
            except Exception:
                total_weight_kg = 0.5
            total_weight_g = int(total_weight_kg * 1000)

            first_item = None
            qty_total = 0
            value_total = 0.0
            try:
                for it in o.items.all():
                    if first_item is None:
                        first_item = it
                    qty_total += int(it.quantity or 1)
                    try:
                        price = float(
                            getattr(it, "price", None)
                            or getattr(getattr(it, "product", None), "price", 0)
                            or 0
                        )
                    except Exception:
                        price = 0
                    value_total += price * int(it.quantity or 1)
            except Exception:
                pass
            try:
                order_total = float(getattr(o, "total_price", 0) or 0)
            except Exception:
                order_total = 0.0
            if order_total <= 0:
                order_total = max(round(value_total, 2), 1.0)

            content_desc = "Silver jewellery"
            if first_item is not None:
                nm = getattr(getattr(first_item, "product", None), "name", "") or ""
                if nm:
                    content_desc = _short(nm, 33)

            is_eu = dest in {
                "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR",
                "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL",
                "PL", "PT", "RO", "SK", "SI", "ES", "SE",
            }
            row = [
                product,
                default_service,
                ekp,
                getattr(o, "awb", "") or "",
                getattr(o, "tracking_number", "") or "",
                str(o.id),
                _short(getattr(o, "full_name", "") or "Recipient", 35),
                _sanitize_phone(getattr(o, "phone", "") or ""),
                _short(getattr(o, "email", "") or "", 80),
                _short(getattr(o, "address", "") or "", 40),
                "",
                "",
                _short(getattr(o, "city", "") or "", 30),
                "",
                _short(getattr(o, "postal_code", "") or "", 15),
                dest,
                total_weight_g,
                default_nature,
                "" if is_eu else f"{round(order_total, 2):.2f}",
                default_currency,
                "" if is_eu else default_hs,
                origin_country,
            ]
            writer.writerow(row)

        return response

    export_dpi_bulk_csv.short_description = (
        "📄 Export to DPI prelabeled items CSV"
    )

    # ------------------------------------------------------------------
    # Etsy / Amazon address text → DPI CSV
    # ------------------------------------------------------------------
    def etsy_to_dpi_csv_view(self, request):
        """Paste raw address blocks (one recipient per block, separated
        by a blank line) and download a DPI-ready CSV.

        Matches the layout of the in-browser "DP Address → CSV" tool:
        Name, Street, HouseNo, Address2, Postcode, City, Country,
        Weight_g, ProductCode, Email, Phone, Reference, EKP,
        CN22_Required, HSCode, ItemValue, Currency, Description, Qty,
        OriginCountry.
        """
        import re as _re

        _COUNTRY_MAP = {
            "bulgaria": "BG", "българия": "BG",
            "germany": "DE", "deutschland": "DE", "германия": "DE",
            "united kingdom": "GB", "great britain": "GB", "uk": "GB",
            "england": "GB", "scotland": "GB", "wales": "GB",
            "usa": "US", "u.s.a.": "US", "united states": "US",
            "united states of america": "US", "america": "US",
            "france": "FR", "italy": "IT", "italia": "IT",
            "spain": "ES", "españa": "ES", "espana": "ES",
            "netherlands": "NL", "the netherlands": "NL",
            "holland": "NL", "nederland": "NL",
            "belgium": "BE", "belgië": "BE", "belgique": "BE",
            "austria": "AT", "österreich": "AT", "osterreich": "AT",
            "switzerland": "CH", "schweiz": "CH", "suisse": "CH",
            "poland": "PL", "polska": "PL",
            "portugal": "PT", "greece": "GR", "ελλάδα": "GR",
            "sweden": "SE", "sverige": "SE",
            "norway": "NO", "norge": "NO",
            "denmark": "DK", "danmark": "DK",
            "finland": "FI", "suomi": "FI",
            "ireland": "IE", "éire": "IE",
            "czech republic": "CZ", "czechia": "CZ", "česko": "CZ",
            "slovakia": "SK", "slovensko": "SK",
            "hungary": "HU", "magyarország": "HU",
            "romania": "RO", "românia": "RO",
            "croatia": "HR", "hrvatska": "HR",
            "slovenia": "SI", "slovenija": "SI",
            "luxembourg": "LU", "estonia": "EE", "eesti": "EE",
            "latvia": "LV", "latvija": "LV",
            "lithuania": "LT", "lietuva": "LT",
            "malta": "MT", "cyprus": "CY",
            "canada": "CA", "australia": "AU", "new zealand": "NZ",
            "japan": "JP", "singapore": "SG", "hong kong": "HK",
            "china": "CN", "india": "IN", "brazil": "BR", "brasil": "BR",
            "mexico": "MX", "méxico": "MX",
            "united arab emirates": "AE", "uae": "AE",
            "israel": "IL", "south africa": "ZA",
            "turkey": "TR", "türkiye": "TR",
            "iceland": "IS", "island": "IS",
            "serbia": "RS", "srbija": "RS",
            "ukraine": "UA", "україна": "UA",
        }
        _EU = {
            "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR",
            "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL",
            "PL", "PT", "RO", "SK", "SI", "ES", "SE",
        }

        def _parse_country(line):
            if not line:
                return None
            s = line.strip().rstrip(".,")
            key = s.lower()
            if key in _COUNTRY_MAP:
                return _COUNTRY_MAP[key]
            if _re.fullmatch(r"[A-Za-z]{2}", s):
                return s.upper()
            return None

        def _parse_postcode_city(line):
            """Return (postcode, city, state) or None."""
            s = line.strip().rstrip(",")
            m = _re.match(
                r"^(.+?),\s*([A-Za-z]{2})\s+(\d{5}(?:-\d{4})?)$", s
            )
            if m:
                return (
                    m.group(3),
                    m.group(1).strip().title(),
                    m.group(2).upper(),
                )
            m = _re.match(r"^(\d{4}\s?[A-Z]{2})\s+(.+)$", s)
            if m:
                return m.group(1), m.group(2).strip(), ""
            m = _re.match(r"^(\d{4,6})\s+(.+?)\s+([A-Z]{2})$", s)
            if m:
                return m.group(1), m.group(2).strip().title(), m.group(3)
            m = _re.match(r"^(\d{4,6})\s+(.+)$", s)
            if m:
                return m.group(1), m.group(2).strip(), ""
            m = _re.match(
                r"^([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\s+(.+)$", s,
            )
            if m:
                return m.group(1), m.group(2).strip(), ""
            m = _re.match(
                r"^(.+?),?\s+([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})$", s,
            )
            if m:
                return m.group(2), m.group(1).strip(), ""
            return None

        def _parse_phone(line):
            if not line:
                return None
            s = line.strip()
            s = _re.sub(
                r"(?i)^(tel|tel\.|phone|mobile|gsm|тел|тел\.)\s*:?\s*",
                "",
                s,
            )
            digits_only = _re.sub(r"[^\d]", "", s)
            if len(digits_only) < 7:
                return None
            has_plus = s.lstrip().startswith("+")
            cleaned = _re.sub(r"[^\d\s\.\-\(\)]", "", s)
            if has_plus:
                cleaned = "+" + cleaned.lstrip()
            return cleaned.strip()[:25]

        def _looks_like_phone(line):
            return _parse_phone(line) is not None

        def _split_street(line):
            s = line.strip().rstrip(",")
            m = _re.match(
                r"^(.+?)\s+(\d+[A-Za-z]?(?:[\-/]\d+[A-Za-z]?)?)$", s,
            )
            if m:
                return m.group(1), m.group(2)
            m = _re.match(r"^(\d+[A-Za-z]?)\s+(.+)$", s)
            if m:
                return m.group(2), m.group(1)
            return s, ""

        def _parse_block(raw):
            lines = [
                ln.strip().rstrip(",")
                for ln in raw.strip().split("\n")
                if ln.strip()
            ]
            if not lines:
                return None
            if len(lines) == 1 and "," in lines[0]:
                parts = [p.strip() for p in lines[0].split(",") if p.strip()]
                lines = parts
            name = lines[0]
            rest = lines[1:]

            email = ""
            for i in range(len(rest) - 1, -1, -1):
                if "@" in rest[i] and " " not in rest[i].strip():
                    email = rest.pop(i).strip()
                    break

            phone = ""
            for i in range(len(rest) - 1, -1, -1):
                p = _parse_phone(rest[i])
                if p and _looks_like_phone(rest[i]):
                    digits = _re.sub(r"[^\d]", "", rest[i])
                    if len(digits) >= 7 and len(digits) / max(len(rest[i]), 1) > 0.4:
                        phone = p
                        rest.pop(i)
                        break

            country = ""
            for i in range(len(rest) - 1, -1, -1):
                c = _parse_country(rest[i])
                if c:
                    country = c
                    rest.pop(i)
                    break

            postcode, city, state = "", "", ""
            for i in range(len(rest) - 1, -1, -1):
                res = _parse_postcode_city(rest[i])
                if res:
                    postcode, city, state = res
                    rest.pop(i)
                    break

            street, house_no, address2 = "", "", ""
            if len(rest) == 1:
                street, house_no = _split_street(rest[0])
            elif len(rest) == 2:
                if _re.fullmatch(
                    r"\d+[A-Za-z]?(?:[\-/]\d+[A-Za-z]?)?", rest[1].strip()
                ):
                    street = rest[0].strip()
                    house_no = rest[1].strip()
                else:
                    street, house_no = _split_street(rest[0])
                    address2 = rest[1].strip()
            elif len(rest) >= 3:
                street, house_no = _split_street(rest[0])
                address2 = " ".join(r.strip() for r in rest[1:])

            return {
                "name": name,
                "street": street,
                "house_no": house_no,
                "address2": address2,
                "postcode": postcode,
                "city": city,
                "state": state,
                "country": country,
                "phone": phone,
                "email": email,
            }

        default_ekp = str(
            getattr(settings, "GLOBAL_MAIL_CUSTOMER_EKP", "") or "316276595"
        )
        default_product = (
            getattr(settings, "GLOBAL_MAIL_PRODUCT_CODE", "PRIO") or "PRIO"
        )
        default_hs = (
            getattr(settings, "GLOBAL_MAIL_DEFAULT_HS_CODE", "7113.11")
            or "7113.11"
        )
        default_currency = (
            getattr(settings, "GLOBAL_MAIL_CURRENCY", "EUR") or "EUR"
        )
        default_origin = getattr(settings, "SHOP_COUNTRY", "BG") or "BG"

        service_level = (
            getattr(settings, "GLOBAL_MAIL_SERVICE_LEVEL", "PRIORITY")
            or "PRIORITY"
        )
        content_type_default = (
            getattr(settings, "GLOBAL_MAIL_CONTENT_TYPE", "SALE_GOODS")
            or "SALE_GOODS"
        )

        action = (request.POST.get("action") or "").strip() if request.method == "POST" else ""

        # ------ STEP 2: user has edited the preview table → emit CSV ------
        if request.method == "POST" and action == "export":
            try:
                n = int(request.POST.get("rows_count") or 0)
            except ValueError:
                n = 0
            ekp = request.POST.get("ekp", default_ekp).strip() or default_ekp
            product = (
                request.POST.get("product", default_product).strip()
                or default_product
            )
            hs_code = (
                request.POST.get("hs_code", default_hs).strip() or default_hs
            )
            currency = (
                request.POST.get("currency", default_currency).strip()
                or default_currency
            )
            origin = (
                request.POST.get("origin", default_origin).strip().upper()
                or default_origin
            )
            default_desc = (
                request.POST.get("description", "Silver jewellery").strip()
                or "Silver jewellery"
            )

            response = HttpResponse(content_type="text/csv; charset=utf-8")
            response["Content-Disposition"] = (
                'attachment; filename="dpi_prelabeled_items.csv"'
            )
            response.write("\ufeff")
            writer = csv.writer(
                response, delimiter=",", quoting=csv.QUOTE_MINIMAL
            )
            writer.writerow([
                "PRODUCT", "SERVICE_LEVEL", "CUST_EKP", "AWB",
                "REGISTERED_BARCODE", "CUST_REF", "NAME",
                "RECIPIENT_PHONE", "RECIPIENT_EMAIL",
                "ADDRESS_LINE_1", "ADDRESS_LINE_2", "ADDRESS_LINE_3",
                "CITY", "STATE", "POSTAL_CODE", "DESTINATION_COUNTRY",
                "WEIGHT", "CONTENT_TYPE", "TOTAL_VALUE", "CURRENCY",
                "HS_CODE", "ORIGIN_COUNTRY",
            ])

            exported = 0
            for i in range(n):
                name = (request.POST.get(f"row_{i}_name") or "").strip()
                if not name:
                    continue
                country = (
                    request.POST.get(f"row_{i}_country") or ""
                ).strip().upper()
                if not country:
                    continue
                street = (request.POST.get(f"row_{i}_street") or "").strip()
                address2 = (request.POST.get(f"row_{i}_address2") or "").strip()
                city = (request.POST.get(f"row_{i}_city") or "").strip()
                state = (request.POST.get(f"row_{i}_state") or "").strip()
                postcode = (
                    request.POST.get(f"row_{i}_postcode") or ""
                ).strip()
                phone_raw = (request.POST.get(f"row_{i}_phone") or "").strip()
                phone = _parse_phone(phone_raw) or phone_raw
                email = (request.POST.get(f"row_{i}_email") or "").strip()
                weight = (request.POST.get(f"row_{i}_weight") or "").strip()
                cust_ref = (request.POST.get(f"row_{i}_ref") or "").strip()
                item_value = (
                    request.POST.get(f"row_{i}_value") or ""
                ).strip()

                is_eu = country in _EU
                writer.writerow([
                    product, service_level, ekp, "", "", cust_ref,
                    name, phone, email,
                    street, address2, "",
                    city, state, postcode, country,
                    weight, content_type_default,
                    "" if is_eu else item_value,
                    currency,
                    "" if is_eu else hs_code,
                    origin,
                ])
                exported += 1

            messages.success(request, f"Exported {exported} row(s).")
            return response

        # ------ STEP 1: user pasted addresses → render editable preview ---
        if request.method == "POST" and action in ("preview", "clear", ""):
            text = request.POST.get("addresses", "") or ""
            ekp = request.POST.get("ekp", default_ekp).strip() or default_ekp
            product = (
                request.POST.get("product", default_product).strip()
                or default_product
            )
            hs_code = (
                request.POST.get("hs_code", default_hs).strip() or default_hs
            )
            currency = (
                request.POST.get("currency", default_currency).strip()
                or default_currency
            )
            origin = (
                request.POST.get("origin", default_origin).strip().upper()
                or default_origin
            )
            default_weight = request.POST.get("weight", "80").strip() or "80"
            default_desc = (
                request.POST.get("description", "Silver jewellery").strip()
                or "Silver jewellery"
            )
            default_value = request.POST.get("item_value", "").strip()
            default_email = request.POST.get("default_email", "").strip()
            default_phone_raw = request.POST.get("default_phone", "").strip()
            default_phone = (
                _parse_phone(default_phone_raw) or default_phone_raw
            )
            ref_prefix = (
                request.POST.get("ref_prefix", "ETSY").strip() or "ETSY"
            )
            custom_refs_raw = request.POST.get("custom_refs", "") or ""
            custom_refs = [
                ln.strip()
                for ln in custom_refs_raw.splitlines()
                if ln.strip()
            ]

            # Keep previously-parsed rows submitted via the hidden
            # `row_N_*` inputs so they are not lost when the user
            # pastes more addresses. A "Clear list" submit drops them.
            rows = []
            if action != "clear":
                try:
                    existing_n = int(request.POST.get("rows_count") or 0)
                except ValueError:
                    existing_n = 0
                for i in range(existing_n):
                    name = (request.POST.get(f"row_{i}_name") or "").strip()
                    if not name:
                        continue
                    country = (
                        request.POST.get(f"row_{i}_country") or ""
                    ).strip().upper()
                    if not country:
                        continue
                    rows.append({
                        "name": name,
                        "street": (
                            request.POST.get(f"row_{i}_street") or ""
                        ).strip(),
                        "address2": (
                            request.POST.get(f"row_{i}_address2") or ""
                        ).strip(),
                        "city": (
                            request.POST.get(f"row_{i}_city") or ""
                        ).strip(),
                        "state": (
                            request.POST.get(f"row_{i}_state") or ""
                        ).strip(),
                        "postcode": (
                            request.POST.get(f"row_{i}_postcode") or ""
                        ).strip(),
                        "country": country,
                        "is_eu": country in _EU,
                        "phone": (
                            request.POST.get(f"row_{i}_phone") or ""
                        ).strip(),
                        "email": (
                            request.POST.get(f"row_{i}_email") or ""
                        ).strip(),
                        "weight": (
                            request.POST.get(f"row_{i}_weight") or ""
                        ).strip() or default_weight,
                        "ref": (
                            request.POST.get(f"row_{i}_ref") or ""
                        ).strip(),
                        "item_value": (
                            request.POST.get(f"row_{i}_value") or ""
                        ).strip(),
                    })

            blocks = [b for b in _re.split(r"\n\s*\n", text) if b.strip()]
            skipped = 0
            starting_idx = len(rows)
            for idx, b in enumerate(blocks):
                parsed = _parse_block(b)
                if not parsed or not parsed["country"]:
                    skipped += 1
                    continue
                street_full = (
                    f"{parsed['street']} {parsed['house_no']}".strip()
                    if parsed["house_no"]
                    else parsed["street"]
                )
                new_idx = len(rows) - starting_idx
                if new_idx < len(custom_refs) and custom_refs[new_idx]:
                    cust_ref = custom_refs[new_idx]
                else:
                    cust_ref = f"{ref_prefix}-{len(rows) + 1}"
                rows.append({
                    "name": parsed["name"],
                    "street": street_full,
                    "address2": parsed["address2"],
                    "city": parsed["city"],
                    "state": parsed["state"],
                    "postcode": parsed["postcode"],
                    "country": parsed["country"],
                    "is_eu": parsed["country"] in _EU,
                    "phone": parsed["phone"] or default_phone,
                    "email": parsed["email"] or default_email,
                    "weight": default_weight,
                    "ref": cust_ref,
                    "item_value": default_value,
                })

            from django.template.response import TemplateResponse
            context = {
                **self.admin_site.each_context(request),
                "title": "Etsy/Amazon addresses → DPI CSV",
                "rows": rows,
                "skipped": skipped,
                "ekp": ekp,
                "product": product,
                "hs_code": hs_code,
                "currency": currency,
                "origin": origin,
                "description": default_desc,
                "addresses_raw": text,
                "ref_prefix": ref_prefix,
                "default_weight_val": default_weight,
                "default_shop_email": default_email
                    or (getattr(settings, "SHOP_EMAIL", "") or ""),
                "default_shop_phone": default_phone
                    or (getattr(settings, "SHOP_PHONE", "") or ""),
                "default_ekp": ekp,
                "default_product": product,
                "default_hs": hs_code,
                "default_currency": currency,
                "default_origin": origin,
                "default_description": default_desc,
                "default_qty": "1",
                "default_value_val": default_value,
                "custom_refs_raw": custom_refs_raw,
                "opts": self.model._meta,
            }
            return TemplateResponse(
                request,
                "admin/ecommerce/order/etsy_to_dpi_csv.html",
                context,
            )

        # ------ STEP 0: blank form ----------------------------------------
        from django.template.response import TemplateResponse
        context = {
            **self.admin_site.each_context(request),
            "title": "Etsy/Amazon addresses → DPI CSV",
            "default_ekp": default_ekp,
            "default_product": default_product,
            "default_hs": default_hs,
            "default_currency": default_currency,
            "default_origin": default_origin,
            "default_description": "Silver jewellery",
            "default_qty": "1",
            "default_shop_email": getattr(settings, "SHOP_EMAIL", "") or "",
            "default_shop_phone": getattr(settings, "SHOP_PHONE", "") or "",
            "default_weight_val": "80",
            "default_value_val": "",
            "ref_prefix": "ETSY",
            "custom_refs_raw": "",
            "addresses_raw": "",
            "rows": [],
            "opts": self.model._meta,
        }
        return TemplateResponse(
            request,
            "admin/ecommerce/order/etsy_to_dpi_csv.html",
            context,
        )

    def save_model(self, request, obj, form, change):
        """Override save to auto-create shipping label for new orders."""
        is_new = not change  # change=False means it's a new object
        super().save_model(request, obj, form, change)
        
        # Auto-create shipping label for new orders if carrier is configured
        if is_new and obj.shipping_carrier:
            from ecommerce.utils.shipping import create_shipping_label
            label_data = create_shipping_label(obj, obj.shipping_carrier)
            
            if label_data:
                # Update order with tracking info
                obj.tracking_number = label_data.get('tracking_number')
                obj.shipping_label_url = label_data.get('label_url')
                obj.shipment_id = label_data.get('shipment_id')
                obj.save(update_fields=['tracking_number', 'shipping_label_url', 'shipment_id'])
                
                from django.urls import reverse
                url = reverse('admin:print_shipping_label', args=[obj.pk])
                messages.success(request, format_html(
                    '✅ Order created! Shipping label generated. <a href="{}" target="_blank" style="color: #667eea; font-weight: bold;">🖨️ Print Label</a> | Tracking: {}',
                    url, obj.tracking_number or 'N/A'
                ))
            else:
                from django.urls import reverse
                url = reverse('admin:print_shipping_label', args=[obj.pk])
                messages.warning(request, format_html(
                    '⚠️ Order created but shipping label could not be generated. <a href="{}" target="_blank">Try manual print</a>',
                    url
                ))
        elif is_new:
            from django.urls import reverse
            url = reverse('admin:print_shipping_label', args=[obj.pk])
            messages.info(request, format_html(
                '✅ Order created! <a href="{}" target="_blank" style="color: #667eea; font-weight: bold;">🖨️ Print Shipping Label</a>',
                url
            ))
    
    @admin.display(description="Label")
    def print_label_link(self, obj):
        """Add a link to print shipping label for this order."""
        from django.urls import reverse
        url = reverse('admin:print_shipping_label', args=[obj.pk])
        return format_html('<a href="{}" target="_blank" style="color: #667eea; font-weight: bold;">🖨️ Print</a>', url)
    print_label_link.short_description = "Label"

    @admin.display(description="AWB (DPI Airwaybill)")
    def awb_with_print_link(self, obj):
        """Show the AWB number plus a button to fetch & print the AWB PDF."""
        from django.urls import reverse

        awb = (getattr(obj, "awb", "") or "").strip()
        if not awb:
            return format_html(
                '<span style="color:#94a3b8;font-size:13px;">— (none yet; '
                'print a DPI label first so DPI returns an AWB number)</span>'
            )
        try:
            url = reverse("admin:print_awb_by_number") + f"?awb={awb}"
        except Exception:
            return awb
        return format_html(
            '<code style="font-size:13px;font-weight:600;">{}</code> '
            '&nbsp; <a href="{}" target="_blank" '
            'style="background:#7c3aed;color:#fff;padding:3px 10px;border-radius:4px;'
            'text-decoration:none;font-size:12px;font-weight:600;">'
            '🧾 Print AWB</a>',
            awb,
            url,
        )
    
    @admin.display(description="Create Label")
    def create_label_button(self, obj):
        """Add a button to create shipping label via API."""
        from django.urls import reverse
        
        if obj.shipping_label_url:
            # Label already exists
            print_url = reverse('admin:print_shipping_label', args=[obj.pk])
            return format_html(
                '<div style="margin: 10px 0;">'
                '<span style="color: green; font-weight: bold;">✅ Label Created</span><br>'
                '<a href="{}" target="_blank" style="color: #667eea; font-weight: bold; margin-top: 5px; display: inline-block;">🖨️ Print Label</a>'
                '</div>',
                print_url
            )
        elif obj.shipping_carrier:
            # Carrier selected, show create button
            create_url = reverse('admin:create_shipping_label', args=[obj.pk])
            return format_html(
                '<div style="margin: 10px 0;">'
                '<a href="{}" '
                'style="background: #667eea; color: white; padding: 8px 16px; text-decoration: none; '
                'border-radius: 4px; display: inline-block; font-weight: bold;">'
                '📦 Create Label via {} API</a>'
                '</div>',
                create_url, obj.shipping_carrier.upper()
            )
        else:
            return format_html(
                '<div style="margin: 10px 0; color: #999;">'
                '⚠️ Select shipping carrier first'
                '</div>'
            )
    create_label_button.short_description = "Create Label"
    
    @admin.display(description="FedEx Copy-Paste Data")
    def fedex_copy_paste(self, obj):
        """Format order data for easy copy-paste into FedEx portal."""
        from django.conf import settings
        
        # Truncate full_name to 35 characters (FedEx limit)
        contact_name = obj.full_name[:35] if len(obj.full_name) > 35 else obj.full_name
        
        # Split address into two fields (35 chars each)
        address1 = obj.address[:35]
        address2 = obj.address[35:70] if len(obj.address) > 35 else ''
        
        # Build bookmarklet JavaScript code with properly escaped values
        bookmarklet_data = {
            'c': contact_name.replace("'", "\\'"),
            'p': obj.phone.replace("'", "\\'"),
            'e': (obj.email or '').replace("'", "\\'"),
            'a1': address1.replace("'", "\\'"),
            'a2': address2.replace("'", "\\'"),
            'pc': obj.postal_code.replace("'", "\\'"),
            'ci': obj.city.replace("'", "\\'"),
            'co': (obj.country or '').replace("'", "\\'")
        }
        
        # Build bookmarklet URL
        bookmarklet_js = (
            "javascript:(function(){"
            "var d={c:'" + bookmarklet_data['c'] + "',p:'" + bookmarklet_data['p'] + 
            "',e:'" + bookmarklet_data['e'] + "',a1:'" + bookmarklet_data['a1'] + 
            "',a2:'" + bookmarklet_data['a2'] + "',pc:'" + bookmarklet_data['pc'] + 
            "',ci:'" + bookmarklet_data['ci'] + "',co:'" + bookmarklet_data['co'] + "'};"
            "var f=function(t){"
            "var ls=Array.from(document.querySelectorAll('label')).filter(function(l){return l.textContent.indexOf(t)!==-1;});"
            "if(ls.length){"
            "var inp=document.querySelector('input[name='+ls[0].getAttribute('for')+']')||ls[0].nextElementSibling||ls[0].closest('div').querySelector('input,select');"
            "return inp;"
            "}"
            "var ins=Array.from(document.querySelectorAll('input,select'));"
            "var fd=ins.find(function(inp){var lb=inp.closest('div,form').querySelector('label');return lb&&lb.textContent.indexOf(t)!==-1;});"
            "return fd||ins.find(function(inp){return inp.placeholder&&inp.placeholder.toLowerCase().indexOf(t.toLowerCase().substring(0,10))!==-1;});"
            "};"
            "var fill=function(t,v){if(!v)return false;var fd=f(t);if(fd){fd.value=v;fd.dispatchEvent(new Event('input',{bubbles:true}));fd.dispatchEvent(new Event('change',{bubbles:true}));return true;}return false;};"
            "var cnt=0;"
            "if(fill('ИМЕ ЗА КОНТАКТ',d.c)||fill('CONTACT NAME',d.c))cnt++;"
            "if(fill('ТЕЛЕФОНЕН НОМЕР',d.p)||fill('PHONE',d.p))cnt++;"
            "if(fill('ИМЕЙЛ',d.e)||fill('EMAIL',d.e))cnt++;"
            "if(fill('ПОЛЕ 1 ЗА АДРЕС',d.a1)||fill('ADDRESS FIELD 1',d.a1))cnt++;"
            "if(fill('ПОЛЕ 2 ЗА АДРЕС',d.a2)||fill('ADDRESS FIELD 2',d.a2))cnt++;"
            "if(fill('ПОЩЕНСКИ КОД',d.pc)||fill('POSTAL CODE',d.pc))cnt++;"
            "if(fill('ГРАД',d.ci)||fill('CITY',d.ci))cnt++;"
            "if(fill('ДЪРЖАВА',d.co)||fill('COUNTRY',d.co))cnt++;"
            "alert('Filled '+cnt+' fields! Check the form.');"
            "})();"
        )
        
        # Format data as tab-separated values in the order FedEx expects
        # This format should auto-fill fields when pasted in sequence
        # Order: Contact Name, Company (empty), Phone, Email, Address1, Address2, Postal Code, City, Country
        tab_separated_data = f"{contact_name}\t\t{obj.phone}\t{obj.email or ''}\t{address1}\t{address2}\t{obj.postal_code}\t{obj.city}\t{obj.country or ''}"
        
        # Also create a newline-separated version for manual field-by-field paste
        newline_separated_data = f"""{contact_name}
{obj.phone}
{obj.email or ''}
{address1}
{address2}
{obj.postal_code}
{obj.city}
{obj.country or ''}"""
        
        return format_html(
            '''
            <div style="margin: 10px 0; font-family: Arial, sans-serif;">
                <h3 style="color: #667eea; margin-bottom: 15px;">📋 Copy All Data for FedEx Ship Manager</h3>
                
                <div style="background: #fff3cd; padding: 15px; border-radius: 4px; margin-bottom: 15px; border-left: 4px solid #ffc107;">
                    <strong>⚠️ Instructions:</strong>
                    <ol style="margin: 10px 0; padding-left: 20px;">
                        <li>Click "Copy All Data" button below</li>
                        <li>Go to FedEx Ship Manager</li>
                        <li>Click in the first field (Contact Name)</li>
                        <li>Press <strong>Tab</strong> key to move to next field</li>
                        <li>Paste (Ctrl+V / Cmd+V) - data will fill current field</li>
                        <li>Press <strong>Tab</strong> again and paste - repeat for each field</li>
                    </ol>
                    <p style="margin: 10px 0 0 0; font-size: 12px;">
                        <strong>Alternative:</strong> If tab-separated doesn't work, use the "Copy Line-by-Line" option below.
                    </p>
                </div>
                
                <div style="background: #f9f9f9; padding: 15px; border-radius: 4px; margin-bottom: 15px;">
                    <strong>Option 1: Tab-Separated (Auto-fill with Tab key):</strong><br>
                    <textarea id="tab-data-{}" readonly style="width: 100%; height: 60px; font-family: monospace; font-size: 12px; padding: 8px; border: 1px solid #ddd; border-radius: 4px; margin: 5px 0; background: white;">{}</textarea>
                    <button onclick="copyTabData({})" style="background: #667eea; color: white; padding: 8px 20px; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; margin-top: 5px;">
                        📋 Copy All Data (Tab-Separated)
                    </button>
                    <span id="tab-status-{}" style="margin-left: 10px; color: green; font-weight: bold;"></span>
                </div>
                
                <div style="background: #f9f9f9; padding: 15px; border-radius: 4px; margin-bottom: 15px;">
                    <strong>Option 2: Line-by-Line (One field per line):</strong><br>
                    <textarea id="line-data-{}" readonly style="width: 100%; height: 200px; font-family: monospace; font-size: 12px; padding: 8px; border: 1px solid #ddd; border-radius: 4px; margin: 5px 0; background: white;">{}</textarea>
                    <button onclick="copyLineData({})" style="background: #28a745; color: white; padding: 8px 20px; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; margin-top: 5px;">
                        📋 Copy All Data (Line-by-Line)
                    </button>
                    <span id="line-status-{}" style="margin-left: 10px; color: green; font-weight: bold;"></span>
                    <p style="margin: 10px 0 0 0; font-size: 12px; color: #666;">
                        Copy this, then paste line-by-line into each FedEx field in order.
                    </p>
                </div>
                
                <div style="background: #e8f4f8; padding: 15px; border-radius: 4px; margin-top: 15px;">
                    <strong>📝 Field order (FedEx may show localised labels; bookmarklet matches both):</strong>
                    <ol style="margin: 10px 0; padding-left: 20px; font-size: 12px;">
                        <li>Contact Name - <code>{}</code></li>
                        <li>Company - <em>leave empty</em></li>
                        <li>Phone - <code>{}</code></li>
                        <li>Email - <code>{}</code></li>
                        <li>Address line 1 - <code>{}</code></li>
                        <li>Address line 2 - <code>{}</code></li>
                        <li>Postal code - <code>{}</code></li>
                        <li>City - <code>{}</code></li>
                        <li>Country / territory - <code>{}</code></li>
                    </ol>
                </div>
                
                <div style="background: #d4edda; padding: 15px; border-radius: 4px; margin-top: 15px; border-left: 4px solid #28a745;">
                    <h4 style="margin-top: 0; color: #155724;">🚀 Option 3: Auto-Fill Bookmarklet (Recommended)</h4>
                    <p style="margin: 10px 0;">This bookmarklet will automatically fill all fields in FedEx Ship Manager with one click!</p>
                    
                    <div style="background: white; padding: 10px; border-radius: 4px; margin: 10px 0;">
                        <strong>Step 1:</strong> Drag this button to your bookmarks bar, or right-click → "Bookmark this link":<br>
                        <a id="bookmarklet-link-{}" href="{}" style="display: inline-block; background: #28a745; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px; font-weight: bold; margin: 10px 0; cursor: move;">📦 Auto-Fill FedEx Form</a>
                    </div>
                    
                    <div style="background: white; padding: 10px; border-radius: 4px; margin: 10px 0;">
                        <strong>Step 2:</strong> Go to FedEx Ship Manager → Create Shipment page<br>
                        <strong>Step 3:</strong> Click the bookmarklet from your bookmarks bar<br>
                        <strong>Step 4:</strong> All fields will be filled automatically! ✅
                    </div>
                    
                    <p style="margin: 10px 0 0 0; font-size: 11px; color: #666;">
                        <strong>Note:</strong> If some fields don't fill, you can manually copy from the fields above. The bookmarklet works by finding fields by their labels.
                    </p>
                </div>
            </div>
            <script>
                function copyTabData(orderId) {{
                    var textarea = document.getElementById('tab-data-' + orderId);
                    textarea.select();
                    textarea.setSelectionRange(0, 99999);
                    try {{
                        document.execCommand('copy');
                        var status = document.getElementById('tab-status-' + orderId);
                        status.textContent = '✅ Copied! Now go to FedEx and paste with Tab key.';
                        setTimeout(function() {{
                            status.textContent = '';
                        }}, 5000);
                    }} catch (err) {{
                        alert('Failed to copy. Please select and copy manually.');
                    }}
                }}
                
                function copyLineData(orderId) {{
                    var textarea = document.getElementById('line-data-' + orderId);
                    textarea.select();
                    textarea.setSelectionRange(0, 99999);
                    try {{
                        document.execCommand('copy');
                        var status = document.getElementById('line-status-' + orderId);
                        status.textContent = '✅ Copied! Paste line-by-line into FedEx fields.';
                        setTimeout(function() {{
                            status.textContent = '';
                        }}, 5000);
                    }} catch (err) {{
                        alert('Failed to copy. Please select and copy manually.');
                    }}
                }}
            </script>
            ''',
            obj.id, tab_separated_data, obj.id, obj.id,
            obj.id, newline_separated_data, obj.id, obj.id,
            obj.id, bookmarklet_js,
            contact_name, obj.phone, obj.email or '', address1, address2, obj.postal_code, obj.city, obj.country or ''
        )
    
    fedex_copy_paste.short_description = "FedEx Copy-Paste"
    
    def print_shipping_labels(self, request, queryset):
        """Admin action to print shipping labels for selected orders."""
        from django.urls import reverse
        from django.contrib import messages
        
        if queryset.count() == 1:
            # Single order - redirect to print page
            order = queryset.first()
            url = reverse('admin:print_shipping_label', args=[order.pk])
            return redirect(url)
        else:
            # Multiple orders - open each in new window
            messages.info(request, f"Opening {queryset.count()} shipping labels in new windows...")
            # Return a response that opens multiple windows
            from django.http import HttpResponse
            from django.urls import reverse
            html = '<html><head><title>Printing Labels</title></head><body><h2>Opening labels...</h2><script>'
            for order in queryset:
                url = reverse('admin:print_shipping_label', args=[order.pk])
                html += f'window.open("{url}", "_blank");'
            html += 'setTimeout(function() { window.close(); }, 1000);</script></body></html>'
            return HttpResponse(html)
    
    print_shipping_labels.short_description = "🖨️ Print shipping labels"
    
    def create_shipping_labels(self, request, queryset):
        """
        Admin action to create shipping labels via carrier APIs.

        Uses ``auto_create_shipping_label`` so:
          - the carrier is auto-detected from ``shipping_option.name`` if not set,
          - orders that already have a label are skipped (idempotent),
          - results are persisted back to the Order.
        """
        from ecommerce.utils.shipping import auto_create_shipping_label
        import logging

        logger = logging.getLogger(__name__)
        created_count = 0
        skipped_count = 0
        failed_count = 0
        error_details = []

        for order in queryset:
            if order.shipping_label_url or order.tracking_number:
                skipped_count += 1
                continue

            logger.info(f"Admin action: creating label for Order #{order.id}")
            try:
                result = auto_create_shipping_label(order.pk)
            except Exception as exc:
                failed_count += 1
                error_details.append(f"Order #{order.id}: {exc}")
                logger.exception(f"create_shipping_labels raised for #{order.id}")
                continue

            if result and (result.get('label_url') or result.get('tracking_number')):
                created_count += 1
                logger.info(f"✅ Created label for Order #{order.id}")
            else:
                failed_count += 1
                error_details.append(
                    f"Order #{order.id}: no carrier detected or API returned empty"
                )
        
        if created_count > 0:
            self.message_user(
                request,
                f"✅ Created {created_count} shipping labels.",
                messages.SUCCESS,
            )
        if skipped_count > 0:
            self.message_user(
                request,
                f"↷ Skipped {skipped_count} orders that already had a label/tracking.",
                messages.INFO,
            )
        if failed_count > 0:
            error_msg = f"⚠️ {failed_count} labels could not be created."
            if error_details:
                error_msg += f" Details: {', '.join(error_details[:3])}"
            self.message_user(
                request,
                error_msg + " Check Railway logs for more details.",
                messages.WARNING,
            )

    create_shipping_labels.short_description = "📦 Create shipping labels via carrier API"

    # ------------------------------------------------------------------
    # Group multiple orders into a single DPI AWB (certification + daily
    # dispatch). All selected orders are shipped as items of ONE DPI
    # create-order call, which forces them to share the same AWB as long
    # as product + serviceLevel match.
    # ------------------------------------------------------------------
    def group_into_single_dpi_awb(self, request, queryset):
        """Admin action: bundle the selected orders into a single DPI
        create-order request so they share one AWB (transportation
        document).

        Honors GLOBAL_MAIL_TEST_MODE:
          * True  → sandbox (safe, nothing billable)
          * False → production (real shipments; tracking/awb persisted)
        """
        import requests
        from django.urls import reverse
        from ecommerce.utils.shipping import GlobalMailShipping

        orders = list(queryset.order_by("id"))
        if len(orders) < 2:
            self.message_user(
                request,
                "Select at least 2 orders to group into one AWB.",
                level=messages.WARNING,
            )
            return

        force_sandbox = bool(getattr(settings, "GLOBAL_MAIL_TEST_MODE", True))
        dpi = GlobalMailShipping(force_sandbox=force_sandbox)

        if not (dpi.consumer_key and dpi.consumer_secret and dpi.customer_ekp):
            self.message_user(
                request,
                "Missing GLOBAL_MAIL_API_KEY / _API_SECRET / _CUSTOMER_EKP on the server.",
                level=messages.ERROR,
            )
            return

        token = dpi._get_access_token()
        if not token:
            self.message_user(
                request,
                "DPI auth failed. Check Railway logs and your credentials.",
                level=messages.ERROR,
            )
            return

        items = []
        products_seen = set()
        services_seen = set()
        for o in orders:
            try:
                sub_payload = dpi._prepare_shipment_data(o)
                sub_items = sub_payload.get("items") or []
                if not sub_items:
                    continue
                sub_items[0]["custRef"] = str(o.id)
                items.append(sub_items[0])
                products_seen.add(sub_items[0].get("product"))
                services_seen.add(sub_items[0].get("serviceLevel"))
            except Exception as exc:
                self.message_user(
                    request,
                    f"Skipping Order #{o.id}: payload build failed ({exc}).",
                    level=messages.WARNING,
                )

        if not items:
            self.message_user(
                request,
                "No valid items to ship after building payload.",
                level=messages.ERROR,
            )
            return

        if len(products_seen) > 1 or len(services_seen) > 1:
            self.message_user(
                request,
                (
                    "⚠️ Selected orders use different products/service levels: "
                    f"products={sorted(p for p in products_seen if p)}, "
                    f"services={sorted(s for s in services_seen if s)}. DPI will split "
                    "them into MULTIPLE AWBs (one per combination). Consider grouping "
                    "by destination/product."
                ),
                level=messages.WARNING,
            )

        lead_order = orders[0]
        _jr = f"AWB-{lead_order.id}-{len(items)}"[:17]
        payload = {
            "customerEkp": str(dpi.customer_ekp),
            "orderStatus": "FINALIZE",
            "paperwork": {
                "contactName": (getattr(settings, "SHOP_CONTACT_NAME", "Marbaras"))[:35],
                "jobReference": _jr,
                "telephoneNumber": (getattr(settings, "SHOP_PHONE", "") or "+359888000000"),
                "awbCopyCount": 1,
            },
            "items": items,
        }

        try:
            r = requests.post(
                dpi.orders_url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=60,
            )
        except Exception as exc:
            self.message_user(
                request,
                f"DPI request exception: {exc}",
                level=messages.ERROR,
            )
            return

        if r.status_code not in (200, 201):
            self.message_user(
                request,
                f"DPI create-order failed: HTTP {r.status_code}. "
                f"Body: {(r.text or '')[:600]}",
                level=messages.ERROR,
            )
            return

        body = r.json() or {}
        shipments = body.get("shipments") or []
        if not shipments:
            self.message_user(
                request,
                f"DPI returned no shipments: {body}",
                level=messages.ERROR,
            )
            return

        cust_ref_to_order = {str(o.id): o for o in orders}
        created_pairs = []
        awbs_used = set()
        for sh in shipments:
            awb = sh.get("awb") or sh.get("awbNumber") or ""
            if awb:
                awbs_used.add(str(awb))
            for it in sh.get("items") or []:
                item_id = it.get("id") or it.get("itemId") or ""
                barcode = it.get("barcode") or ""
                cust_ref = str(it.get("custRef") or "").strip()
                ord_obj = cust_ref_to_order.get(cust_ref)
                if not ord_obj:
                    continue

                if not force_sandbox:
                    try:
                        _update_fields = []
                        if item_id and str(item_id) != (ord_obj.shipment_id or ""):
                            ord_obj.shipment_id = str(item_id)
                            _update_fields.append("shipment_id")
                        if awb and hasattr(ord_obj, "awb") and str(awb) != (ord_obj.awb or ""):
                            ord_obj.awb = str(awb)
                            _update_fields.append("awb")
                        if barcode and barcode != (ord_obj.tracking_number or ""):
                            ord_obj.tracking_number = barcode
                            _update_fields.append("tracking_number")
                        if _update_fields:
                            ord_obj.save(update_fields=_update_fields)
                    except Exception:
                        import logging as _logging
                        _logging.getLogger(__name__).exception(
                            "group_into_single_dpi_awb: failed to persist order #%s",
                            ord_obj.id,
                        )

                created_pairs.append(
                    {
                        "order_id": ord_obj.id,
                        "full_name": ord_obj.full_name,
                        "country": ord_obj.country,
                        "item_id": str(item_id),
                        "barcode": str(barcode),
                        "awb": str(awb),
                    }
                )

        request.session["dpi_grouped_result"] = {
            "sandbox": force_sandbox,
            "awbs": sorted(awbs_used),
            "items": created_pairs,
        }
        self.message_user(
            request,
            (
                f"✅ DPI order created with {len(created_pairs)} item(s) "
                f"under AWB(s): {', '.join(sorted(awbs_used)) or '—'} "
                f"(mode={'SANDBOX' if force_sandbox else 'PRODUCTION'})."
            ),
            level=messages.SUCCESS,
        )
        return HttpResponseRedirect(reverse("admin:dpi_grouped_result"))

    group_into_single_dpi_awb.short_description = (
        "🧾 Group selected orders into ONE DPI AWB (certification)"
    )

    def dpi_grouped_result_view(self, request):
        """Display the result of the last group_into_single_dpi_awb action
        with one-click links to print each item label + the shared AWB."""
        from django.shortcuts import render
        from django.urls import reverse

        if not request.user.is_staff:
            return HttpResponse("Staff only.", status=403, content_type="text/plain")

        data = request.session.get("dpi_grouped_result") or {}
        items = data.get("items") or []
        awbs = data.get("awbs") or []

        rows_html = []
        for it in items:
            try:
                order_url = reverse("admin:ecommerce_order_change", args=[it["order_id"]])
            except Exception:
                order_url = "#"
            try:
                label_url = reverse(
                    "admin:print_test_label_for_order", args=[it["order_id"]]
                )
            except Exception:
                label_url = None
            rows_html.append(
                f'<tr>'
                f'<td><a href="{order_url}" target="_blank">#{it["order_id"]}</a></td>'
                f'<td>{it.get("full_name") or ""}</td>'
                f'<td>{it.get("country") or ""}</td>'
                f'<td><code>{it.get("barcode") or it.get("item_id") or ""}</code></td>'
                f'<td><code>{it.get("awb") or ""}</code></td>'
                f'</tr>'
            )
        rows = "".join(rows_html) or '<tr><td colspan="5">No data — run the action first.</td></tr>'

        awb_buttons = ""
        for awb in awbs:
            if not awb:
                continue
            try:
                awb_url = reverse("admin:print_awb_by_number") + f"?awb={awb}"
            except Exception:
                continue
            awb_buttons += (
                f'<a href="{awb_url}" target="_blank" '
                f'style="display:inline-block;background:#7c3aed;color:#fff;'
                f'padding:10px 18px;border-radius:6px;text-decoration:none;'
                f'font-weight:600;margin:4px 6px 4px 0;">🧾 Print AWB {awb}</a>'
            )
        if not awb_buttons:
            awb_buttons = '<span style="color:#94a3b8;">No AWB returned.</span>'

        mode_banner = ""
        if data.get("sandbox"):
            mode_banner = (
                '<div style="background:#fef3c7;border-left:4px solid #f59e0b;'
                'padding:10px 14px;border-radius:4px;margin-bottom:16px;">'
                '🧪 <strong>SANDBOX mode</strong> — these shipments are NOT real.'
                '</div>'
            )
        else:
            mode_banner = (
                '<div style="background:#fee2e2;border-left:4px solid #dc2626;'
                'padding:10px 14px;border-radius:4px;margin-bottom:16px;">'
                '🚨 <strong>PRODUCTION mode</strong> — real billable shipments; '
                'AWB + tracking are saved on each order.'
                '</div>'
            )

        html = f"""
        <!DOCTYPE html><html><head><meta charset="utf-8">
        <title>DPI Grouped AWB result</title>
        <style>
          body{{font-family:-apple-system,sans-serif;max-width:1000px;margin:30px auto;padding:20px;}}
          h1{{font-size:22px;}}
          table{{border-collapse:collapse;width:100%;margin-top:12px;}}
          th,td{{border:1px solid #e2e8f0;padding:8px 10px;text-align:left;font-size:13px;}}
          th{{background:#f1f5f9;}}
          code{{background:#f1f5f9;padding:1px 6px;border-radius:3px;font-size:12px;}}
          a.back{{color:#475569;text-decoration:none;font-size:13px;}}
        </style></head><body>
        <p><a class="back" href="{reverse('admin:ecommerce_order_changelist')}">&larr; Back to orders</a></p>
        <h1>🧾 DPI grouped AWB — result</h1>
        {mode_banner}
        <div style="margin:12px 0 18px 0;">{awb_buttons}</div>
        <h3 style="font-size:15px;margin-top:24px;">Items in this order</h3>
        <table>
          <thead><tr>
            <th>Order</th><th>Recipient</th><th>Country</th>
            <th>Barcode / Item ID</th><th>AWB</th>
          </tr></thead>
          <tbody>{rows}</tbody>
        </table>
        <p style="margin-top:20px;color:#64748b;font-size:12px;">
          To print an individual item label, open the order (click its number)
          and use the &#x1F3F7;&#xFE0F; Label button, or run the bulk print
          queue. The AWB buttons above download the shared transportation
          document required by DPI.
        </p>
        </body></html>
        """
        return HttpResponse(html, content_type="text/html; charset=utf-8")

    def get_urls(self):
        """Add custom URLs for printing shipping labels and barcode scanner."""
        from django.urls import path
        urls = super().get_urls()
        custom_urls = [
            path(
                '<int:order_id>/print-label/',
                self.admin_site.admin_view(self.print_shipping_label_view),
                name='print_shipping_label',
            ),
            path(
                '<int:order_id>/create-label/',
                self.admin_site.admin_view(self.create_label_view),
                name='create_shipping_label',
            ),
            path(
                'barcode-scanner/',
                self.admin_site.admin_view(self.barcode_scanner_view),
                name='barcode_scanner',
            ),
            path(
                'print-queue/',
                self.admin_site.admin_view(self.print_queue_view),
                name='print_queue',
            ),
            path(
                'print-queue.json',
                self.admin_site.admin_view(self.print_queue_feed_view),
                name='print_queue_feed',
            ),
            path(
                '<int:order_id>/auto-create-label/',
                self.admin_site.admin_view(self.auto_create_label_view),
                name='auto_create_shipping_label',
            ),
            path(
                'probe-dhl/',
                self.admin_site.admin_view(self.probe_dhl_view),
                name='probe_dhl_api',
            ),
            path(
                'print-awb/',
                self.admin_site.admin_view(self.print_awb_by_number_view),
                name='print_awb_by_number',
            ),
            path(
                'dpi-grouped-result/',
                self.admin_site.admin_view(self.dpi_grouped_result_view),
                name='dpi_grouped_result',
            ),
            path(
                'print-test-label/',
                self.admin_site.admin_view(self.print_test_label_view),
                name='print_test_label',
            ),
            path(
                '<int:order_id>/print-test-label/',
                self.admin_site.admin_view(self.print_test_label_for_order_view),
                name='print_test_label_for_order',
            ),
            path(
                'etsy-to-dpi-csv/',
                self.admin_site.admin_view(self.etsy_to_dpi_csv_view),
                name='etsy_to_dpi_csv',
            ),
        ]
        return custom_urls + urls
    
    def print_shipping_label_view(self, request, order_id):
        """View to render shipping label for printing."""
        from django.shortcuts import get_object_or_404
        from django.template.loader import render_to_string
        from django.http import HttpResponse
        
        order = get_object_or_404(Order, pk=order_id)
        
        # Prefetch related items for better performance
        order.items.select_related('product', 'variant').all()
        
        html = render_to_string('admin/shipping_label.html', {
            'order': order,
        }, request=request)
        
        return HttpResponse(html)
    
    def create_label_view(self, request, order_id):
        """View to create shipping label for a single order."""
        from django.shortcuts import get_object_or_404, redirect
        from django.urls import reverse
        from ecommerce.utils.shipping import create_shipping_label
        import logging
        
        logger = logging.getLogger(__name__)
        order = get_object_or_404(Order, pk=order_id)
        
        if not order.shipping_carrier:
            messages.error(request, f"Order #{order.id}: No carrier selected. Please select a shipping carrier first.")
            from django.urls import reverse
        return redirect(reverse('admin:ecommerce_order_change', args=[order_id]))
        
        logger.info(f"Creating shipping label for Order #{order.id} with carrier {order.shipping_carrier}")
        label_data = create_shipping_label(order, order.shipping_carrier)
        
        if label_data:
            order.tracking_number = label_data.get('tracking_number')
            order.shipping_label_url = label_data.get('label_url')
            order.shipment_id = label_data.get('shipment_id')
            _update_fields = ['tracking_number', 'shipping_label_url', 'shipment_id']
            _awb = label_data.get('awb')
            if _awb and hasattr(order, 'awb'):
                order.awb = _awb
                _update_fields.append('awb')
            order.save(update_fields=_update_fields)
            messages.success(request, f"✅ Successfully created shipping label for Order #{order.id}. Tracking: {order.tracking_number}")
            logger.info(f"✅ Successfully created label for Order #{order.id}: tracking={order.tracking_number}")
        else:
            messages.error(request, f"❌ Failed to create shipping label for Order #{order.id}. Check Railway logs for details.")
            logger.error(f"❌ Failed to create label for Order #{order.id} with carrier {order.shipping_carrier}")
        
        from django.urls import reverse
        return redirect(reverse('admin:ecommerce_order_change', args=[order_id]))
    
    def _print_queue_orders_qs(self):
        """
        Orders that should appear in the automatic print queue:

          * not shipped yet, AND
          * either already have a label / tracking number, OR
          * have a shipping carrier assigned (so the admin can trigger
            auto-create for them), OR
          * have a shipping option whose name implies a known carrier
            (FedEx / Global Mail / Global Post / DHL), so freshly placed
            orders show up even before shipping_carrier is filled in.
        """
        from django.db.models import Q

        carrier_keywords = ['fedex', 'global', 'dhl', 'easypost', 'deutsche']
        option_name_filter = Q()
        for kw in carrier_keywords:
            option_name_filter |= Q(shipping_option__name__icontains=kw)

        has_label_or_tracking = (
            (Q(shipping_label_url__isnull=False) & ~Q(shipping_label_url=''))
            | (Q(tracking_number__isnull=False) & ~Q(tracking_number=''))
        )
        has_carrier = (
            Q(shipping_carrier__isnull=False) & ~Q(shipping_carrier='')
        )

        return (
            Order.objects
            .filter(is_shipped=False)
            .filter(has_label_or_tracking | has_carrier | option_name_filter)
            .select_related('shipping_option')
            .order_by('created_at')
            .distinct()
        )

    def print_queue_view(self, request):
        """
        Admin page that lists unprinted shipping labels and auto-opens each
        newly arrived label in its own browser tab so a connected 4x6
        thermal printer can pick it up.
        """
        from django.shortcuts import render
        from django.urls import reverse

        orders = list(self._print_queue_orders_qs()[:200])

        initial = []
        for o in orders:
            initial.append({
                'id': o.id,
                'full_name': o.full_name,
                'carrier': (o.shipping_carrier or '').upper() or '—',
                'tracking_number': o.tracking_number or '',
                'created_at': o.created_at.strftime('%Y-%m-%d %H:%M'),
                'print_url': reverse('admin:print_shipping_label', args=[o.pk]) + '?auto=1',
                'change_url': reverse('admin:ecommerce_order_change', args=[o.pk]),
                'has_label': bool(o.shipping_label_url),
            })

        ctx = {
            'title': 'Shipping label print queue',
            'feed_url': reverse('admin:print_queue_feed'),
            'initial_orders': initial,
            'initial_orders_json': json.dumps(initial),
            'opts': Order._meta,
            'app_label': Order._meta.app_label,
            'has_permission': True,
            'site_header': getattr(self.admin_site, 'site_header', 'Admin'),
            'site_title': getattr(self.admin_site, 'site_title', 'Admin'),
        }
        return render(request, 'admin/shipping_print_queue.html', ctx)

    def print_queue_feed_view(self, request):
        """
        JSON feed used by the print queue page to poll for new unprinted
        labels. Client-side JavaScript tracks which orders it has already
        opened and only auto-opens new ones.
        """
        from django.http import JsonResponse
        from django.urls import reverse

        try:
            since_id = int(request.GET.get('since', '0') or '0')
        except (TypeError, ValueError):
            since_id = 0

        qs = self._print_queue_orders_qs()
        if since_id:
            qs = qs.filter(id__gt=since_id)

        orders_data = []
        for o in qs[:100]:
            orders_data.append({
                'id': o.id,
                'full_name': o.full_name,
                'carrier': (o.shipping_carrier or '').upper() or '—',
                'tracking_number': o.tracking_number or '',
                'created_at': o.created_at.strftime('%Y-%m-%d %H:%M'),
                'print_url': reverse('admin:print_shipping_label', args=[o.pk]) + '?auto=1',
                'change_url': reverse('admin:ecommerce_order_change', args=[o.pk]),
                'has_label': bool(o.shipping_label_url),
            })

        return JsonResponse({'orders': orders_data})

    def auto_create_label_view(self, request, order_id):
        """
        Manual trigger to (re)run the automatic label creation for an order.
        Useful if the background thread failed silently (e.g. carrier API
        was down at checkout time).
        """
        from django.shortcuts import get_object_or_404, redirect
        from django.urls import reverse
        from ecommerce.utils.shipping import auto_create_shipping_label

        order = get_object_or_404(Order, pk=order_id)
        result = auto_create_shipping_label(order.pk)

        if result and result.get('label_url'):
            messages.success(
                request,
                f"✅ Label ready for Order #{order.id}. Tracking: {result.get('tracking_number') or 'N/A'}",
            )
        elif result and result.get('tracking_number'):
            messages.warning(
                request,
                f"⚠️ Order #{order.id}: carrier returned tracking but no label URL. "
                f"Tracking: {result.get('tracking_number')}",
            )
        else:
            messages.error(
                request,
                f"❌ Could not auto-create label for Order #{order.id}. "
                "Check carrier/credentials or server logs.",
            )

        return redirect(reverse('admin:print_queue'))

    def probe_dhl_view(self, request):
        """
        Admin page that probes several DHL API products with the configured
        Global Mail credentials and reports which API accepts them.

        It does NOT create any shipments — only read/auth requests against
        sandbox endpoints. Available at /admin/ecommerce/order/probe-dhl/.
        """
        from django.shortcuts import render
        from django.conf import settings as dj_settings

        if not request.user.is_superuser:
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden("Superuser only.")

        key = (getattr(dj_settings, "GLOBAL_MAIL_API_KEY", "") or "").strip()
        secret = (getattr(dj_settings, "GLOBAL_MAIL_API_SECRET", "") or "").strip()
        account = (getattr(dj_settings, "GLOBAL_MAIL_ACCOUNT_NUMBER", "") or "").strip()

        ekp = (getattr(dj_settings, "GLOBAL_MAIL_CUSTOMER_EKP", "") or "").strip()
        test_mode = bool(getattr(dj_settings, "GLOBAL_MAIL_TEST_MODE", True))

        report = None
        test_order_result = None
        action = request.POST.get("action") if request.method == "POST" else None
        if action == "run_probe" or (request.method == "POST" and not action):
            report = self._run_dhl_probes(key, secret, account)
        elif action == "test_order":
            test_order_result = self._run_dpi_test_order(key, secret, ekp, test_mode)

        ctx = {
            'title': 'Probe DHL API credentials',
            'has_credentials': bool(key and secret),
            'masked_key': (key[:10] + "…") if key else '(not set)',
            'masked_secret_len': len(secret) if secret else 0,
            'account': account or '(not set)',
            'ekp': ekp or '(not set)',
            'test_mode': test_mode,
            'report': report,
            'test_order_result': test_order_result,
            'opts': Order._meta,
            'app_label': Order._meta.app_label,
            'has_permission': True,
            'site_header': getattr(self.admin_site, 'site_header', 'Admin'),
            'site_title': getattr(self.admin_site, 'site_title', 'Admin'),
        }
        return render(request, 'admin/probe_dhl_api.html', ctx)

    def print_awb_by_number_view(self, request):
        """
        Fetch & stream the AWB (Airwaybill / transportation document) PDF
        for any DPI AWB number. Accepts the number via GET param (?awb=...)
        or renders a small form so the admin can paste it in.

        Required by DPI for pickup and during the go-live certification.
        URL: /admin/ecommerce/order/print-awb/?awb=XXXXXXX
        """
        from django.shortcuts import render
        from ecommerce.utils.shipping import GlobalMailShipping

        if not request.user.is_staff:
            return HttpResponse("Staff only.", status=403, content_type="text/plain")

        awb = (request.GET.get("awb") or request.POST.get("awb") or "").strip()

        if not awb:
            html = """
            <!DOCTYPE html><html><head><meta charset="utf-8">
            <title>Print DPI AWB</title>
            <style>
              body{font-family:-apple-system,sans-serif;max-width:600px;margin:40px auto;padding:20px;}
              h1{font-size:20px;margin-bottom:8px;}
              p{color:#64748b;margin-bottom:20px;}
              input[type=text]{width:100%;padding:10px;font-size:14px;border:1px solid #cbd5e1;border-radius:6px;box-sizing:border-box;}
              button{margin-top:12px;padding:10px 16px;background:#7c3aed;color:#fff;border:none;border-radius:6px;font-size:14px;font-weight:600;cursor:pointer;}
              button:hover{background:#6d28d9;}
              .hint{background:#f1f5f9;padding:12px;border-radius:6px;margin-top:16px;font-size:13px;color:#475569;}
            </style></head><body>
            <h1>&#x1F9FE; Print DPI AWB Label</h1>
            <p>Enter an AWB (Airwaybill) number. The transportation document PDF
            will open in a new tab.</p>
            <form method="GET" action="">
              <label>AWB Number</label>
              <input type="text" name="awb" placeholder="e.g. CXNLABC123456789" autofocus required />
              <button type="submit">Fetch AWB</button>
            </form>
            <div class="hint">
              <strong>Tip:</strong> You can find the AWB number in the order's
              detail page after the first label was printed. For marketplace
              orders there is a dedicated &#x1F9FE; AWB button in the list view.
            </div>
            </body></html>
            """
            return HttpResponse(html, content_type="text/html; charset=utf-8")

        force_sandbox = bool(getattr(settings, "GLOBAL_MAIL_TEST_MODE", True))
        dpi = GlobalMailShipping(force_sandbox=force_sandbox)

        if not (dpi.consumer_key and dpi.consumer_secret and dpi.customer_ekp):
            return HttpResponse(
                "Missing GLOBAL_MAIL_API_KEY / GLOBAL_MAIL_API_SECRET / GLOBAL_MAIL_CUSTOMER_EKP.",
                status=400,
                content_type="text/plain",
            )

        pdf_bytes = dpi.get_awb_label(awb)
        if not pdf_bytes:
            return HttpResponse(
                f"AWB fetch failed for '{awb}'. Check Railway logs for the "
                f"exact HTTP status/body from DPI. Common causes:\n"
                f"  • The AWB number is wrong or doesn't exist for this EKP.\n"
                f"  • The shipment hasn't been FINALIZED yet on DPI's side.\n"
                f"  • Sandbox/production mismatch "
                f"(mode={'sandbox' if force_sandbox else 'production'}).",
                status=502,
                content_type="text/plain; charset=utf-8",
            )

        refitted = GlobalMailShipping.refit_pdf_to_4x6(pdf_bytes)
        response = HttpResponse(refitted, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="AWB-{awb}.pdf"'
        response["X-DPI-AWB"] = awb
        return response

    def print_test_label_view(self, request):
        """
        Create a one-off DPI sandbox shipment and stream the PDF label
        back to the browser (inline).  This lets admins verify their
        4x6 thermal printer (e.g. Zebra ZP-505) before going live.

        Always hits the sandbox — never production — regardless of
        GLOBAL_MAIL_TEST_MODE.
        """
        import requests
        from django.conf import settings as dj_settings
        from django.http import HttpResponse, HttpResponseForbidden, HttpResponseBadRequest

        if not request.user.is_superuser:
            return HttpResponseForbidden("Superuser only.")

        key = (getattr(dj_settings, "GLOBAL_MAIL_API_KEY", "") or "").strip()
        secret = (getattr(dj_settings, "GLOBAL_MAIL_API_SECRET", "") or "").strip()
        ekp = (getattr(dj_settings, "GLOBAL_MAIL_CUSTOMER_EKP", "") or "").strip()
        host = "https://api-sandbox.dhl.com"

        if not (key and secret and ekp):
            return HttpResponseBadRequest(
                "GLOBAL_MAIL_API_KEY, GLOBAL_MAIL_API_SECRET and GLOBAL_MAIL_CUSTOMER_EKP must be set."
            )

        try:
            tr = requests.get(
                f"{host}/dpi/v1/auth/accesstoken",
                auth=(key, secret),
                headers={"Accept": "application/json"},
                timeout=15,
            )
            if tr.status_code != 200:
                return HttpResponse(
                    f"Auth failed: HTTP {tr.status_code}\n\n{tr.text[:500]}",
                    status=502,
                    content_type="text/plain; charset=utf-8",
                )
            token = (tr.json() or {}).get("access_token")
            if not token:
                return HttpResponse("No access_token in response.", status=502, content_type="text/plain")
        except Exception as e:
            return HttpResponse(f"Auth exception: {e}", status=502, content_type="text/plain")

        payload = {
            "customerEkp": ekp,
            "orderStatus": "FINALIZE",
            "paperwork": {
                "contactName": (getattr(dj_settings, "SHOP_CONTACT_NAME", "Marbaras"))[:35],
                "jobReference": "test-print",
                "telephoneNumber": getattr(dj_settings, "SHOP_PHONE", "+359888000000") or "+359888000000",
                "awbCopyCount": 1,
            },
            "items": [{
                "product": "GPT",
                "serviceLevel": "PRIORITY",
                "recipient": "Test Recipient",
                "recipientPhone": "+4930000000",
                "recipientEmail": "test@example.com",
                "addressLine1": "Teststrasse 1",
                "city": "Berlin",
                "postalCode": "10115",
                "destinationCountry": "DE",
                "shipmentAmount": 10.00,
                "shipmentCurrency": "EUR",
                "shipmentGrossWeight": 250,
                "returnItemWanted": False,
                "custRef": "print-test",
                "contents": [{
                    "contentPieceIndexNumber": 1,
                    "contentPieceAmount": 1,
                    "contentPieceDescription": "Silver ring sample",
                    "contentPieceHsCode": "711311",
                    "contentPieceOrigin": "BG",
                    "contentPieceValue": "10.00",
                    "contentPieceNetweight": 250,
                }],
            }],
        }

        try:
            r = requests.post(
                f"{host}/dpi/shipping/v1/orders",
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=30,
            )
            if r.status_code not in (200, 201):
                return HttpResponse(
                    f"Create order failed: HTTP {r.status_code}\n\n{r.text[:1500]}",
                    status=502,
                    content_type="text/plain; charset=utf-8",
                )
            body = r.json() or {}
            shipments = body.get("shipments") or []
            if not shipments or not shipments[0].get("items"):
                return HttpResponse(f"No shipments/items in response:\n{body}", status=502, content_type="text/plain")
            item_id = shipments[0]["items"][0].get("id")
            if not item_id:
                return HttpResponse("Item has no id.", status=502, content_type="text/plain")
        except Exception as e:
            return HttpResponse(f"Create order exception: {e}", status=502, content_type="text/plain")

        try:
            label_size = (getattr(dj_settings, "GLOBAL_MAIL_LABEL_PAGE_SIZE", "4x6") or "").strip()
            label_params = {"pageSize": label_size} if label_size and label_size.lower() not in ("none", "off", "default") else None
            lr = requests.get(
                f"{host}/dpi/shipping/v1/items/{item_id}/label",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/pdf"},
                params=label_params,
                timeout=30,
            )
            if lr.status_code != 200 or not lr.content:
                return HttpResponse(
                    f"Label fetch failed: HTTP {lr.status_code}\n\n{lr.text[:500]}",
                    status=502,
                    content_type="text/plain; charset=utf-8",
                )
        except Exception as e:
            return HttpResponse(f"Label fetch exception: {e}", status=502, content_type="text/plain")

        from ecommerce.utils.shipping import GlobalMailShipping as _GM
        pdf_bytes = _GM.refit_pdf_to_4x6(lr.content)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="dpi-test-label-{item_id}.pdf"'
        response["X-DPI-Item-Id"] = str(item_id)
        response["X-DPI-AWB"] = str(shipments[0].get("awb") or "")
        return response

    def print_test_label_for_order_view(self, request, order_id):
        """
        Create a DPI shipment using the real data of an existing Order
        (recipient, items, weight, etc.) and stream the PDF label back
        inline.

        Mode is controlled by ``GLOBAL_MAIL_TEST_MODE``:
          * ``True``  → hits api-sandbox.dhl.com, DOES NOT save anything
                        to the order (pure test printing).
          * ``False`` → hits api.dhl.com, creates a REAL billable
                        shipment and persists tracking / AWB / shipment_id
                        back onto the order.
        """
        import requests
        from django.shortcuts import get_object_or_404
        from django.http import HttpResponse, HttpResponseForbidden
        from django.utils import timezone
        from ecommerce.utils.shipping import GlobalMailShipping

        if not request.user.is_superuser:
            return HttpResponseForbidden("Superuser only.")

        order = get_object_or_404(Order, pk=order_id)
        force_sandbox = bool(getattr(settings, "GLOBAL_MAIL_TEST_MODE", True))
        dpi = GlobalMailShipping(force_sandbox=force_sandbox)

        if not (dpi.consumer_key and dpi.consumer_secret and dpi.customer_ekp):
            return HttpResponse(
                "Missing GLOBAL_MAIL_API_KEY / GLOBAL_MAIL_API_SECRET / GLOBAL_MAIL_CUSTOMER_EKP.",
                status=400, content_type="text/plain",
            )

        token = dpi._get_access_token()
        if not token:
            return HttpResponse("DPI auth failed — check Railway logs.", status=502, content_type="text/plain")

        try:
            payload = dpi._prepare_shipment_data(order)
        except Exception as exc:
            return HttpResponse(
                f"Failed to build payload for Order #{order.id}: {exc}",
                status=500, content_type="text/plain; charset=utf-8",
            )

        try:
            r = requests.post(
                dpi.orders_url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=30,
            )
            if r.status_code not in (200, 201):
                return HttpResponse(
                    f"DPI create-order failed for Order #{order.id}: HTTP {r.status_code}\n\n"
                    f"{r.text[:2000]}\n\nRequest payload:\n{payload}",
                    status=502, content_type="text/plain; charset=utf-8",
                )
            body = r.json() or {}
            shipments = body.get("shipments") or []
            if not shipments or not shipments[0].get("items"):
                return HttpResponse(
                    f"DPI returned no shipments/items:\n{body}",
                    status=502, content_type="text/plain; charset=utf-8",
                )
            item_id = shipments[0]["items"][0].get("id")
            awb = shipments[0].get("awb") or ""
        except Exception as exc:
            return HttpResponse(f"DPI create-order exception: {exc}", status=502, content_type="text/plain")

        try:
            lr = requests.get(
                f"{dpi.item_label_url}/{item_id}/label",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/pdf"},
                params=dpi._label_params() or None,
                timeout=30,
            )
            if lr.status_code != 200 or not lr.content:
                return HttpResponse(
                    f"Label fetch failed for Order #{order.id} (item {item_id}): HTTP {lr.status_code}\n\n{lr.text[:500]}",
                    status=502, content_type="text/plain; charset=utf-8",
                )
        except Exception as exc:
            return HttpResponse(f"Label fetch exception: {exc}", status=502, content_type="text/plain")

        if not force_sandbox:
            try:
                _update_fields = []
                tracking = ""
                try:
                    _first = shipments[0].get("items") or []
                    if _first:
                        tracking = _first[0].get("barcode") or ""
                except Exception:
                    tracking = ""
                if item_id and str(item_id) != (order.shipment_id or ""):
                    order.shipment_id = str(item_id)
                    _update_fields.append("shipment_id")
                if awb and hasattr(order, "awb") and str(awb) != (order.awb or ""):
                    order.awb = str(awb)
                    _update_fields.append("awb")
                if tracking and tracking != (order.tracking_number or ""):
                    order.tracking_number = tracking
                    _update_fields.append("tracking_number")
                if _update_fields:
                    order.save(update_fields=_update_fields)
            except Exception:
                import logging as _logging
                _logging.getLogger(__name__).exception(
                    "print_test_label_for_order_view: failed to persist label data "
                    "for Order #%s (awb=%s, item=%s)",
                    order.id, awb, item_id,
                )

        pdf_bytes = GlobalMailShipping.refit_pdf_to_4x6(lr.content)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        _prefix = "dpi-test" if force_sandbox else "dpi"
        response["Content-Disposition"] = (
            f'inline; filename="{_prefix}-order-{order.id}-item-{item_id}.pdf"'
        )
        response["X-DPI-Item-Id"] = str(item_id)
        response["X-DPI-AWB"] = str(awb)
        response["X-DPI-Order-Id"] = str(order.id)
        response["X-DPI-Mode"] = "sandbox" if force_sandbox else "production"
        return response

    def _run_dhl_probes(self, key: str, secret: str, account: str):
        """Run probes against a broad set of DHL API products / endpoints."""
        import requests

        def _trunc(t, n=160):
            return (t or "").replace("\n", " ")[:n]

        def _probe(api, func):
            try:
                return func()
            except requests.RequestException as e:
                return {"api": api, "ok": False, "status": "network", "note": _trunc(str(e))}
            except Exception as e:
                return {"api": api, "ok": False, "status": "error", "note": _trunc(str(e))}

        def _rejected(r):
            body_l = (r.text or "").lower()
            if r.status_code in (401, 403):
                return True
            if r.status_code == 400 and ("invalid credentials" in body_l or "unauthorized" in body_l):
                return True
            return False

        results = []

        # ==================================================================
        # 0. Deutsche Post International (DPI) / "Global Mail" — OAuth 2.0
        #    The AUTHORITATIVE endpoint for DHL Global Mail Business Customers.
        #    Spec: GET /dpi/v1/auth/accesstoken with HTTP Basic Auth
        #    (username = consumerKey, password = consumerSecret).
        #    Source: developer.dhl.com → GMPP Postman collection (v5.7.10).
        # ==================================================================
        def probe_dpi(host, user, pw, label_suffix):
            api = f"DPI Global Mail · {host.split('//')[1]} · {label_suffix}"
            r = requests.get(
                f"{host}/dpi/v1/auth/accesstoken",
                auth=(user, pw),
                headers={"Accept": "application/json"},
                timeout=15,
            )
            body = r.text or ""
            if r.status_code == 200 and ("access_token" in body or "accessToken" in body):
                return {
                    "api": api,
                    "ok": True,
                    "status": "200",
                    "note": "✅ access_token received — THIS IS THE RIGHT API",
                }
            if _rejected(r):
                return {"api": api, "ok": False, "status": str(r.status_code), "note": "credentials rejected"}
            return {"api": api, "ok": False, "status": str(r.status_code), "note": _trunc(body)}

        # Try several auth-variant combinations, because user provided:
        #   userId     = l2006@abv.bg
        #   consumerKey    = T2Lnu62rspJ1wdaI3JOA1JpM7oECmfz2
        #   consumerSecret = 4AIqPAggU2aIPvPE
        # In DPI spec `username = consumerKey, password = consumerSecret`, but
        # older/regional variants use the email as username.
        for host_url, host_label in (
            ("https://api-sandbox.dhl.com", "sandbox"),
            ("https://api.dhl.com", "production"),
        ):
            # Standard: Basic(consumerKey:consumerSecret) — this is the spec.
            results.append(_probe(
                f"DPI {host_label} key:secret",
                lambda h=host_url, lbl=host_label: probe_dpi(h, key, secret, f"Basic({lbl}: consumerKey:consumerSecret)"),
            ))
            # Fallback: Basic(userId:consumerSecret) — some tenants use email.
            if account:
                results.append(_probe(
                    f"DPI {host_label} userId:secret",
                    lambda h=host_url, lbl=host_label: probe_dpi(h, account, secret, f"Basic({lbl}: userId:consumerSecret)"),
                ))
            # Fallback: Basic(userId:consumerKey) — last resort.
            if account:
                results.append(_probe(
                    f"DPI {host_label} userId:key",
                    lambda h=host_url, lbl=host_label: probe_dpi(h, account, key, f"Basic({lbl}: userId:consumerKey)"),
                ))

        # ------------------------------------------------------------------
        # 1. MyDHL API (DHL Express) — Basic Auth
        # ------------------------------------------------------------------
        def probe_mydhl_test():
            api = "MyDHL API (DHL Express) · test"
            r = requests.get(
                "https://express.api.dhl.com/mydhlapi/test/address-validate",
                auth=(key, secret),
                params={"type": "delivery", "countryCode": "DE", "postalCode": "53113"},
                timeout=15,
            )
            if r.status_code == 200:
                return {"api": api, "ok": True, "status": "200", "note": "credentials accepted"}
            if _rejected(r):
                return {"api": api, "ok": False, "status": str(r.status_code), "note": "credentials rejected"}
            return {"api": api, "ok": False, "status": str(r.status_code), "note": _trunc(r.text)}
        results.append(_probe("MyDHL API (DHL Express) · test", probe_mydhl_test))

        # ------------------------------------------------------------------
        # 2. DHL eCommerce Solutions — OAuth 2.0. Try both sandbox and prod
        #    with both GET-query and POST-body variants.
        # ------------------------------------------------------------------
        def probe_ecom(host, method, variant):
            api = f"DHL eCommerce Solutions · {host.split('//')[1]} · {method} {variant}"
            url = f"{host}/auth/v4/accesstoken"
            if method == "GET":
                r = requests.get(url, auth=(key, secret),
                                 params={"grant_type": "client_credentials"}, timeout=15)
            else:
                if variant == "body":
                    r = requests.post(url, auth=(key, secret),
                                      data={"grant_type": "client_credentials"}, timeout=15)
                else:
                    r = requests.post(url, auth=(key, secret),
                                      params={"grant_type": "client_credentials"}, timeout=15)
            body = r.text or ""
            if r.status_code == 200 and ("access_token" in body or "accessToken" in body):
                return {"api": api, "ok": True, "status": "200", "note": "access_token received"}
            if _rejected(r):
                return {"api": api, "ok": False, "status": str(r.status_code), "note": "credentials rejected"}
            return {"api": api, "ok": False, "status": str(r.status_code), "note": _trunc(body)}

        results.append(_probe("eCommerce sandbox GET", lambda: probe_ecom("https://api-sandbox.dhlecs.com", "GET", "query")))
        results.append(_probe("eCommerce sandbox POST body", lambda: probe_ecom("https://api-sandbox.dhlecs.com", "POST", "body")))
        results.append(_probe("eCommerce production GET", lambda: probe_ecom("https://api.dhlecs.com", "GET", "query")))

        # ------------------------------------------------------------------
        # 3. DHL Parcel DE Shipping — both sandbox and production domains.
        # ------------------------------------------------------------------
        def probe_parcel_de(host):
            api = f"DHL Parcel DE Shipping · {host.split('//')[1]}"
            r = requests.get(
                f"{host}/parcel/de/shipping/v2/orders",
                headers={"dpdhl-api-key": key, "Accept": "application/json"},
                auth=(account, secret) if account else None,
                params={"profile": "STANDARD_GRUPPENPROFIL"},
                timeout=15,
            )
            body_l = (r.text or "").lower()
            if r.status_code == 200:
                return {"api": api, "ok": True, "status": "200", "note": "API key accepted"}
            if r.status_code == 400:
                if "api key" in body_l or "dpdhl-api-key" in body_l:
                    return {"api": api, "ok": False, "status": "400", "note": "DPDHL-API-Key invalid"}
                return {"api": api, "ok": True, "status": "400", "note": "API key OK; user/password/payload issue"}
            if _rejected(r):
                return {"api": api, "ok": False, "status": str(r.status_code), "note": "credentials rejected"}
            return {"api": api, "ok": False, "status": str(r.status_code), "note": _trunc(r.text)}
        results.append(_probe("Parcel DE sandbox", lambda: probe_parcel_de("https://api-sandbox.dhl.com")))
        results.append(_probe("Parcel DE production", lambda: probe_parcel_de("https://api-eu.dhl.com")))

        # ------------------------------------------------------------------
        # 4. DHL Track & Trace Unified API — only requires DHL-API-Key header.
        # ------------------------------------------------------------------
        def probe_track_unified():
            api = "DHL Shipment Tracking Unified (api-eu)"
            r = requests.get(
                "https://api-eu.dhl.com/track/shipments",
                headers={"DHL-API-Key": key, "Accept": "application/json"},
                params={"trackingNumber": "00340434292135100186"},
                timeout=15,
            )
            body_l = (r.text or "").lower()
            if r.status_code in (200, 404):
                return {"api": api, "ok": True, "status": str(r.status_code), "note": "API key accepted"}
            if _rejected(r):
                return {"api": api, "ok": False, "status": str(r.status_code), "note": "API key rejected"}
            return {"api": api, "ok": False, "status": str(r.status_code), "note": _trunc(r.text)}
        results.append(_probe("Tracking Unified", probe_track_unified))

        # ------------------------------------------------------------------
        # 5. DHL Location Finder — requires DHL-API-Key header.
        # ------------------------------------------------------------------
        def probe_location():
            api = "DHL Location Finder"
            r = requests.get(
                "https://api-eu.dhl.com/location-finder/v1/find-by-address",
                headers={"DHL-API-Key": key, "Accept": "application/json"},
                params={"countryCode": "DE", "postalCode": "53113"},
                timeout=15,
            )
            if r.status_code == 200:
                return {"api": api, "ok": True, "status": "200", "note": "API key accepted"}
            if _rejected(r):
                return {"api": api, "ok": False, "status": str(r.status_code), "note": "API key rejected"}
            return {"api": api, "ok": False, "status": str(r.status_code), "note": _trunc(r.text)}
        results.append(_probe("Location Finder", probe_location))

        # ------------------------------------------------------------------
        # 6. DHL Parcel DE Customer Account — Basic Auth on token endpoint.
        # ------------------------------------------------------------------
        def probe_parcel_de_account(host):
            api = f"Parcel DE Customer Account · {host.split('//')[1]}"
            r = requests.post(
                f"{host}/parcel/de/account/auth/v1/accesstoken",
                auth=(key, secret),
                timeout=15,
            )
            if r.status_code == 200 and "accessToken" in (r.text or ""):
                return {"api": api, "ok": True, "status": "200", "note": "accessToken received"}
            if _rejected(r):
                return {"api": api, "ok": False, "status": str(r.status_code), "note": "credentials rejected"}
            return {"api": api, "ok": False, "status": str(r.status_code), "note": _trunc(r.text)}
        results.append(_probe("Parcel DE Account sandbox", lambda: probe_parcel_de_account("https://api-sandbox.dhl.com")))
        results.append(_probe("Parcel DE Account production", lambda: probe_parcel_de_account("https://api-eu.dhl.com")))

        # ------------------------------------------------------------------
        # 7. DHL Deutsche Post International (mail)
        # ------------------------------------------------------------------
        def probe_dp_intl():
            api = "DHL Deutsche Post International (mail)"
            r = requests.get(
                "https://api-eu.dhl.com/mail/intl/tracking/v1/packages",
                headers={"DHL-API-Key": key},
                auth=(key, secret),
                timeout=15,
            )
            body_l = (r.text or "").lower()
            if r.status_code in (200, 400) and "invalid credentials" not in body_l and "unauthorized" not in body_l:
                return {"api": api, "ok": True, "status": str(r.status_code), "note": "credentials accepted"}
            if _rejected(r):
                return {"api": api, "ok": False, "status": str(r.status_code), "note": "credentials rejected"}
            return {"api": api, "ok": False, "status": str(r.status_code), "note": _trunc(r.text)}
        results.append(_probe("Deutsche Post International", probe_dp_intl))

        return results

    def _run_dpi_test_order(self, key: str, secret: str, ekp: str, test_mode: bool):
        """
        Create a tiny test order in DPI sandbox to verify the EKP number
        and payload structure. Tries several EKP format variants automatically
        (9 vs 10 digits, with/without leading zero).
        """
        import requests
        import json as _json

        host = "https://api-sandbox.dhl.com" if test_mode else "https://api.dhl.com"
        results = {"host": host, "test_mode": test_mode, "steps": []}

        if not (key and secret):
            results["steps"].append({"step": "preflight", "ok": False, "note": "Missing consumerKey/consumerSecret"})
            return results
        if not ekp:
            results["steps"].append({"step": "preflight", "ok": False, "note": "Missing GLOBAL_MAIL_CUSTOMER_EKP"})
            return results

        try:
            tr = requests.get(
                f"{host}/dpi/v1/auth/accesstoken",
                auth=(key, secret),
                headers={"Accept": "application/json"},
                timeout=15,
            )
            if tr.status_code != 200:
                results["steps"].append({
                    "step": "auth", "ok": False,
                    "note": f"HTTP {tr.status_code}: {(tr.text or '')[:300]}",
                })
                return results
            token = (tr.json() or {}).get("access_token") or ""
            results["steps"].append({"step": "auth", "ok": True, "note": f"token ok (len {len(token)})"})
        except Exception as e:
            results["steps"].append({"step": "auth", "ok": False, "note": f"network: {e}"})
            return results

        def build_order(customer_ekp: str):
            return {
                "customerEkp": customer_ekp,
                "orderStatus": "FINALIZE",
                "paperwork": {
                    "contactName": "Marbaras Test",
                    "jobReference": "probe-test",
                    "telephoneNumber": "+359888000000",
                    "awbCopyCount": 1,
                },
                "items": [{
                    "product": "GPT",
                    "serviceLevel": "PRIORITY",
                    "recipient": "John Doe",
                    "recipientPhone": "+4930000000",
                    "recipientEmail": "john@example.com",
                    "addressLine1": "Teststr. 1",
                    "city": "Berlin",
                    "postalCode": "10115",
                    "destinationCountry": "DE",
                    "shipmentAmount": 10.00,
                    "shipmentCurrency": "EUR",
                    "shipmentGrossWeight": 250,
                    "returnItemWanted": False,
                    "custRef": "probe-test",
                    "contents": [{
                        "contentPieceIndexNumber": 1,
                        "contentPieceAmount": 1,
                        "contentPieceDescription": "Silver ring sample",
                        "contentPieceHsCode": "711311",
                        "contentPieceOrigin": "BG",
                        "contentPieceValue": "10.00",
                        "contentPieceNetweight": 250,
                    }],
                }],
            }

        ekp_variants = []
        raw = ekp.strip()
        ekp_variants.append(raw)
        if len(raw) < 10 and raw.isdigit():
            ekp_variants.append(raw.zfill(10))
        if len(raw) == 10 and raw.startswith("0"):
            ekp_variants.append(raw.lstrip("0"))

        seen = set()
        url = f"{host}/dpi/shipping/v1/orders"
        for ekp_try in ekp_variants:
            if ekp_try in seen:
                continue
            seen.add(ekp_try)
            try:
                r = requests.post(
                    url,
                    json=build_order(ekp_try),
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    timeout=30,
                )
                try:
                    body = r.json()
                    pretty = _json.dumps(body, ensure_ascii=False, indent=2)[:2000]
                except Exception:
                    pretty = (r.text or "")[:2000]
                ok = r.status_code in (200, 201)
                results["steps"].append({
                    "step": f"create order (ekp={ekp_try})",
                    "ok": ok,
                    "note": f"HTTP {r.status_code}",
                    "body": pretty,
                })
                if ok:
                    # Try to fetch the PDF label for the first item.
                    try:
                        body = r.json() or {}
                        shipments = body.get("shipments") or []
                        item_id = None
                        if shipments:
                            items = shipments[0].get("items") or []
                            if items:
                                item_id = items[0].get("id")
                        if item_id:
                            label_url = f"{host}/dpi/shipping/v1/items/{item_id}/label"
                            lr = requests.get(
                                label_url,
                                headers={
                                    "Authorization": f"Bearer {token}",
                                    "Accept": "application/pdf",
                                },
                                timeout=30,
                            )
                            results["steps"].append({
                                "step": f"fetch item label (id={item_id})",
                                "ok": lr.status_code == 200 and bool(lr.content),
                                "note": (
                                    f"HTTP {lr.status_code} · "
                                    f"{len(lr.content)} bytes · "
                                    f"Content-Type: {lr.headers.get('Content-Type', '?')}"
                                ),
                            })
                    except Exception as e:
                        results["steps"].append({
                            "step": "fetch item label",
                            "ok": False,
                            "note": f"error: {e}",
                        })
                    break
            except Exception as e:
                results["steps"].append({
                    "step": f"create order (ekp={ekp_try})",
                    "ok": False,
                    "note": f"network: {e}",
                })

        return results

    def barcode_scanner_view(self, request):
        """View for barcode scanner to find orders."""
        from django.shortcuts import render
        from django.http import JsonResponse
        from .models import Order
        
        if request.method == 'POST':
            # Handle barcode scan
            barcode = request.POST.get('barcode', '').strip()
            
            if not barcode:
                return JsonResponse({'error': 'No barcode provided'}, status=400)
            
            # Try to find order by ID or tracking number
            try:
                order_id = int(barcode)
                order = Order.objects.get(pk=order_id)
            except (ValueError, Order.DoesNotExist):
                # Try tracking number
                try:
                    order = Order.objects.get(tracking_number=barcode)
                except Order.DoesNotExist:
                    return JsonResponse({'error': f'Order not found for barcode: {barcode}'}, status=404)
            
            from django.urls import reverse
            order_url = reverse('admin:ecommerce_order_change', args=[order.id])
            return JsonResponse({
                'success': True,
                'order_id': order.id,
                'order_url': order_url,
                'full_name': order.full_name,
                'tracking_number': order.tracking_number or 'Not created',
                'has_label': bool(order.shipping_label_url)
            })
        
        # GET request - show scanner page
        return render(request, 'admin/barcode_scanner.html', {
            'title': 'Barcode Scanner - Find Order'
        })


# ---------- Coupons ----------
@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ("code", "percent_off", "amount_off", "active", "starts_at", "ends_at", "used_count", "usage_limit")
    search_fields = ("code",)
    list_filter = ("active",)
    actions = ["send_coupon_email", "generate_5_percent_coupons"]
    
    def generate_5_percent_coupons(self, request, queryset):
        """Generate 200 new 5% discount coupons."""
        from django.utils import timezone
        from ecommerce.utils.coupons import create_batch
        from django.contrib import messages
        
        now = timezone.now()
        starts_at = now
        ends_at = now + timezone.timedelta(days=365)
        
        try:
            codes = create_batch(
                200,
                prefix="WELCOME-",
                percent_off=5.0,
                starts_at=starts_at,
                ends_at=ends_at,
                usage_limit=1,
                active=True,
            )
            self.message_user(
                request,
                f'Successfully generated {len(codes)} coupons with 5% discount!',
                messages.SUCCESS
            )
        except Exception as e:
            self.message_user(
                request,
                f'Error generating coupons: {str(e)}',
                messages.ERROR
            )
    generate_5_percent_coupons.short_description = "Generate 200 new 5 percent discount coupons"
    readonly_fields = ("send_coupon_button",)
    
    fieldsets = (
        ("Coupon Information", {
            "fields": ("code", "percent_off", "amount_off", "active")
        }),
        ("Validity", {
            "fields": ("starts_at", "ends_at")
        }),
        ("Usage", {
            "fields": ("usage_limit", "used_count")
        }),
        ("Send Coupon", {
            "fields": ("send_coupon_button",),
            "description": "Send this coupon code to a customer via email"
        }),
    )
    
    def send_coupon_button(self, obj):
        """Display button to send coupon via email."""
        if obj.pk:
            from django.urls import reverse
            from django.utils.html import format_html
            url = reverse('admin:ecommerce_coupon_send', args=[obj.pk])
            return format_html(
                '<a href="{}" class="button" style="background: #28a745; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; display: inline-block; font-weight: bold;">📧 Send Coupon via Email</a>',
                url
            )
        return "Save coupon first to send via email"
    send_coupon_button.short_description = "Send Coupon"
    
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('send-coupon/<int:coupon_id>/', self.admin_site.admin_view(self.send_coupon_view), name='ecommerce_coupon_send'),
        ]
        return custom_urls + urls
    
    def send_coupon_view(self, request, coupon_id):
        """View to send coupon via email."""
        from django.shortcuts import get_object_or_404, render, redirect
        from django.contrib import messages
        from ecommerce.models import Coupon, EmailSubscription
        from ecommerce.utils.emailing import send_welcome_email_with_promo
        
        coupon = get_object_or_404(Coupon, pk=coupon_id)
        
        if request.method == 'POST':
            email = request.POST.get('email', '').strip().lower()
            
            if not email:
                messages.error(request, 'Email is required')
                return redirect('admin:ecommerce_coupon_send', coupon_id=coupon_id)
            
            # Validate email
            from django.core.validators import validate_email
            from django.core.exceptions import ValidationError
            try:
                validate_email(email)
            except ValidationError:
                messages.error(request, 'Invalid email address')
                return redirect('admin:ecommerce_coupon_send', coupon_id=coupon_id)
            
            # Check if email already subscribed
            existing_subscription = EmailSubscription.objects.filter(email=email).first()
            if existing_subscription:
                # Update existing subscription with new coupon
                old_coupon_code = existing_subscription.coupon.code if existing_subscription.coupon else "N/A"
                existing_subscription.coupon = coupon
                existing_subscription.save()
                subscription = existing_subscription
                messages.info(request, f'Email {email} already had a subscription with coupon {old_coupon_code}. Updated to new coupon {coupon.code}.')
            else:
                # Create email subscription record
                try:
                    subscription = EmailSubscription.objects.create(
                        email=email,
                        coupon=coupon
                    )
                except Exception as e:
                    messages.error(request, f'Error creating subscription: {str(e)}')
                    return redirect('admin:ecommerce_coupon_send', coupon_id=coupon_id)
            
            # Create temporary user object
            class EmailUser:
                def __init__(self, email):
                    self.email = email
                    self.username = email.split('@')[0]
            
            email_user = EmailUser(email)
            base_url = request.build_absolute_uri('/').rstrip('/')
            
            # Get discount display
            discount_display = ""
            if coupon.percent_off:
                discount_display = f"{coupon.percent_off}% OFF"
            elif coupon.amount_off:
                discount_display = f"${coupon.amount_off} OFF"
            
            # Send email
            try:
                result = send_welcome_email_with_promo(email_user, base_url, coupon.code, discount_display)
                if result:
                    messages.success(request, f'✅ Successfully sent coupon {coupon.code} to {email}')
                else:
                    messages.warning(request, f'⚠️ Email sending failed, but subscription was created. Coupon code: {coupon.code}')
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Error sending coupon email: {e}")
                messages.error(request, f'Error sending email: {str(e)}. Subscription created with code: {coupon.code}')
            
            return redirect('admin:ecommerce_coupon_change', coupon_id)
        
        # GET request - show form
        discount_display = ""
        if coupon.percent_off:
            discount_display = f"{coupon.percent_off}% OFF"
        elif coupon.amount_off:
            discount_display = f"${coupon.amount_off} OFF"
        
        return render(request, 'admin/ecommerce/coupon/send_coupon.html', {
            'coupon': coupon,
            'discount_display': discount_display,
            'opts': self.model._meta,
            'title': f'Send Coupon {coupon.code}',
        })
    
    def send_coupon_email(self, request, queryset):
        """Admin action to send selected coupons via email."""
        if queryset.count() != 1:
            messages.error(request, 'Please select exactly one coupon to send.')
            return
        
        coupon = queryset.first()
        return redirect('admin:ecommerce_coupon_send', coupon_id=coupon.id)
    
    send_coupon_email.short_description = "Send coupon via email"


class BannerImageAdminForm(forms.ModelForm):
    """Custom form for BannerImage to allow large video file uploads."""
    class Meta:
        model = BannerImage
        fields = '__all__'
        widgets = {
            'video_file': forms.FileInput(attrs={'accept': 'video/*'}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Remove any size restrictions from the form field
        if 'video_file' in self.fields:
            # Remove max_length restriction if it exists
            self.fields['video_file'].widget.attrs.pop('max_length', None)
            # Set a very high max_length to allow large files
            self.fields['video_file'].max_length = None
    
    def clean_video_file(self):
        """Custom validation for video file - allow large files."""
        video_file = self.cleaned_data.get('video_file')
        if video_file:
            # Check file size (500MB limit)
            max_size = 524288000  # 500MB
            if hasattr(video_file, 'size') and video_file.size > max_size:
                raise forms.ValidationError(
                    f"Video file is too large ({video_file.size / 1024 / 1024:.2f}MB). Maximum size is 500MB."
                )
        return video_file


@admin.register(LegalPage)
class LegalPageAdmin(admin.ModelAdmin):
    list_display = ('page_type', 'title', 'last_updated')
    list_filter = ('page_type', 'last_updated')
    fieldsets = (
        ('Page Information', {
            'fields': ('page_type', 'title')
        }),
        ('Content', {
            'fields': ('content',),
            'description': 'Enter HTML content for the page. You can use HTML tags for formatting.'
        }),
    )
    readonly_fields = ('last_updated',)
    
    def has_add_permission(self, request):
        # Only allow adding if there are less than 2 pages (privacy + terms)
        return LegalPage.objects.count() < 2
    
    def has_delete_permission(self, request, obj=None):
        # Prevent deletion of legal pages
        return False


@admin.register(EmailSubscription)
class EmailSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('email', 'coupon', 'subscribed_at')
    list_filter = ('subscribed_at', 'coupon')
    search_fields = ('email', 'coupon__code')
    readonly_fields = ('subscribed_at',)
    date_hierarchy = 'subscribed_at'


@admin.register(BannerImage)
class BannerImageAdmin(admin.ModelAdmin):
    form = BannerImageAdminForm
    list_display = ("title", "order", "is_active", "has_image", "has_video", "created_at")
    list_filter = ("is_active", "created_at")
    search_fields = ("title",)
    ordering = ("order", "-created_at")
    fieldsets = (
        ("Basic Information", {
            "fields": ("title", "link_url", "order", "is_active")
        }),
        ("Media", {
            "fields": ("image", "video_file"),
            "description": "Upload either an image OR a video. Video will autoplay, loop, and be muted like a GIF. Maximum video size: 500MB."
        }),
    )
    
    def has_image(self, obj):
        return bool(obj.image)
    has_image.boolean = True
    has_image.short_description = "Has Image"
    
    def has_video(self, obj):
        return bool(obj.video_file)
    has_video.boolean = True
    has_video.short_description = "Has Video"
    
    def save_model(self, request, obj, form, change):
        """Override save to handle validation errors gracefully."""
        try:
            # Check file size before validation if it's a new upload
            if 'video_file' in form.changed_data and obj.video_file:
                max_size = 524288000  # 500MB
                if hasattr(obj.video_file, 'size') and obj.video_file.size > max_size:
                    from django.contrib import messages
                    messages.error(request, f"Video file is too large ({obj.video_file.size / 1024 / 1024:.2f}MB). Maximum size is 500MB.")
                    return
            
            obj.full_clean()
            super().save_model(request, obj, form, change)
            from django.contrib import messages
            messages.success(request, "Banner saved successfully!")
        except Exception as e:
            from django.contrib import messages
            error_msg = str(e)
            # Check if it's a storage space error
            if "No space left on device" in error_msg or "Errno 28" in error_msg:
                messages.error(request, f"Storage space error: {error_msg}. Please check Cloudinary credentials in Railway environment variables (CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET).")
            elif "too large" in error_msg.lower() or "file size" in error_msg.lower():
                messages.error(request, f"File size error: {error_msg}")
            elif "Invalid video file format" in error_msg:
                messages.error(request, f"File format error: {error_msg}")
            else:
                messages.error(request, f"Error saving banner: {error_msg}")
            raise


# =============================================================================
# Marketplace Orders (Amazon / Etsy CSV import → DPI labels)
# =============================================================================
class _MarketplaceCSVUploadForm(forms.Form):
    csv_file = forms.FileField(
        label="Order export (CSV / TSV)",
        help_text="Download from Amazon Seller Central → Reports → Orders, or from Etsy Shop Manager → Orders → Download CSV.",
    )
    marketplace = forms.ChoiceField(
        label="Marketplace",
        choices=[
            ("auto", "Auto-detect"),
            ("amazon", "Amazon"),
            ("etsy", "Etsy"),
        ],
        initial="auto",
        required=True,
    )


class _MarketplaceOrderAdapter:
    """Adapter that lets a :class:`MarketplaceOrder` quack like a website
    :class:`Order` so that the shared DPI payload builder
    (``GlobalMailShipping._prepare_shipment_data``) can consume it.
    """

    def __init__(self, mo: "MarketplaceOrder"):
        from types import SimpleNamespace
        from django.utils import timezone

        self.id = f"MP{mo.id}"
        self.pk = mo.pk
        self.full_name = mo.buyer_name or "Recipient"
        self.email = mo.buyer_email or ""
        self.phone = mo.buyer_phone or ""
        joined = mo.address_line1 or ""
        if mo.address_line2:
            joined = f"{joined}, {mo.address_line2}"
        self.address = joined[:120]
        self.city = mo.city or "City"
        self.postal_code = mo.postal_code or "0000"
        self.country = mo.country or "BG"
        self.total_price = mo.total_amount or Decimal("0")
        self.total = mo.total_amount or Decimal("0")
        self.currency = mo.currency or "EUR"
        # DHL Express _prepare_shipment_data expects a created_at datetime
        self.created_at = mo.imported_at or timezone.now()

        fake_product = SimpleNamespace(
            name=(mo.items_summary or "Silver jewellery")[:60] or "Silver jewellery",
            discount_price=None,
            price=mo.total_amount or Decimal("1"),
        )
        fake_item = SimpleNamespace(
            product=fake_product,
            variant=None,
            quantity=max(int(mo.item_count or 1), 1),
        )

        class _ItemsManager:
            def __init__(self, items):
                self._items = list(items)

            def all(self):
                return list(self._items)

            def __iter__(self):
                return iter(self._items)

            def __len__(self):
                return len(self._items)

        self.items = _ItemsManager([fake_item])


@admin.register(MarketplaceOrder)
class MarketplaceOrderAdmin(admin.ModelAdmin):
    change_list_template = "admin/marketplace_order_changelist.html"

    list_display = (
        "id",
        "marketplace_badge",
        "external_order_id",
        "status_badge",
        "print_label_link",
        "print_awb_link",
        "buyer_name",
        "city",
        "country",
        "tracking_number",
        "total_amount",
        "currency",
        "imported_at",
    )
    list_filter = ("marketplace", "status", "country", "imported_at")
    search_fields = (
        "external_order_id",
        "buyer_name",
        "buyer_email",
        "tracking_number",
        "postal_code",
        "city",
    )
    readonly_fields = (
        "imported_at",
        "label_created_at",
        "shipped_at",
        "dpi_item_id",
        "awb",
        "tracking_number",
        "raw_csv_data",
    )

    fieldsets = (
        ("Source", {"fields": ("marketplace", "external_order_id", "status")}),
        (
            "Buyer",
            {"fields": ("buyer_name", "buyer_email", "buyer_phone")},
        ),
        (
            "Shipping address",
            {
                "fields": (
                    "address_line1",
                    "address_line2",
                    "city",
                    "state",
                    "postal_code",
                    "country",
                )
            },
        ),
        (
            "Package",
            {
                "fields": (
                    "items_summary",
                    "item_count",
                    "total_weight_g",
                    "total_amount",
                    "currency",
                )
            },
        ),
        (
            "Shipping result",
            {
                "fields": (
                    "tracking_number",
                    "dpi_item_id",
                    "awb",
                    "label_created_at",
                    "shipped_at",
                )
            },
        ),
        ("Debug", {"classes": ("collapse",), "fields": ("notes", "raw_csv_data")}),
    )

    actions = ["bulk_create_labels_action", "export_dpi_bulk_csv"]

    # ------------------------------------------------------------------
    # DPI bulk-dispatch CSV export (same format as OrderAdmin)
    # ------------------------------------------------------------------
    def export_dpi_bulk_csv(self, request, queryset):
        """Export selected marketplace orders to DPI prelabeled items CSV."""
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            'attachment; filename="dpi_prelabeled_items.csv"'
        )
        response.write("\ufeff")
        writer = csv.writer(response, delimiter=",", quoting=csv.QUOTE_MINIMAL)

        columns = [
            "PRODUCT", "SERVICE_LEVEL", "CUST_EKP", "AWB",
            "REGISTERED_BARCODE", "CUST_REF", "NAME", "RECIPIENT_PHONE",
            "RECIPIENT_EMAIL", "ADDRESS_LINE_1", "ADDRESS_LINE_2",
            "ADDRESS_LINE_3", "CITY", "STATE", "POSTAL_CODE",
            "DESTINATION_COUNTRY", "WEIGHT", "CONTENT_TYPE", "TOTAL_VALUE",
            "CURRENCY", "HS_CODE", "ORIGIN_COUNTRY",
        ]
        writer.writerow(columns)

        import re as _re

        def _sanitize_phone(raw):
            s = (raw or "").strip()
            if not s:
                return ""
            s = _re.split(
                r"(?i)(?:\s*(?:ext\.?|extension)\b|(?<=\d)\s*x\s*(?=\d)|\bx\b)",
                s, maxsplit=1,
            )[0]
            has_plus = s.lstrip().startswith("+")
            s = _re.sub(r"[^\d\s\.\-\(\)]", "", s)
            if has_plus:
                s = "+" + s.lstrip()
            return s.strip()[:25]

        def _short(s, n):
            s = (s or "").strip()
            return s[:n]

        ekp = str(getattr(settings, "GLOBAL_MAIL_CUSTOMER_EKP", "") or "")
        default_product = getattr(settings, "GLOBAL_MAIL_PRODUCT_CODE", "GPT") or "GPT"
        default_service = getattr(settings, "GLOBAL_MAIL_SERVICE_LEVEL", "PRIORITY") or "PRIORITY"
        default_currency = getattr(settings, "GLOBAL_MAIL_CURRENCY", "EUR") or "EUR"
        origin_country = getattr(settings, "SHOP_COUNTRY", "BG") or "BG"
        default_hs = getattr(settings, "GLOBAL_MAIL_DEFAULT_HS_CODE", "711311") or "711311"
        default_nature = getattr(settings, "GLOBAL_MAIL_NATURE_TYPE", "SALE_GOODS") or "SALE_GOODS"

        _BUILTIN_NON_EU_PRODUCT_MAP = {
            "US": "GPP", "CA": "GPP", "AU": "GPP", "NZ": "GPP", "JP": "GPP",
            "KR": "GPP", "SG": "GPP", "HK": "GPP", "CN": "GPP", "IN": "GPP",
            "BR": "GPP", "MX": "GPP", "AE": "GPP", "IL": "GPP", "ZA": "GPP",
            "TR": "GPP", "CH": "GPP", "NO": "GPP", "IS": "GPP", "GB": "GPP",
        }
        user_map = getattr(settings, "GLOBAL_MAIL_PRODUCT_MAP", {}) or {}
        product_map = {**_BUILTIN_NON_EU_PRODUCT_MAP, **user_map}

        for mo in queryset.order_by("id"):
            dest = ((getattr(mo, "country", "") or "BG").strip() or "BG").upper()
            if len(dest) > 2:
                _iso = {
                    "BULGARIA": "BG", "GERMANY": "DE", "UNITED KINGDOM": "GB",
                    "GREAT BRITAIN": "GB", "UK": "GB", "USA": "US",
                    "UNITED STATES": "US", "FRANCE": "FR", "ITALY": "IT",
                    "SPAIN": "ES", "NETHERLANDS": "NL", "BELGIUM": "BE",
                    "AUSTRIA": "AT", "SWITZERLAND": "CH", "POLAND": "PL",
                }
                dest = _iso.get(dest, dest[:2])

            product = product_map.get(dest) or default_product

            try:
                total_weight_g = int(getattr(mo, "total_weight_g", 0) or 0)
            except Exception:
                total_weight_g = 0
            if total_weight_g <= 0:
                try:
                    total_weight_g = max(int((getattr(mo, "item_count", 1) or 1)) * 500, 100)
                except Exception:
                    total_weight_g = 500

            try:
                order_total = float(getattr(mo, "total_amount", 0) or 0)
            except Exception:
                order_total = 0.0
            if order_total <= 0:
                order_total = 1.0

            is_eu = dest in {
                "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR",
                "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL",
                "PL", "PT", "RO", "SK", "SI", "ES", "SE",
            }
            row = [
                product,
                default_service,
                ekp,
                getattr(mo, "awb", "") or "",
                getattr(mo, "tracking_number", "") or "",
                _short(getattr(mo, "external_order_id", "") or str(mo.id), 30),
                _short(getattr(mo, "buyer_name", "") or "Recipient", 35),
                _sanitize_phone(getattr(mo, "buyer_phone", "") or ""),
                _short(getattr(mo, "buyer_email", "") or "", 80),
                _short(getattr(mo, "address_line1", "") or "", 40),
                _short(getattr(mo, "address_line2", "") or "", 40),
                "",
                _short(getattr(mo, "city", "") or "", 30),
                _short(getattr(mo, "state", "") or "", 30),
                _short(getattr(mo, "postal_code", "") or "", 15),
                dest,
                total_weight_g,
                default_nature,
                "" if is_eu else f"{round(order_total, 2):.2f}",
                getattr(mo, "currency", "") or default_currency,
                "" if is_eu else default_hs,
                origin_country,
            ]
            writer.writerow(row)

        return response

    export_dpi_bulk_csv.short_description = "📄 Export to DPI prelabeled items CSV"

    # ------------------------------------------------------------------
    # Custom URLs
    # ------------------------------------------------------------------
    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "import-csv/",
                self.admin_site.admin_view(self.import_csv_view),
                name="marketplace_order_import_csv",
            ),
            path(
                "<int:pk>/print-label/",
                self.admin_site.admin_view(self.print_label_view),
                name="marketplace_order_print_label",
            ),
            path(
                "<int:pk>/print-awb/",
                self.admin_site.admin_view(self.print_awb_view),
                name="marketplace_order_print_awb",
            ),
            path(
                "bulk-print/",
                self.admin_site.admin_view(self.bulk_print_view),
                name="marketplace_order_bulk_print",
            ),
        ]
        return custom + urls

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------
    @admin.display(description="Marketplace")
    def marketplace_badge(self, obj):
        colors = {
            "amazon": ("#ff9900", "#fff"),
            "etsy": ("#f16521", "#fff"),
            "ebay": ("#0064d2", "#fff"),
            "other": ("#6b7280", "#fff"),
        }
        bg, fg = colors.get(obj.marketplace, ("#6b7280", "#fff"))
        return format_html(
            '<span style="background:{};color:{};padding:2px 8px;border-radius:4px;'
            'font-size:11px;font-weight:600;text-transform:uppercase;">{}</span>',
            bg,
            fg,
            obj.get_marketplace_display(),
        )

    @admin.display(description="Status")
    def status_badge(self, obj):
        colors = {
            "imported": ("#64748b", "#fff"),
            "label_created": ("#0ea5e9", "#fff"),
            "shipped": ("#16a34a", "#fff"),
            "failed": ("#dc2626", "#fff"),
            "cancelled": ("#78716c", "#fff"),
        }
        bg, fg = colors.get(obj.status, ("#64748b", "#fff"))
        return format_html(
            '<span style="background:{};color:{};padding:2px 8px;border-radius:4px;'
            'font-size:11px;font-weight:600;">{}</span>',
            bg,
            fg,
            obj.get_status_display(),
        )

    @admin.display(description="🖨️ Label")
    def print_label_link(self, obj):
        from django.urls import reverse

        try:
            url = reverse("admin:marketplace_order_print_label", args=[obj.pk])
        except Exception:
            return ""
        label = "🖨️ Re-print" if obj.tracking_number else "🖨️ Create"
        bg = "#16a34a" if obj.tracking_number else "#0ea5e9"
        return format_html(
            '<a href="{}" target="_blank" '
            'style="background:{};color:#fff;padding:3px 8px;border-radius:4px;'
            'text-decoration:none;font-size:11px;white-space:nowrap;">{}</a>',
            url,
            bg,
            label,
        )

    @admin.display(description="🧾 AWB")
    def print_awb_link(self, obj):
        """Render a button that downloads the AWB transportation document.
        Only shown when the order already has an AWB number."""
        from django.urls import reverse

        if not obj.awb:
            return format_html(
                '<span style="color:#94a3b8;font-size:11px;">—</span>'
            )
        try:
            url = reverse("admin:marketplace_order_print_awb", args=[obj.pk])
        except Exception:
            return ""
        return format_html(
            '<a href="{}" target="_blank" title="AWB {}" '
            'style="background:#7c3aed;color:#fff;padding:3px 8px;border-radius:4px;'
            'text-decoration:none;font-size:11px;white-space:nowrap;">🧾 AWB</a>',
            url,
            obj.awb,
        )

    # ------------------------------------------------------------------
    # CSV Import view
    # ------------------------------------------------------------------
    def import_csv_view(self, request):
        from ecommerce.utils.marketplace_csv import parse_csv_auto

        context = dict(
            self.admin_site.each_context(request),
            title="Import marketplace orders",
            opts=self.model._meta,
            has_view_permission=True,
        )

        if request.method == "POST":
            form = _MarketplaceCSVUploadForm(request.POST, request.FILES)
            if form.is_valid():
                uploaded = form.cleaned_data["csv_file"]
                forced = form.cleaned_data["marketplace"]
                if forced == "auto":
                    forced = None

                try:
                    content = uploaded.read()
                    if not content:
                        raise ValueError("Uploaded file is empty.")
                    marketplace, parsed = parse_csv_auto(content, forced)
                except Exception as exc:
                    messages.error(request, f"Failed to parse CSV: {exc}")
                    context["form"] = form
                    return render(request, "admin/marketplace_csv_import.html", context)

                created = 0
                skipped = 0
                errors = []
                for entry in parsed:
                    try:
                        obj, is_created = MarketplaceOrder.objects.update_or_create(
                            marketplace=entry["marketplace"],
                            external_order_id=entry["external_order_id"],
                            defaults={
                                "buyer_name": entry["buyer_name"] or "Recipient",
                                "buyer_email": entry.get("buyer_email") or None,
                                "buyer_phone": entry.get("buyer_phone") or None,
                                "address_line1": entry.get("address_line1") or "",
                                "address_line2": entry.get("address_line2") or None,
                                "city": entry.get("city") or "",
                                "state": entry.get("state") or None,
                                "postal_code": entry.get("postal_code") or "",
                                "country": (entry.get("country") or "")[:2].upper(),
                                "items_summary": entry.get("items_summary") or "",
                                "item_count": entry.get("item_count") or 1,
                                "total_weight_g": entry.get("total_weight_g") or 100,
                                "total_amount": entry.get("total_amount") or Decimal("0"),
                                "currency": (entry.get("currency") or "EUR")[:3],
                                "raw_csv_data": entry.get("raw_csv_data"),
                            },
                        )
                        if is_created:
                            created += 1
                        else:
                            skipped += 1
                    except Exception as exc:
                        errors.append(f"Order {entry.get('external_order_id')}: {exc}")

                if created:
                    messages.success(
                        request,
                        f"✅ Imported {created} new {marketplace} order(s).",
                    )
                if skipped:
                    messages.info(
                        request,
                        f"ℹ️ {skipped} existing order(s) updated (already imported).",
                    )
                for err in errors[:10]:
                    messages.error(request, err)

                from django.urls import reverse

                return HttpResponseRedirect(
                    reverse("admin:ecommerce_marketplaceorder_changelist")
                )
        else:
            form = _MarketplaceCSVUploadForm()

        context["form"] = form
        return render(request, "admin/marketplace_csv_import.html", context)

    # ------------------------------------------------------------------
    # Create label & stream PDF
    # ------------------------------------------------------------------
    def print_label_view(self, request, pk):
        """Create a DPI (Deutsche Post International) shipment for the
        marketplace order and stream the 4x6 label PDF back to the browser.
        """
        import requests
        from django.shortcuts import get_object_or_404
        from ecommerce.utils.shipping import GlobalMailShipping

        if not request.user.is_staff:
            return HttpResponse("Staff only.", status=403, content_type="text/plain")

        mo = get_object_or_404(MarketplaceOrder, pk=pk)

        force_sandbox = bool(getattr(settings, "GLOBAL_MAIL_TEST_MODE", True))
        dpi = GlobalMailShipping(force_sandbox=force_sandbox)

        if not (dpi.consumer_key and dpi.consumer_secret and dpi.customer_ekp):
            return HttpResponse(
                "Missing GLOBAL_MAIL_API_KEY / GLOBAL_MAIL_API_SECRET / GLOBAL_MAIL_CUSTOMER_EKP.",
                status=400,
                content_type="text/plain",
            )

        token = dpi._get_access_token()
        if not token:
            return HttpResponse(
                "DPI auth failed — check Railway logs for the exact error. "
                "If you just switched GLOBAL_MAIL_TEST_MODE to False, make "
                "sure your API_KEY/SECRET are the PRODUCTION credentials "
                "from DHL (sandbox keys won't work on api.dhl.com).",
                status=502,
                content_type="text/plain",
            )

        try:
            adapter = _MarketplaceOrderAdapter(mo)
            payload = dpi._prepare_shipment_data(adapter)
            _mp_prefix = {"amazon": "A", "etsy": "E"}.get(mo.marketplace, "M")
            _ext_id = (mo.external_order_id or str(mo.pk)).strip()
            payload["paperwork"]["jobReference"] = f"{_mp_prefix}-{_ext_id}"[:17]
        except Exception as exc:
            mo.status = "failed"
            mo.notes = f"Payload build failed: {exc}"
            mo.save(update_fields=["status", "notes"])
            return HttpResponse(
                f"Failed to build payload: {exc}",
                status=500,
                content_type="text/plain; charset=utf-8",
            )

        def _is_product_error(status_code: int, body_text: str) -> bool:
            """Detect DPI errors that indicate the product code isn't valid for
            this customer/destination, so we can retry with an alternative."""
            if status_code not in (400, 422):
                return False
            t = (body_text or "").lower()
            return (
                "does not exist" in t
                or "destination country is invalid for this product" in t
                or "product is not available" in t
                or "invalid product" in t
            )

        original_product = payload["items"][0].get("product")
        candidates = [original_product]
        for c in ("GPP", "GPT", "GMT", "GMP", "PKM", "PLT", "WPI", "WP", "PKG", "PKI"):
            if c and c not in candidates:
                candidates.append(c)

        r = None
        last_error_text = ""
        for attempt_idx, candidate in enumerate(candidates):
            payload["items"][0]["product"] = candidate
            try:
                r = requests.post(
                    dpi.orders_url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    timeout=30,
                )
            except Exception as exc:
                mo.status = "failed"
                mo.notes = f"DPI create-order exception (product={candidate}): {exc}"
                mo.save(update_fields=["status", "notes"])
                return HttpResponse(
                    f"DPI create-order exception: {exc}",
                    status=502,
                    content_type="text/plain",
                )
            if r.status_code in (200, 201):
                break
            last_error_text = r.text or ""
            if not _is_product_error(r.status_code, last_error_text):
                break

        if r is None or r.status_code not in (200, 201):
            mo.status = "failed"
            mo.notes = (
                f"DPI create-order HTTP {getattr(r, 'status_code', '?')} after "
                f"{len(candidates)} product attempts: {last_error_text[:500]}"
            )
            mo.save(update_fields=["status", "notes"])
            mode_hint = ""
            if getattr(dpi, "test_mode", False):
                mode_hint = (
                    "\n\nℹ️  DPI client is running in SANDBOX / TEST mode "
                    "(GLOBAL_MAIL_TEST_MODE=True or unset).\n"
                    "The sandbox product catalog is very limited and typically "
                    "does NOT include worldwide products for destinations like US.\n"
                    "To fix: set GLOBAL_MAIL_TEST_MODE=False in Railway AND ensure "
                    "your GLOBAL_MAIL_API_KEY / _API_SECRET are the PRODUCTION "
                    "credentials from DPI (different from sandbox ones)."
                )
            return HttpResponse(
                f"DPI create-order failed for {mo.marketplace}#{mo.external_order_id}: "
                f"HTTP {getattr(r, 'status_code', '?')}\n\n"
                f"Tried products: {candidates}\n\n"
                f"Last error:\n{last_error_text[:2000]}"
                f"{mode_hint}\n\nPayload:\n{payload}",
                status=502,
                content_type="text/plain; charset=utf-8",
            )

        body = r.json() or {}
        shipments = body.get("shipments") or []
        if not shipments or not shipments[0].get("items"):
            mo.status = "failed"
            mo.notes = f"DPI returned no shipments/items: {body}"
            mo.save(update_fields=["status", "notes"])
            return HttpResponse(
                f"DPI returned no shipments/items:\n{body}",
                status=502,
                content_type="text/plain; charset=utf-8",
            )
        item_id = shipments[0]["items"][0].get("id")
        awb = shipments[0].get("awb") or shipments[0]["items"][0].get("barcode") or ""
        successful_product = payload["items"][0].get("product")
        if successful_product != original_product:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "DPI: product %s unavailable, succeeded with fallback %s",
                original_product, successful_product,
            )
            mo.notes = (
                (mo.notes or "") +
                f"\n[info] Used fallback product '{successful_product}' "
                f"(requested '{original_product}' unavailable)."
            )

        try:
            lr = requests.get(
                f"{dpi.item_label_url}/{item_id}/label",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/pdf"},
                params=dpi._label_params() or None,
                timeout=30,
            )
            if lr.status_code != 200 or not lr.content:
                mo.status = "failed"
                mo.notes = f"Label fetch HTTP {lr.status_code}: {lr.text[:500]}"
                mo.save(update_fields=["status", "notes"])
                return HttpResponse(
                    f"Label fetch failed (item {item_id}): HTTP {lr.status_code}\n\n{lr.text[:500]}",
                    status=502,
                    content_type="text/plain; charset=utf-8",
                )
        except Exception as exc:
            mo.status = "failed"
            mo.notes = f"Label fetch exception: {exc}"
            mo.save(update_fields=["status", "notes"])
            return HttpResponse(
                f"Label fetch exception: {exc}",
                status=502,
                content_type="text/plain",
            )

        mo.status = "label_created"
        mo.dpi_item_id = str(item_id)
        mo.awb = str(awb)
        mo.tracking_number = str(awb or item_id)
        mo.label_created_at = timezone.now()
        mo.notes = None
        mo.save(
            update_fields=[
                "status",
                "dpi_item_id",
                "awb",
                "tracking_number",
                "label_created_at",
                "notes",
            ]
        )

        pdf_bytes = GlobalMailShipping.refit_pdf_to_4x6(lr.content)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'inline; filename="{mo.marketplace}-{mo.external_order_id}.pdf"'
        )
        response["X-DPI-Item-Id"] = str(item_id)
        response["X-DPI-AWB"] = str(awb)
        return response

    # ------------------------------------------------------------------
    # AWB (transportation document) — required by DPI certification
    # ------------------------------------------------------------------
    def print_awb_view(self, request, pk):
        """Fetch the AWB (Airwaybill / transportation document) PDF for a
        marketplace order whose shipment has already been created.

        The AWB is the master dispatch document that groups items with the
        same product+serviceLevel under a single shipment number. DPI requires
        it for pickup and during certification.
        """
        from django.shortcuts import get_object_or_404
        from ecommerce.utils.shipping import GlobalMailShipping

        if not request.user.is_staff:
            return HttpResponse("Staff only.", status=403, content_type="text/plain")

        mo = get_object_or_404(MarketplaceOrder, pk=pk)

        if not mo.awb:
            return HttpResponse(
                "This order has no AWB yet. Print the item label first — "
                "that call creates the DPI shipment and returns the AWB "
                "number. Then come back here to print the AWB document.",
                status=400,
                content_type="text/plain; charset=utf-8",
            )

        force_sandbox = bool(getattr(settings, "GLOBAL_MAIL_TEST_MODE", True))
        dpi = GlobalMailShipping(force_sandbox=force_sandbox)

        if not (dpi.consumer_key and dpi.consumer_secret and dpi.customer_ekp):
            return HttpResponse(
                "Missing GLOBAL_MAIL_API_KEY / GLOBAL_MAIL_API_SECRET / GLOBAL_MAIL_CUSTOMER_EKP.",
                status=400,
                content_type="text/plain",
            )

        pdf_bytes = dpi.get_awb_label(mo.awb)
        if not pdf_bytes:
            return HttpResponse(
                f"AWB fetch failed for {mo.awb}. Check Railway logs for the "
                f"exact HTTP status/body from DPI. Common causes:\n"
                f"  • The shipment hasn't been FINALIZED on DPI's side yet.\n"
                f"  • The AWB number has expired or was already dispatched.\n"
                f"  • You're in sandbox but the AWB was created in production "
                f"(or vice versa).",
                status=502,
                content_type="text/plain; charset=utf-8",
            )

        refitted = GlobalMailShipping.refit_pdf_to_4x6(pdf_bytes)
        response = HttpResponse(refitted, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'inline; filename="AWB-{mo.awb}.pdf"'
        )
        response["X-DPI-AWB"] = str(mo.awb)
        return response

    # ------------------------------------------------------------------
    # Bulk action (multi-tab opener via intermediate page)
    # ------------------------------------------------------------------
    @admin.action(description="🖨️ Print labels for selected (opens each in a new tab)")
    def bulk_create_labels_action(self, request, queryset):
        from django.urls import reverse

        ids = list(queryset.values_list("pk", flat=True))
        if not ids:
            self.message_user(
                request, "No orders selected.", level=messages.WARNING
            )
            return
        return HttpResponseRedirect(
            reverse("admin:marketplace_order_bulk_print")
            + "?ids=" + ",".join(str(i) for i in ids)
        )

    def bulk_print_view(self, request):
        """Intermediate page that sequentially opens each selected order's
        label in its own browser tab. Required because modern browsers
        block mass ``window.open()`` calls fired outside a direct user
        gesture; the user clicks one button here and tabs are opened
        with a small delay between them to sidestep the popup blocker.
        """
        from django.urls import reverse

        raw_ids = (request.GET.get("ids") or "").strip()
        try:
            ids = [int(x) for x in raw_ids.split(",") if x.strip().isdigit()]
        except Exception:
            ids = []
        orders = list(MarketplaceOrder.objects.filter(pk__in=ids))
        url_pairs = [
            (
                mo,
                reverse("admin:marketplace_order_print_label", args=[mo.pk]),
            )
            for mo in orders
        ]
        context = dict(
            self.admin_site.each_context(request),
            title=f"Print {len(url_pairs)} label(s)",
            opts=self.model._meta,
            url_pairs=url_pairs,
        )
        return render(request, "admin/marketplace_bulk_print.html", context)

    # ------------------------------------------------------------------
    # Changelist context — link to import page
    # ------------------------------------------------------------------
    def changelist_view(self, request, extra_context=None):
        from django.urls import reverse

        extra_context = extra_context or {}
        try:
            extra_context["import_csv_url"] = reverse(
                "admin:marketplace_order_import_csv"
            )
        except Exception:
            extra_context["import_csv_url"] = ""
        return super().changelist_view(request, extra_context=extra_context)

