from decimal import Decimal
import secrets
import string

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify



RING_SIZE_CHOICES = [(s, s) for s in [
    "48","49","50","51","52","53","54","55","56","57","58","59","60","61","62","63","64","65","66"
]]


class Category(models.Model):
    """Product category (e.g., Rings, Necklaces, etc.) with support for sub-categories."""
    name = models.CharField(max_length=100)
    slug = models.SlugField(blank=True)
    image = models.ImageField(upload_to="categories/", blank=True, null=True, help_text="Image for sub-category display")
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='subcategories',
        help_text="Select a parent category to make this a sub-category"
    )
    
    class Meta:
        verbose_name = "Category"
        verbose_name_plural = "Categories"
        ordering = ["name"]
        unique_together = [['slug', 'parent']]

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name or "")
            self.slug = base_slug
        super().save(*args, **kwargs)
        # Invalidate categories cache when category is saved
        from django.core.cache import cache
        cache.delete('all_categories')
        cache.delete('all_categories_ids')

    def __str__(self) -> str:
        if self.parent:
            return f"{self.parent.name} > {self.name}"
        return self.name
    
    def get_full_path(self) -> str:
        """Return full category path (e.g., 'Jewelry > Rings > Wedding Rings')."""
        path = [self.name]
        current = self.parent
        while current:
            path.insert(0, current.name)
            current = current.parent
        return " > ".join(path)


class Product(models.Model):
    """Main product entity."""
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=250, unique=True, blank=True, help_text="URL-friendly version of product name")
    description = models.TextField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    discount_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name="products",
        help_text="Primary category (for backward compatibility)",
    )
    categories = models.ManyToManyField(
        Category,
        related_name="categorized_products",
        blank=True,
        help_text="All categories this product belongs to (including Sale)",
    )

    image = models.ImageField(upload_to="products/", blank=True, null=True)
    serial_number = models.CharField(
        max_length=50,
        unique=True,
        blank=True,
        null=True,
        validators=[RegexValidator(r"^[\w\-\.]+$")]
    )
    brand = models.CharField(max_length=50, blank=True, null=True)
    is_gold_plated = models.BooleanField(
        default=False,
        help_text="Check if this product is gold plated. When checked, gold plated images will be shown. When unchecked, normal images will be shown."
    )

    cart_add_count = models.PositiveIntegerField(default=0)
    stock = models.PositiveIntegerField(default=0)
    sale_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the product should be automatically removed from Sale category. Leave empty if no expiration.",
    )

    class Meta:
        ordering = ["-id"]
        verbose_name = "Product"
        verbose_name_plural = "Products"

    def save(self, *args, **kwargs):
        """Generate slug from name if not provided."""
        if not self.slug:
            base_slug = slugify(self.name)
            slug = base_slug
            counter = 1
            # Ensure uniqueness
            while Product.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        # Ensure primary category is also in categories
        super().save(*args, **kwargs)
        if self.category and self.category not in self.categories.all():
            self.categories.add(self.category)


    bundle_items = models.ManyToManyField(
        "self",
        through="ProductBundleItem",
        symmetrical=False,
        blank=True,
        related_name="bundled_with",
    )

    def get_discounted_price(self) -> Decimal:
        """Return discount_price if valid and sale hasn't expired, else base price."""
        from django.utils import timezone
        if self.discount_price and self.discount_price < self.price:
            # Check if sale has expired
            if self.sale_expires_at:
                if timezone.now() > self.sale_expires_at:
                    return self.price
            return self.discount_price
        return self.price

    def has_discount(self) -> bool:
        """Check if product has valid discount (not expired)."""
        from django.utils import timezone
        if not (self.discount_price and self.discount_price < self.price):
            return False
        # Check if sale has expired
        if self.sale_expires_at:
            if timezone.now() > self.sale_expires_at:
                return False
        return True

    def save(self, *args, **kwargs):
        """Generate random slug if not provided and ensure primary category is in categories."""
        if not self.slug:
            # Generate random alphanumeric slug (8-12 characters)
            length = 10
            alphabet = string.ascii_lowercase + string.digits
            slug = ''.join(secrets.choice(alphabet) for _ in range(length))
            
            # Ensure uniqueness
            counter = 1
            while Product.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = ''.join(secrets.choice(alphabet) for _ in range(length))
                counter += 1
                # Safety check to prevent infinite loop
                if counter > 100:
                    # Fallback: use timestamp + random
                    import time
                    slug = f"{int(time.time())}{''.join(secrets.choice(alphabet) for _ in range(6))}"
                    break
            self.slug = slug
        # Ensure primary category is also in categories
        super().save(*args, **kwargs)
        if self.category and self.category not in self.categories.all():
            self.categories.add(self.category)

    @property
    def is_ring(self) -> bool:
        """Heuristic: category slug or name indicates a ring."""
        slug = (self.category.slug or "").lower()
        name = (self.category.name or "").lower()
        return slug == "rings" or "ring" in name or "пръстен" in name

    def get_manual_bundle_qs(self):
        """Fetch the admin-picked bundle items in order."""
        return (
            Product.objects
            .filter(
                bundled_as_item__product=self,
                bundled_as_item__is_active=True,
            )
            .select_related("category")
            .order_by("bundled_as_item__position", "-id")
        )

    def is_favorited(self, user) -> bool:
        """Convenience helper; avoids hitting .all() in templates."""
        if not user or not user.is_authenticated:
            return False
        return Favorite.objects.filter(user=user, product=self).exists()

    @property
    def main_image(self):
        """Return the main product image - either the direct image field or the first ProductImage."""
        if self.image:
            return self.image
        # Try to get the first ProductImage (cached if prefetched)
        if hasattr(self, '_prefetched_objects_cache') and 'images' in self._prefetched_objects_cache:
            images = self._prefetched_objects_cache['images']
            if images:
                return images[0].image
        # Fallback to query if not prefetched
        first_image = self.images.first()
        if first_image:
            return first_image.image
        return None

    def __str__(self) -> str:
        return self.name


