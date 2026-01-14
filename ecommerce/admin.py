
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
    BlogPost, BannerImage, LegalPage, EmailSubscription,
    Category, Product, ProductImage, ProductVariant,
    CartItem, Order, OrderItem, Favorite, Discount, ShippingOption, Coupon, ProductBundleItem
)
from .utils.emailing import send_order_shipped_email


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1
    max_num = 10
    fields = ("image", "version_type", "is_gold_plated")
    verbose_name = "Product Image"
    verbose_name_plural = "Product Images"

class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1
    fields = ("size", "stock", "price_override", "sku")

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
    actions = ['increase_prices_action', 'decrease_prices_action']
    
    def get_list_display(self, request):
        """Dynamically get list_display to handle missing sale_expires_at field."""
        base_fields = ("name", "serial_number", "price", "discount_price", "category", "brand", "cart_add_count")
        # Check if sale_expires_at field exists
        try:
            from ecommerce.models import Product
            if hasattr(Product, 'sale_expires_at'):
                return base_fields + ("sale_expires_at",)
        except:
            pass
        return base_fields
    
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
                "fields": ("stock", "recently_sold", "cart_add_count")
            }),
            ("Images", {
                "fields": ("image",)
            }),
            ("Product Variant", {
                "fields": ("is_gold_plated",),
                "description": "Check if this product is gold plated. When checked, only gold plated images will be shown. When unchecked, only normal images will be shown.",
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
            
            # Handle different actions
            if action == 'view':
                # Just return current stock info
                from django.urls import reverse
                product_url = reverse('admin:ecommerce_product_change', args=[product.id])
                return JsonResponse({
                    'success': True,
                    'action': 'view',
                    'product_id': product.id,
                    'product_name': product.name,
                    'product_url': product_url,
                    'variant_size': variant.size if variant else None,
                    'current_stock': current_stock,
                    'quantity': 0,
                    'message': f'Current stock: {current_stock}'
                })
            
            elif action == 'add':
                # Increase stock by quantity
                with transaction.atomic():
                    if variant:
                        variant.stock += quantity
                        variant.save(update_fields=['stock'])
                        new_stock = variant.stock
                    else:
                        product.stock += quantity
                        product.save(update_fields=['stock'])
                        new_stock = product.stock
                
                from django.urls import reverse
                product_url = reverse('admin:ecommerce_product_change', args=[product.id])
                return JsonResponse({
                    'success': True,
                    'action': 'add',
                    'product_id': product.id,
                    'product_name': product.name,
                    'product_url': product_url,
                    'variant_size': variant.size if variant else None,
                    'current_stock': current_stock,
                    'new_stock': new_stock,
                    'quantity': quantity,
                    'message': f'Stock increased by {quantity}! New stock: {new_stock}'
                })
            
            elif action == 'remove':
                # Decrease stock by quantity
                with transaction.atomic():
                    if variant:
                        if variant.stock >= quantity:
                            variant.stock -= quantity
                            variant.save(update_fields=['stock'])
                            new_stock = variant.stock
                        else:
                            return JsonResponse({
                                'success': False,
                                'error': f'Product "{product.name}" (Size: {variant.size}) has only {variant.stock} in stock, cannot remove {quantity}!'
                            }, status=400)
                    else:
                        if product.stock >= quantity:
                            product.stock -= quantity
                            product.save(update_fields=['stock'])
                            new_stock = product.stock
                        else:
                            return JsonResponse({
                                'success': False,
                                'error': f'Product "{product.name}" has only {product.stock} in stock, cannot remove {quantity}!'
                            }, status=400)
                
                from django.urls import reverse
                product_url = reverse('admin:ecommerce_product_change', args=[product.id])
                return JsonResponse({
                    'success': True,
                    'action': 'remove',
                    'product_id': product.id,
                    'product_name': product.name,
                    'product_url': product_url,
                    'variant_size': variant.size if variant else None,
                    'current_stock': current_stock,
                    'new_stock': new_stock,
                    'quantity': quantity,
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
admin.site.register(OrderItem)


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
    list_display = (
        "id",
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

    search_fields = ("id", "full_name", "email", "phone", "address", "city", "postal_code", "tracking_number")
    list_filter = ("is_shipped", "shipping_option", "shipping_carrier", "coupon", "created_at")
    list_select_related = ("shipping_option", "coupon", "user")
    readonly_fields = ("created_at", "shipment_id", "shipping_label_url", "create_label_button", "fedex_copy_paste", "shipped_at")
    
    fieldsets = (
        ("Order Information", {
            "fields": ("user", "email", "created_at", "total_price", "coupon")
        }),
        ("Shipping", {
            "fields": ("shipping_option", "shipping_carrier", "create_label_button", "tracking_number", "shipment_id", "shipping_label_url", "is_shipped", "shipped_at")
        }),
        ("Customer Details", {
            "fields": ("full_name", "phone", "address", "city", "postal_code", "country")
        }),
        ("FedEx Copy-Paste", {
            "fields": ("fedex_copy_paste",),
            "description": "Copy all order details formatted for FedEx portal with one click"
        }),
    )

    actions = ["export_orders_csv", "send_shipped_email", "print_shipping_labels", "create_shipping_labels", "mark_as_shipped", "mark_as_not_shipped"]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(_items_count=Count("items"))
    
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
                        <li>Click in the first field (ИМЕ ЗА КОНТАКТ / Contact Name)</li>
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
                    <strong>📝 Field Order (for reference):</strong>
                    <ol style="margin: 10px 0; padding-left: 20px; font-size: 12px;">
                        <li>ИМЕ ЗА КОНТАКТ (Contact Name) - <code>{}</code></li>
                        <li>ФИРМА (Company) - <em>leave empty</em></li>
                        <li>ТЕЛЕФОНЕН НОМЕР (Phone) - <code>{}</code></li>
                        <li>ИМЕЙЛ (Email) - <code>{}</code></li>
                        <li>ПОЛЕ 1 ЗА АДРЕС (Address 1) - <code>{}</code></li>
                        <li>ПОЛЕ 2 ЗА АДРЕС (Address 2) - <code>{}</code></li>
                        <li>ПОЩЕНСКИ КОД (Postal Code) - <code>{}</code></li>
                        <li>ГРАД (City) - <code>{}</code></li>
                        <li>ДЪРЖАВА/ТЕРИТОРИЯ (Country) - <code>{}</code></li>
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
        """Admin action to create shipping labels via carrier APIs."""
        from ecommerce.utils.shipping import create_shipping_label
        import logging
        
        logger = logging.getLogger(__name__)
        created_count = 0
        failed_count = 0
        error_details = []
        
        for order in queryset:
            if not order.shipping_carrier:
                failed_count += 1
                error_details.append(f"Order #{order.id}: No carrier selected")
                continue
            
            logger.info(f"Creating shipping label for Order #{order.id} with carrier {order.shipping_carrier}")
            label_data = create_shipping_label(order, order.shipping_carrier)
            
            if label_data:
                order.tracking_number = label_data.get('tracking_number')
                order.shipping_label_url = label_data.get('label_url')
                order.shipment_id = label_data.get('shipment_id')
                order.save(update_fields=['tracking_number', 'shipping_label_url', 'shipment_id'])
                created_count += 1
                logger.info(f"✅ Successfully created label for Order #{order.id}: tracking={order.tracking_number}")
            else:
                failed_count += 1
                error_details.append(f"Order #{order.id}: Label creation failed (check logs)")
                logger.error(f"❌ Failed to create label for Order #{order.id} with carrier {order.shipping_carrier}")
        
        if created_count > 0:
            self.message_user(
                request,
                f"✅ Created {created_count} shipping labels.",
                messages.SUCCESS
            )
        if failed_count > 0:
            error_msg = f"⚠️ {failed_count} labels could not be created."
            if error_details:
                error_msg += f" Details: {', '.join(error_details[:3])}"  # Show first 3 errors
            self.message_user(
                request,
                error_msg + " Check Railway logs for more details.",
                messages.WARNING
            )
    
    create_shipping_labels.short_description = "📦 Create shipping labels via carrier API"
    
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
            order.save(update_fields=['tracking_number', 'shipping_label_url', 'shipment_id'])
            messages.success(request, f"✅ Successfully created shipping label for Order #{order.id}. Tracking: {order.tracking_number}")
            logger.info(f"✅ Successfully created label for Order #{order.id}: tracking={order.tracking_number}")
        else:
            messages.error(request, f"❌ Failed to create shipping label for Order #{order.id}. Check Railway logs for details.")
            logger.error(f"❌ Failed to create label for Order #{order.id} with carrier {order.shipping_carrier}")
        
        from django.urls import reverse
        return redirect(reverse('admin:ecommerce_order_change', args=[order_id]))
    
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

