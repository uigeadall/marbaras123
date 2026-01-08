# Generated migration for adding currency field to Order model

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ecommerce', '0023_add_global_mail_carrier'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='currency',
            field=models.CharField(default='EUR', help_text='Currency code (EUR, GBP, USD, BGN, etc.)', max_length=3),
        ),
    ]
