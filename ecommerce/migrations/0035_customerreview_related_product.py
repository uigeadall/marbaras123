import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ecommerce", "0034_customerreview_rating"),
    ]

    operations = [
        migrations.AddField(
            model_name="customerreview",
            name="related_product",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional: product thumbnail and Purchased link (Etsy-style card).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="customer_reviews",
                to="ecommerce.product",
            ),
        ),
    ]
