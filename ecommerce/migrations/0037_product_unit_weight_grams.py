from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ecommerce", "0036_remove_product_gold_plated_and_image_version_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="unit_weight_grams",
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text="Тегло на един артикул в грамове (за склад / метал).",
                max_digits=10,
                null=True,
                verbose_name="Грамаж на една бройка (g)",
            ),
        ),
        migrations.AlterField(
            model_name="product",
            name="stock",
            field=models.PositiveIntegerField(
                db_index=True,
                default=0,
                help_text="За продукт без варианти: бройки на склад. При варианти наличността е по редовете „Variants“; общата сума се показва в админа по-долу.",
                verbose_name="Наличност (бройки)",
            ),
        ),
    ]
