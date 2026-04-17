# Align DB with models: Product/ProductImage no longer have these columns.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("ecommerce", "0035_customerreview_related_product"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="product",
            name="is_gold_plated",
        ),
        migrations.RemoveField(
            model_name="productimage",
            name="is_gold_plated",
        ),
        migrations.RemoveField(
            model_name="productimage",
            name="version_type",
        ),
    ]
