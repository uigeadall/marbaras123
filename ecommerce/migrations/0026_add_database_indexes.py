# Generated migration for adding database indexes for performance optimization

from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ('ecommerce', '0025_add_order_shipped_fields'),
    ]

    operations = [
        # Product indexes - най-често използвани заявки
        migrations.AlterField(
            model_name='product',
            name='name',
            field=models.CharField(db_index=True, max_length=200),
        ),
        migrations.AlterField(
            model_name='product',
            name='slug',
            field=models.SlugField(blank=True, db_index=True, help_text='URL-friendly version of product name', max_length=250, unique=True),
        ),
        migrations.AlterField(
            model_name='product',
            name='category',
            field=models.ForeignKey(db_index=True, help_text='Primary category (for backward compatibility)', on_delete=models.CASCADE, related_name='products', to='ecommerce.category'),
        ),
        migrations.AlterField(
            model_name='product',
            name='stock',
            field=models.PositiveIntegerField(db_index=True, default=0),
        ),
        migrations.AlterField(
            model_name='product',
            name='cart_add_count',
            field=models.PositiveIntegerField(db_index=True, default=0),
        ),
        
        # Category indexes
        migrations.AlterField(
            model_name='category',
            name='name',
            field=models.CharField(db_index=True, max_length=100),
        ),
        migrations.AlterField(
            model_name='category',
            name='slug',
            field=models.SlugField(blank=True, db_index=True),
        ),
        migrations.AlterField(
            model_name='category',
            name='parent',
            field=models.ForeignKey(blank=True, db_index=True, help_text='Select a parent category to make this a sub-category', null=True, on_delete=models.CASCADE, related_name='subcategories', to='ecommerce.category'),
        ),
        
        # Order indexes - критични за admin и user dashboard
        migrations.AlterField(
            model_name='order',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, db_index=True),
        ),
        migrations.AlterField(
            model_name='order',
            name='is_shipped',
            field=models.BooleanField(db_index=True, default=False, help_text='Mark order as shipped'),
        ),
        migrations.AlterField(
            model_name='order',
            name='shipped_at',
            field=models.DateTimeField(blank=True, db_index=True, help_text='Date and time when order was shipped', null=True),
        ),
        migrations.AlterField(
            model_name='order',
            name='email',
            field=models.EmailField(blank=True, db_index=True, null=True),
        ),
        migrations.AlterField(
            model_name='order',
            name='user',
            field=models.ForeignKey(blank=True, db_index=True, null=True, on_delete=models.CASCADE, to='auth.user'),
        ),
        
        # CartItem indexes - за бързо достъпване на количката
        migrations.AlterField(
            model_name='cartitem',
            name='user',
            field=models.ForeignKey(blank=True, db_index=True, null=True, on_delete=models.CASCADE, to='auth.user'),
        ),
        migrations.AlterField(
            model_name='cartitem',
            name='session_key',
            field=models.CharField(blank=True, db_index=True, max_length=40, null=True),
        ),
        migrations.AlterField(
            model_name='cartitem',
            name='product',
            field=models.ForeignKey(db_index=True, on_delete=models.CASCADE, to='ecommerce.product'),
        ),
        
        # Favorite indexes - за бързо достъпване на favorites
        migrations.AlterField(
            model_name='favorite',
            name='user',
            field=models.ForeignKey(db_index=True, on_delete=models.CASCADE, related_name='favorites', to='auth.user'),
        ),
        migrations.AlterField(
            model_name='favorite',
            name='product',
            field=models.ForeignKey(db_index=True, on_delete=models.CASCADE, related_name='favorited_by', to='ecommerce.product'),
        ),
        migrations.AlterField(
            model_name='favorite',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, db_index=True),
        ),
        
        # ProductVariant indexes - за бързо търсене по размер
        migrations.AlterField(
            model_name='productvariant',
            name='product',
            field=models.ForeignKey(db_index=True, on_delete=models.CASCADE, related_name='variants', to='ecommerce.product'),
        ),
        migrations.AlterField(
            model_name='productvariant',
            name='stock',
            field=models.PositiveIntegerField(db_index=True, default=0, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AlterField(
            model_name='productvariant',
            name='sku',
            field=models.CharField(blank=True, db_index=True, max_length=64, null=True, unique=True),
        ),
        
        # OrderItem indexes
        migrations.AlterField(
            model_name='orderitem',
            name='order',
            field=models.ForeignKey(db_index=True, on_delete=models.CASCADE, related_name='items', to='ecommerce.order'),
        ),
        migrations.AlterField(
            model_name='orderitem',
            name='product',
            field=models.ForeignKey(db_index=True, on_delete=models.CASCADE, to='ecommerce.product'),
        ),
        
        # Comment indexes - за бързо показване на коментари
        migrations.AlterField(
            model_name='comment',
            name='product',
            field=models.ForeignKey(db_index=True, on_delete=models.CASCADE, related_name='comments', to='ecommerce.product'),
        ),
        migrations.AlterField(
            model_name='comment',
            name='user',
            field=models.ForeignKey(db_index=True, on_delete=models.CASCADE, to='auth.user'),
        ),
        migrations.AlterField(
            model_name='comment',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, db_index=True),
        ),
        
        # Rating indexes
        migrations.AlterField(
            model_name='rating',
            name='product',
            field=models.ForeignKey(db_index=True, on_delete=models.CASCADE, related_name='ratings', to='ecommerce.product'),
        ),
        migrations.AlterField(
            model_name='rating',
            name='user',
            field=models.ForeignKey(db_index=True, on_delete=models.CASCADE, to='auth.user'),
        ),
        
        # ProductImage indexes
        migrations.AlterField(
            model_name='productimage',
            name='product',
            field=models.ForeignKey(db_index=True, on_delete=models.CASCADE, related_name='images', to='ecommerce.product'),
        ),
        
        # BlogPost indexes
        migrations.AlterField(
            model_name='blogpost',
            name='title',
            field=models.CharField(db_index=True, max_length=200),
        ),
        migrations.AlterField(
            model_name='blogpost',
            name='slug',
            field=models.SlugField(blank=True, db_index=True, unique=True),
        ),
        migrations.AlterField(
            model_name='blogpost',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, db_index=True),
        ),
        migrations.AlterField(
            model_name='blogpost',
            name='is_published',
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AlterField(
            model_name='blogpost',
            name='order',
            field=models.IntegerField(db_index=True, default=0, help_text='Order for display (lower numbers first)'),
        ),
        
        # Coupon indexes
        migrations.AlterField(
            model_name='coupon',
            name='code',
            field=models.CharField(db_index=True, max_length=40, unique=True),
        ),
        migrations.AlterField(
            model_name='coupon',
            name='active',
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AlterField(
            model_name='coupon',
            name='starts_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AlterField(
            model_name='coupon',
            name='ends_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
