
from django.contrib import admin
from django.db.models import Count, Sum, Case, When, Value
from django.http import HttpResponse
from django.contrib import messages
from django.utils.html import format_html
from django.shortcuts import redirect
from django import forms
from django.conf import settings
from decimal import Decimal
import csv

from .models import (
    BlogPost, BannerImage,
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

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    inlines = [ProductImageInline, ProductVariantInline, ProductBundleItemInline]
    
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
                "fields": ("stock", "cart_add_count")
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
admin.site.register(Favorite)
admin.site.register(Discount)
admin.site.register(ShippingOption)



@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "id",
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

    search_fields = ("id", "full_name", "email", "phone", "address", "city", "postal_code", "tracking_number")
    list_filter = ("shipping_option", "shipping_carrier", "coupon", "created_at")
    list_select_related = ("shipping_option", "coupon", "user")
    readonly_fields = ("created_at", "tracking_number", "shipment_id", "shipping_label_url", "create_label_button", "fedex_copy_paste")
    
    fieldsets = (
        ("Order Information", {
            "fields": ("user", "email", "created_at", "total_price", "coupon")
        }),
        ("Shipping", {
            "fields": ("shipping_option", "shipping_carrier", "create_label_button", "tracking_number", "shipment_id", "shipping_label_url")
        }),
        ("Customer Details", {
            "fields": ("full_name", "phone", "address", "city", "postal_code", "country")
        }),
        ("FedEx Copy-Paste", {
            "fields": ("fedex_copy_paste",),
            "description": "Copy all order details formatted for FedEx portal with one click"
        }),
    )

    actions = ["export_orders_csv", "send_shipped_email", "print_shipping_labels", "create_shipping_labels"]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(_items_count=Count("items"))

    @admin.display(description="Items")
    def items_count(self, obj):
        return getattr(obj, "_items_count", 0)

    @admin.display(description="Coupon")
    def coupon_code(self, obj):
        return obj.coupon.code if obj.coupon else "-"

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
        
        # Get shop information
        shop_name = getattr(settings, 'SHOP_NAME', 'Marbaras')
        shop_address = getattr(settings, 'SHOP_ADDRESS', '')
        shop_city = getattr(settings, 'SHOP_CITY', 'Sofia')
        shop_postal = getattr(settings, 'SHOP_POSTAL_CODE', '')
        shop_country = getattr(settings, 'SHOP_COUNTRY', 'BG')
        shop_phone = getattr(settings, 'SHOP_PHONE', '')
        shop_email = getattr(settings, 'SHOP_EMAIL', '')
        
        # Truncate full_name to 35 characters (FedEx limit)
        contact_name = obj.full_name[:35] if len(obj.full_name) > 35 else obj.full_name
        
        # Format data for FedEx Ship Manager - one field per line, ready to paste
        # Format: Field Label: Value (for manual copy-paste)
        formatted_data = f"""=== RECIPIENT (ДОСТАВЯНЕ НА) ===
Copy each value separately and paste into the corresponding field:

ИМЕ ЗА КОНТАКТ (CONTACT NAME):
{contact_name}

ФИРМА (COMPANY):
(leave empty)

ТЕЛЕФОНЕН НОМЕР (PHONE NUMBER):
{obj.phone}

ИМЕЙЛ (EMAIL):
{obj.email or ''}

ДЪРЖАВА/ТЕРИТОРИЯ (COUNTRY):
{obj.country or ''}

ПОЛЕ 1 ЗА АДРЕС (ADDRESS FIELD 1):
{obj.address[:35]}

ПОЛЕ 2 ЗА АДРЕС (ADDRESS FIELD 2):
{obj.address[35:70] if len(obj.address) > 35 else ''}

ПОЩЕНСКИ КОД (POSTAL CODE):
{obj.postal_code}

ГРАД (CITY):
{obj.city}

=== SHIPPER (ИЗПРАЩАНЕ ОТ) ===
(Already filled: {shop_name})

=== NOTES ===
Order ID: #{obj.id}
Items: {', '.join([f"{item.product.name} x{item.quantity}" for item in obj.items.all()[:3]])}
"""
        
        # Create individual copy buttons for each field
        return format_html(
            '''
            <div style="margin: 10px 0; font-family: Arial, sans-serif;">
                <h3 style="color: #667eea; margin-bottom: 15px;">📋 Copy Individual Fields for FedEx Ship Manager</h3>
                
                <div style="background: #f9f9f9; padding: 15px; border-radius: 4px; margin-bottom: 15px;">
                    <strong>ИМЕ ЗА КОНТАКТ (CONTACT NAME):</strong><br>
                    <div style="background: white; padding: 8px; border: 1px solid #ddd; border-radius: 4px; margin: 5px 0; display: inline-block; min-width: 300px; font-family: monospace;">
                        <span id="contact-name-{}">{}</span>
                    </div>
                    <button onclick="copyField('contact-name-{}')" style="background: #667eea; color: white; padding: 5px 15px; border: none; border-radius: 4px; cursor: pointer; margin-left: 10px;">Copy</button>
                </div>
                
                <div style="background: #f9f9f9; padding: 15px; border-radius: 4px; margin-bottom: 15px;">
                    <strong>ТЕЛЕФОНЕН НОМЕР (PHONE NUMBER):</strong><br>
                    <div style="background: white; padding: 8px; border: 1px solid #ddd; border-radius: 4px; margin: 5px 0; display: inline-block; min-width: 300px; font-family: monospace;">
                        <span id="phone-{}">{}</span>
                    </div>
                    <button onclick="copyField('phone-{}')" style="background: #667eea; color: white; padding: 5px 15px; border: none; border-radius: 4px; cursor: pointer; margin-left: 10px;">Copy</button>
                </div>
                
                <div style="background: #f9f9f9; padding: 15px; border-radius: 4px; margin-bottom: 15px;">
                    <strong>ИМЕЙЛ (EMAIL):</strong><br>
                    <div style="background: white; padding: 8px; border: 1px solid #ddd; border-radius: 4px; margin: 5px 0; display: inline-block; min-width: 300px; font-family: monospace;">
                        <span id="email-{}">{}</span>
                    </div>
                    <button onclick="copyField('email-{}')" style="background: #667eea; color: white; padding: 5px 15px; border: none; border-radius: 4px; cursor: pointer; margin-left: 10px;">Copy</button>
                </div>
                
                <div style="background: #f9f9f9; padding: 15px; border-radius: 4px; margin-bottom: 15px;">
                    <strong>ПОЛЕ 1 ЗА АДРЕС (ADDRESS FIELD 1):</strong><br>
                    <div style="background: white; padding: 8px; border: 1px solid #ddd; border-radius: 4px; margin: 5px 0; display: inline-block; min-width: 300px; font-family: monospace;">
                        <span id="address1-{}">{}</span>
                    </div>
                    <button onclick="copyField('address1-{}')" style="background: #667eea; color: white; padding: 5px 15px; border: none; border-radius: 4px; cursor: pointer; margin-left: 10px;">Copy</button>
                </div>
                
                <div style="background: #f9f9f9; padding: 15px; border-radius: 4px; margin-bottom: 15px;">
                    <strong>ПОЛЕ 2 ЗА АДРЕС (ADDRESS FIELD 2):</strong><br>
                    <div style="background: white; padding: 8px; border: 1px solid #ddd; border-radius: 4px; margin: 5px 0; display: inline-block; min-width: 300px; font-family: monospace;">
                        <span id="address2-{}">{}</span>
                    </div>
                    <button onclick="copyField('address2-{}')" style="background: #667eea; color: white; padding: 5px 15px; border: none; border-radius: 4px; cursor: pointer; margin-left: 10px;">Copy</button>
                </div>
                
                <div style="background: #f9f9f9; padding: 15px; border-radius: 4px; margin-bottom: 15px;">
                    <strong>ПОЩЕНСКИ КОД (POSTAL CODE):</strong><br>
                    <div style="background: white; padding: 8px; border: 1px solid #ddd; border-radius: 4px; margin: 5px 0; display: inline-block; min-width: 300px; font-family: monospace;">
                        <span id="postal-{}">{}</span>
                    </div>
                    <button onclick="copyField('postal-{}')" style="background: #667eea; color: white; padding: 5px 15px; border: none; border-radius: 4px; cursor: pointer; margin-left: 10px;">Copy</button>
                </div>
                
                <div style="background: #f9f9f9; padding: 15px; border-radius: 4px; margin-bottom: 15px;">
                    <strong>ГРАД (CITY):</strong><br>
                    <div style="background: white; padding: 8px; border: 1px solid #ddd; border-radius: 4px; margin: 5px 0; display: inline-block; min-width: 300px; font-family: monospace;">
                        <span id="city-{}">{}</span>
                    </div>
                    <button onclick="copyField('city-{}')" style="background: #667eea; color: white; padding: 5px 15px; border: none; border-radius: 4px; cursor: pointer; margin-left: 10px;">Copy</button>
                </div>
                
                <div style="background: #f9f9f9; padding: 15px; border-radius: 4px; margin-bottom: 15px;">
                    <strong>ДЪРЖАВА/ТЕРИТОРИЯ (COUNTRY):</strong><br>
                    <div style="background: white; padding: 8px; border: 1px solid #ddd; border-radius: 4px; margin: 5px 0; display: inline-block; min-width: 300px; font-family: monospace;">
                        <span id="country-{}">{}</span>
                    </div>
                    <button onclick="copyField('country-{}')" style="background: #667eea; color: white; padding: 5px 15px; border: none; border-radius: 4px; cursor: pointer; margin-left: 10px;">Copy</button>
                </div>
                
                <div style="margin-top: 20px; padding: 10px; background: #e8f4f8; border-radius: 4px; font-size: 12px;">
                    <strong>💡 Tip:</strong> Click "Copy" next to each field, then paste (Ctrl+V / Cmd+V) into the corresponding field in FedEx Ship Manager.
                </div>
            </div>
            <script>
                function copyField(fieldId) {{
                    var element = document.getElementById(fieldId);
                    var text = element.textContent;
                    
                    // Create temporary textarea
                    var textarea = document.createElement('textarea');
                    textarea.value = text;
                    textarea.style.position = 'fixed';
                    textarea.style.opacity = '0';
                    document.body.appendChild(textarea);
                    textarea.select();
                    textarea.setSelectionRange(0, 99999);
                    
                    try {{
                        document.execCommand('copy');
                        // Show feedback
                        var button = event.target;
                        var originalText = button.textContent;
                        button.textContent = '✅ Copied!';
                        button.style.background = '#28a745';
                        setTimeout(function() {{
                            button.textContent = originalText;
                            button.style.background = '#667eea';
                        }}, 1500);
                    }} catch (err) {{
                        alert('Failed to copy. Please select and copy manually.');
                    }}
                    
                    document.body.removeChild(textarea);
                }}
            </script>
            ''',
            obj.id, contact_name, obj.id,
            obj.id, obj.phone, obj.id,
            obj.id, obj.email or '', obj.id,
            obj.id, obj.address[:35], obj.id,
            obj.id, obj.address[35:70] if len(obj.address) > 35 else '', obj.id,
            obj.id, obj.postal_code, obj.id,
            obj.id, obj.city, obj.id,
            obj.id, obj.country or '', obj.id
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
            return redirect('admin:ecommerce_order_change', order_id)
        
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
        
        return redirect('admin:ecommerce_order_change', order_id)
    
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

