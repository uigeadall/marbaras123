# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ecommerce', '0020_add_shipping_carrier_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='is_gold_plated',
            field=models.BooleanField(
                default=False,
                help_text='Check if this product is gold plated. When checked, gold plated images will be shown. When unchecked, normal images will be shown.'
            ),
        ),
        migrations.AddField(
            model_name='productimage',
            name='is_gold_plated',
            field=models.BooleanField(
                default=False,
                help_text='Check if this image is for the gold plated version of the product. Leave unchecked for normal version images.'
            ),
        ),
        migrations.AlterModelOptions(
            name='productimage',
            options={'verbose_name': 'Product Image', 'verbose_name_plural': 'Product Images'},
        ),
    ]