class ProductBundleItem(models.Model):
    """
    Connects a 'product' (the page you're on) to another Product ('item')
    that should appear under 'Buy as a set'.
    """
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="bundle_links"
    )
    item = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="bundled_as_item"
    )
    position = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    note = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["product", "item"], name="uniq_product_item_bundle")
        ]

    def __str__(self) -> str:
        return f"{self.product} → {self.item} (pos {self.position})"


class ProductVariant(models.Model):
    """Size/variant only for ring-type products."""
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='variants')
    size = models.CharField(max_length=10, choices=RING_SIZE_CHOICES)
    price_override = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    stock = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)])
    sku = models.CharField(max_length=64, blank=True, null=True, unique=True)

    class Meta:
        unique_together = (('product', 'size'),)
        ordering = ['product_id', 'size']
        verbose_name = "Product Variant"
        verbose_name_plural = "Product Variants"

    def clean(self):
        if self.product_id and not self.product.is_ring:
            raise ValidationError("Sizes/variants are allowed only for ring products.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def effective_price(self) -> Decimal:
        return self.price_override if self.price_override is not None else self.product.get_discounted_price()

    def __str__(self) -> str:
        return f"{self.product.name} — size {self.size}"


class ProductImage(models.Model):
    product = models.ForeignKey(Product, related_name='images', on_delete=models.CASCADE)
    image = models.ImageField(upload_to='products/multiple/')
    is_gold_plated = models.BooleanField(
        default=False,
        help_text="Check if this image is for the gold plated version of the product. Leave unchecked for normal version images."
    )

    class Meta:
        verbose_name = "Product Image"
        verbose_name_plural = "Product Images"

    def __str__(self) -> str:
        version = "Gold Plated" if self.is_gold_plated else "Normal"
        return f"{self.product.name} image ({version})"


class Rating(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='ratings')
    value = models.IntegerField(choices=[(i, str(i)) for i in range(1, 6)])

    class Meta:
        unique_together = ('user', 'product')
        ordering = ["-id"]
        verbose_name = "Rating"
        verbose_name_plural = "Ratings"

    def __str__(self) -> str:
        return f"{self.product.name} - {self.value}⭐ by {self.user.username}"


class Discount(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='discounts')
    percentage = models.DecimalField(max_digits=5, decimal_places=2)
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()

    def __str__(self) -> str:
        return f"{self.percentage}% off {self.product.name} from {self.start_date} to {self.end_date}"


class CartItem(models.Model):
    """Supports both authenticated users and guest carts (session_key)."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    session_key = models.CharField(max_length=40, null=True, blank=True)

    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    variant = models.ForeignKey(ProductVariant, on_delete=models.SET_NULL, null=True, blank=True)

    quantity = models.PositiveIntegerField(default=1)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Cart Item"
        verbose_name_plural = "Cart Items"
        ordering = ["-added_at"]
        constraints = [
            # Authenticated user, with variant
            models.UniqueConstraint(
                fields=['user', 'product', 'variant'],
                name='unique_user_product_variant',
                condition=models.Q(user__isnull=False, variant__isnull=False),
            ),
            # Authenticated user, no variant
            models.UniqueConstraint(
                fields=['user', 'product'],
                name='unique_user_product_no_variant',
                condition=models.Q(user__isnull=False, variant__isnull=True),
            ),
            # Guest (session), with variant
            models.UniqueConstraint(
                fields=['session_key', 'product', 'variant'],
                name='unique_session_product_variant',
                condition=models.Q(session_key__isnull=False, variant__isnull=False),
            ),
            # Guest (session), no variant
            models.UniqueConstraint(
                fields=['session_key', 'product'],
                name='unique_session_product_no_variant',
                condition=models.Q(session_key__isnull=False, variant__isnull=True),
            ),
        ]

    def clean(self):
        if not self.user and not self.session_key:
            raise ValidationError("Either user or session_key must be provided.")
        if self.user and self.session_key:
            raise ValidationError("Cannot have both user and session_key.")
    
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        base = f"{self.quantity} x {self.product.name}"
        return f"{base} ({self.variant.size})" if self.variant_id else base


class Favorite(models.Model):
    """
    A user's favorite (wish-list) product.
    Reverse from Product is 'favorited_by' (a queryset of Favorite rows).
    Filter products via: Product.objects.filter(favorited_by__user=request.user)
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='favorites')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='favorited_by')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'product')
        ordering = ["-created_at"]
        verbose_name = "Favorite"
        verbose_name_plural = "Favorites"

    def __str__(self) -> str:
        return f"{self.user.username} ❤️ {self.product.name}"


