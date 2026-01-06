# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ecommerce', '0019_add_video_to_banner'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='shipping_carrier',
            field=models.CharField(blank=True, choices=[('fedex', 'FedEx'), ('dhl', 'DHL'), ('deutsche_post', 'Deutsche Post')], help_text='Shipping carrier for this order', max_length=50, null=True),
        ),
        migrations.AddField(
            model_name='order',
            name='tracking_number',
            field=models.CharField(blank=True, help_text='Tracking number from carrier', max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='order',
            name='shipping_label_url',
            field=models.URLField(blank=True, help_text='URL to shipping label PDF', null=True),
        ),
        migrations.AddField(
            model_name='order',
            name='shipment_id',
            field=models.CharField(blank=True, help_text='Carrier shipment ID', max_length=100, null=True),
        ),
    ]

