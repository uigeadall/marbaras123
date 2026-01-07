# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ecommerce', '0021_add_gold_plated_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='productimage',
            name='version_type',
            field=models.CharField(
                choices=[('silver', 'Silver'), ('gold_plated', 'Gold Plated'), ('rose_gold_plated', 'Rose Gold Plated')],
                default='silver',
                help_text='Select the version type for this image: Silver, Gold Plated, or Rose Gold Plated',
                max_length=20
            ),
        ),
    ]