class Comment(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='comments')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Comment"
        verbose_name_plural = "Comments"


class ShippingOption(models.Model):
    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=6, decimal_places=2)
    delivery_time = models.CharField(max_length=100)

    def display_label(self):
        """Return display label based on price."""
        if self.price == 0 or self.price == Decimal("0.00"):
            return "Free Shipping"
        elif self.price == Decimal("19.99") or self.price == 19.99:
            return "Express Shipping"
        return self.name

    def __str__(self) -> str:
        return f"{self.name} (${self.price}) - {self.delivery_time}"


class Coupon(models.Model):
    code = models.CharField(max_length=40, unique=True)
    percent_off = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    amount_off = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    usage_limit = models.IntegerField(null=True, blank=True)
    used_count = models.IntegerField(default=0)
    
    class Meta:
        ordering = ["code"]
        verbose_name = "Coupon"
        verbose_name_plural = "Coupons"

    def is_valid_now(self) -> bool:
        now = timezone.now()
        if not self.active:
            return False
        if self.starts_at and now < self.starts_at:
            return False
        if self.ends_at and now > self.ends_at:
            return False
        if self.usage_limit and self.used_count >= self.usage_limit:
            return False
        return True

    def clean(self):
        if not self.percent_off and not self.amount_off:
            raise ValidationError("Either percent_off or amount_off must be provided.")
        if self.percent_off and self.amount_off:
            raise ValidationError("Cannot have both percent_off and amount_off.")
        if self.percent_off and (self.percent_off <= 0 or self.percent_off > 100):
            raise ValidationError("percent_off must be between 0 and 100.")
        if self.amount_off and self.amount_off <= 0:
            raise ValidationError("amount_off must be greater than 0.")
        if self.starts_at and self.ends_at and self.starts_at >= self.ends_at:
            raise ValidationError("ends_at must be after starts_at.")
    
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def apply(self, subtotal: Decimal) -> Decimal:
        """Apply absolute or percent discount; clamp at zero; keep 2 decimals."""
        if self.amount_off:
            return max(Decimal("0.00"), subtotal - self.amount_off)
        if self.percent_off:
            return (subtotal * (Decimal("100") - self.percent_off) / Decimal("100")).quantize(Decimal("0.01"))
        return subtotal

    def __str__(self) -> str:
        state = "active" if self.active else "inactive"
        return f"{self.code} ({state})"


