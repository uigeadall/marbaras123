# Customer testimonials for the home page

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ecommerce", "0032_increase_media_field_max_length"),
    ]

    operations = [
        migrations.CreateModel(
            name="CustomerReview",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "customer_name",
                    models.CharField(
                        help_text="Display name (e.g. first name or initials).",
                        max_length=120,
                    ),
                ),
                (
                    "review",
                    models.TextField(
                        help_text="Review text shown on the home page.",
                    ),
                ),
                (
                    "image",
                    models.ImageField(
                        blank=True,
                        help_text="Optional photo (customer or product).",
                        max_length=512,
                        null=True,
                        upload_to="reviews/",
                    ),
                ),
                (
                    "is_published",
                    models.BooleanField(db_index=True, default=True),
                ),
                (
                    "order",
                    models.IntegerField(
                        db_index=True,
                        default=0,
                        help_text="Lower numbers appear first.",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, db_index=True),
                ),
            ],
            options={
                "verbose_name": "Customer Review",
                "verbose_name_plural": "Customer Reviews",
                "ordering": ["order", "-created_at"],
            },
        ),
    ]
