from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ecommerce", "0039_productreview_user_session_unique"),
    ]

    operations = [
        migrations.CreateModel(
            name="MarketplaceOrder",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "marketplace",
                    models.CharField(
                        choices=[
                            ("amazon", "Amazon"),
                            ("etsy", "Etsy"),
                            ("ebay", "eBay"),
                            ("other", "Other"),
                        ],
                        db_index=True,
                        default="amazon",
                        max_length=20,
                    ),
                ),
                ("external_order_id", models.CharField(db_index=True, max_length=120)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("imported", "Imported"),
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
                ("buyer_name", models.CharField(max_length=120)),
                ("buyer_email", models.EmailField(blank=True, max_length=254, null=True)),
                ("buyer_phone", models.CharField(blank=True, max_length=40, null=True)),
                ("address_line1", models.CharField(max_length=200)),
                ("address_line2", models.CharField(blank=True, max_length=200, null=True)),
                ("city", models.CharField(max_length=100)),
                ("state", models.CharField(blank=True, max_length=80, null=True)),
                ("postal_code", models.CharField(max_length=20)),
                (
                    "country",
                    models.CharField(
                        help_text="ISO 3166-1 alpha-2 country code", max_length=2
                    ),
                ),
                (
                    "items_summary",
                    models.TextField(
                        help_text="Human-readable description of items, e.g. '2x Silver Ring / 1x Bracelet'"
                    ),
                ),
                ("item_count", models.PositiveIntegerField(default=1)),
                (
                    "total_weight_g",
                    models.PositiveIntegerField(
                        default=100, help_text="Total package weight in grams"
                    ),
                ),
                ("total_amount", models.DecimalField(decimal_places=2, max_digits=10)),
                ("currency", models.CharField(default="EUR", max_length=3)),
                (
                    "tracking_number",
                    models.CharField(blank=True, db_index=True, max_length=80, null=True),
                ),
                ("dpi_item_id", models.CharField(blank=True, max_length=120, null=True)),
                ("awb", models.CharField(blank=True, max_length=80, null=True)),
                ("label_created_at", models.DateTimeField(blank=True, null=True)),
                ("shipped_at", models.DateTimeField(blank=True, null=True)),
                ("imported_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("notes", models.TextField(blank=True, null=True)),
                (
                    "raw_csv_data",
                    models.JSONField(
                        blank=True,
                        help_text="Original CSV row for debugging",
                        null=True,
                    ),
                ),
            ],
            options={
                "verbose_name": "Marketplace Order",
                "verbose_name_plural": "Marketplace Orders",
                "ordering": ["-imported_at"],
                "unique_together": {("marketplace", "external_order_id")},
            },
        ),
        migrations.AddIndex(
            model_name="marketplaceorder",
            index=models.Index(
                fields=["marketplace", "status"],
                name="ecommerce_m_marketp_af8bff_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="marketplaceorder",
            index=models.Index(
                fields=["-imported_at"],
                name="ecommerce_m_importe_ee49a7_idx",
            ),
        ),
    ]