class Order(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    shipping_option = models.ForeignKey(ShippingOption, on_delete=models.SET_NULL, null=True, blank=True)
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    stripe_checkout_id = models.CharField(max_length=255, blank=True, null=True, unique=True)

    # Shipping carrier integration fields
    shipping_carrier = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        choices=[
            ('fedex', 'FedEx'),
            ('dhl', 'DHL'),
            ('deutsche_post', 'Deutsche Post'),
        ],
        help_text="Shipping carrier for this order"
    )
    tracking_number = models.CharField(max_length=100, blank=True, null=True, help_text="Tracking number from carrier")
    shipping_label_url = models.URLField(blank=True, null=True, help_text="URL to shipping label PDF")
    shipment_id = models.CharField(max_length=100, blank=True, null=True, help_text="Carrier shipment ID")

    email = models.EmailField(blank=True, null=True)
    full_name = models.CharField(max_length=100)
    country = models.CharField(max_length=100, blank=True, null=True)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=20)
    phone = models.CharField(max_length=20)

    coupon = models.ForeignKey(Coupon, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Order"
        verbose_name_plural = "Orders"

    def clean(self):
        if not self.user and not self.email:
            raise ValidationError("Either user or email must be provided.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def get_total_with_shipping(self) -> Decimal:
        if self.shipping_option:
            return self.total_price + self.shipping_option.price
        return self.total_price

    def __str__(self) -> str:
        return f"Order #{self.pk} by {self.full_name}"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    variant = models.ForeignKey(ProductVariant, on_delete=models.SET_NULL, null=True, blank=True)
    quantity = models.PositiveIntegerField()

    class Meta:
        verbose_name = "Order Item"
        verbose_name_plural = "Order Items"
        ordering = ["order", "id"]

    def __str__(self) -> str:
        label = f"{self.quantity} x {self.product.name}"
        if self.variant:
            return f"{label} (size {self.variant.size}) in order #{self.order_id}"
        return f"{label} in order #{self.order_id}"


class UserProfile(models.Model):
    """User profile to store shipping and contact information."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    phone = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(blank=True, null=True, help_text="Shipping email (can differ from account email)")
    address = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    postal_code = models.CharField(max_length=20, blank=True, null=True)
    country = models.CharField(max_length=100, blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User Profile"
        verbose_name_plural = "User Profiles"

    def __str__(self) -> str:
        return f"Profile for {self.user.username}"


class BlogPost(models.Model):
    """Blog post model for articles."""
    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True, blank=True)
    content = models.TextField()
    excerpt = models.TextField(max_length=500, blank=True, help_text="Short summary for preview")
    image = models.ImageField(upload_to="blog/", blank=True, null=True, help_text="Featured image for blog post")
    video_file = models.FileField(
        upload_to="blog/videos/", 
        blank=True, 
        null=True, 
        help_text="Upload a video file (MP4, WebM, OGG). Max size: 100MB"
    )
    video_url = models.URLField(blank=True, null=True, help_text="Optional video URL (YouTube, Vimeo, etc.) or upload a file above")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_published = models.BooleanField(default=True)
    order = models.IntegerField(default=0, help_text="Order for display (lower numbers first)")

    class Meta:
        ordering = ["order", "-created_at"]
        verbose_name = "Blog Post"
        verbose_name_plural = "Blog Posts"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.title


class BannerImage(models.Model):
    """Banner images/videos for the home page carousel."""
    # Get storage dynamically to ensure Cloudinary is used if configured
    # Create Cloudinary storage instance directly to avoid default_storage initialization issues
    @staticmethod
    def _get_storage():
        from django.conf import settings
        # Always try to use Cloudinary if credentials are available
        if hasattr(settings, 'CLOUDINARY_CLOUD_NAME') and settings.CLOUDINARY_CLOUD_NAME:
            try:
                # Use custom storage for large files (videos)
                from ecommerce.storage_large import LargeFileCloudinaryStorage
                return LargeFileCloudinaryStorage()
            except (ImportError, AttributeError, Exception) as e:
                # Fallback to regular Cloudinary storage
                try:
                    from cloudinary_storage.storage import MediaCloudinaryStorage
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.warning(f"Using regular Cloudinary storage (large file storage failed): {e}")
                    return MediaCloudinaryStorage()
                except Exception as fallback_error:
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.error(f"Failed to initialize Cloudinary storage: {fallback_error}")
        # If Cloudinary is not available, this will fail - we don't want to use local storage
        raise Exception("Cloudinary storage is required but not properly configured. Please check CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET environment variables.")
    
    image = models.ImageField(
        upload_to="banners/", 
        blank=True, 
        null=True, 
        storage=_get_storage(),
        help_text="Banner image for carousel"
    )
    video_file = models.FileField(
        upload_to="banners/videos/", 
        blank=True, 
        null=True,
        max_length=None,  # Remove default 10MB limit
        storage=_get_storage(),
        help_text="Upload a video file (MP4, WebM, OGG). Video will autoplay, loop, and be muted like a GIF. Max size: 500MB"
    )
    title = models.CharField(max_length=200, blank=True, help_text="Optional title/alt text")
    link_url = models.URLField(blank=True, null=True, help_text="Optional link URL when banner is clicked")
    is_active = models.BooleanField(default=True, help_text="Show this banner in carousel")
    order = models.IntegerField(default=0, help_text="Display order (lower numbers first)")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "-created_at"]
        verbose_name = "Banner Image"
        verbose_name_plural = "Banner Images"

    def clean(self):
        """Validate that at least one of image or video_file is provided, and check file sizes."""
        from django.core.exceptions import ValidationError
        
        if not self.image and not self.video_file:
            raise ValidationError("You must provide either an image or a video file.")
        
        # Validate video file size (500MB = 524288000 bytes)
        if self.video_file:
            max_size = 524288000  # 500MB
            if hasattr(self.video_file, 'size') and self.video_file.size > max_size:
                raise ValidationError(f"Video file is too large. Maximum size is 500MB. Your file is {self.video_file.size / 1024 / 1024:.2f}MB.")
            
            # Validate video file extension
            allowed_extensions = ['.mp4', '.webm', '.ogg', '.mov', '.avi']
            if hasattr(self.video_file, 'name'):
                file_ext = self.video_file.name.lower()
                if not any(file_ext.endswith(ext) for ext in allowed_extensions):
                    raise ValidationError(f"Invalid video file format. Allowed formats: {', '.join(allowed_extensions)}")

    def save(self, *args, **kwargs):
        """Override save to call clean validation."""
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.title or f"Banner {self.id}"

