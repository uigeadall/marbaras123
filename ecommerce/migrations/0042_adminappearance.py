from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ecommerce", "0041_order_awb"),
    ]

    operations = [
        migrations.CreateModel(
            name="AdminAppearance",
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
                    "background_image",
                    models.ImageField(
                        blank=True,
                        help_text="Background photo for the admin panel. Leave empty for gradient only (or use ADMIN_BACKGROUND_IMAGE in env).",
                        max_length=512,
                        null=True,
                        upload_to="admin_theme/",
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Admin appearance",
                "verbose_name_plural": "Admin appearance",
            },
        ),
    ]
