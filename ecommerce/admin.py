
from django.contrib import admin
from django.db.models import Count, Sum, Case, When, Value
from django.http import HttpResponse
from django.contrib import messages
from django.utils.html import format_html
from django.shortcuts import redirect
from django import forms
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

    search_fields = ("id", "full_name", "email", "phone", "address", "city", "postal_code")
    list_filter = ("shipping_option", "coupon", "created_at")
    list_select_related = ("shipping_option", "coupon", "user")
    readonly_fields = ()

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
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="orders.csv"'
        writer = csv.writer(response)
        writer.writerow([
            "id", "created_at", "full_name", "email", "phone",
            "address", "city", "postal_code",
            "shipping_option", "coupon", "total_price", "items_count"
        ])

        counts = (
            OrderItem.objects
            .filter(order__in=queryset)
            .values("order_id")
            .annotate(c=Sum("quantity"))
        )
        counts_map = {row["order_id"]: row["c"] for row in counts}

        for o in queryset.select_related("shipping_option", "coupon"):
            writer.writerow([
                o.id, o.created_at, o.full_name, o.email, o.phone,
                o.address, o.city, o.postal_code,
                (o.shipping_option.name if o.shipping_option else ""),
                (o.coupon.code if o.coupon else ""),
                o.total_price,
                counts_map.get(o.id, 0),
            ])
        return response

    export_orders_csv.short_description = "Export selected orders to CSV"
    
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
        
        created_count = 0
        failed_count = 0
        
        for order in queryset:
            if not order.shipping_carrier:
                failed_count += 1
                continue
            
            label_data = create_shipping_label(order, order.shipping_carrier)
            
            if label_data:
                order.tracking_number = label_data.get('tracking_number')
                order.shipping_label_url = label_data.get('label_url')
                order.shipment_id = label_data.get('shipment_id')
                order.save(update_fields=['tracking_number', 'shipping_label_url', 'shipment_id'])
                created_count += 1
            else:
                failed_count += 1
        
        if created_count > 0:
            self.message_user(
                request,
                f"✅ Created {created_count} shipping labels.",
                messages.SUCCESS
            )
        if failed_count > 0:
            self.message_user(
                request,
                f"⚠️ {failed_count} labels could not be created (check carrier configuration or select carrier).",
                messages.WARNING
            )
    
    create_shipping_labels.short_description = "📦 Create shipping labels via carrier API"
    
    def get_urls(self):
        """Add custom URL for printing shipping labels."""
        from django.urls import path
        urls = super().get_urls()
        custom_urls = [
            path(
                '<int:order_id>/print-label/',
                self.admin_site.admin_view(self.print_shipping_label_view),
                name='print_shipping_label',
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

