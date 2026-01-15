# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ecommerce', '0030_add_variant_type_to_productvariant'),
    ]

    operations = [
        migrations.AlterField(
            model_name='productvariant',
            name='variant_type',
            field=models.CharField(
                choices=[
                    ('ring_size', 'Ring Size'),
                    ('earring_hoop_size', 'Earring Hoop Size'),
                    ('zodiac_sign', 'Zodiac Sign'),
                    ('other', 'Other')
                ],
                db_index=True,
                default='ring_size',
                help_text='Type of variant',
                max_length=20
            ),
        ),
        migrations.AlterField(
            model_name='productvariant',
            name='size',
            field=models.CharField(
                blank=True,
                help_text='Ring size (for ring variants), earring hoop size (for earring variants), or zodiac sign (for zodiac variants)',
                max_length=20,
                null=True
            ),
        ),
    ]
