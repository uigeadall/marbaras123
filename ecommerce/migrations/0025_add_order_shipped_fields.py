# Generated migration for adding shipped fields to Order model

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ecommerce', '0024_add_order_currency'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='is_shipped',
            field=models.BooleanField(default=False, help_text='Mark order as shipped'),
        ),
        migrations.AddField(
            model_name='order',
            name='shipped_at',
            field=models.DateTimeField(blank=True, help_text='Date and time when order was shipped', null=True),
        ),
    ]
