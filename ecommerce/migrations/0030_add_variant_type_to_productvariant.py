# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ecommerce', '0029_add_email_subscription_model'),
    ]

    operations = [
        migrations.AddField(
            model_name='productvariant',
            name='variant_type',
            field=models.CharField(choices=[('ring_size', 'Ring Size'), ('zodiac_sign', 'Zodiac Sign'), ('other', 'Other')], db_index=True, default='ring_size', help_text='Type of variant', max_length=20),
        ),
        migrations.AlterField(
            model_name='productvariant',
            name='size',
            field=models.CharField(blank=True, help_text='Ring size (for ring variants) or zodiac sign (for zodiac variants)', max_length=20, null=True),
        ),
        migrations.AlterUniqueTogether(
            name='productvariant',
            unique_together={('product', 'variant_type', 'size')},
        ),
        migrations.RemoveConstraint(
            model_name='productvariant',
            name='ecommerce_productvariant_product_id_size_uniq',
        ),
    ]
