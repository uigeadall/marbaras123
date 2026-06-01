from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ecommerce", "0043_remove_adminappearance"),
    ]

    operations = [
        migrations.AlterField(
            model_name="marketplaceorder",
            name="status",
            field=models.CharField(
                choices=[
                    ("imported", "Imported"),
                    ("prepared", "Preparing at DHL"),
                    ("label_created", "Label created"),
                    ("shipped", "Shipped"),
                    ("failed", "Failed"),
                    ("cancelled", "Cancelled"),
                ],
                db_index=True,
                default="imported",
                max_length=20,
            ),
        ),
    ]
