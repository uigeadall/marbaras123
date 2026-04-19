from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ecommerce", "0040_marketplace_order"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="awb",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="DPI / Global Mail Airwaybill number (master transportation document)",
                max_length=80,
                null=True,
            ),
        ),
    ]
