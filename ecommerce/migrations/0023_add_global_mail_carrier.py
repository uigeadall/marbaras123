# Generated manually
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ecommerce', '0022_add_version_type_field'),
    ]

    operations = [
        migrations.AlterField(
            model_name='order',
            name='shipping_carrier',
            field=models.CharField(
                blank=True,
                choices=[
                    ('fedex', 'FedEx'),
                    ('dhl', 'DHL'),
                    ('deutsche_post', 'Deutsche Post'),
                    ('global_mail', 'Global Mail'),
                ],
                help_text='Shipping carrier for this order',
                max_length=50,
                null=True
            ),
        ),
    ]

