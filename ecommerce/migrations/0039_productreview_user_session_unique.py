import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("ecommerce", "0038_product_review"),
    ]

    operations = [
        migrations.AddField(
            model_name="productreview",
            name="session_key",
            field=models.CharField(blank=True, db_index=True, max_length=40),
        ),
        migrations.AddField(
            model_name="productreview",
            name="user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="product_reviews",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddConstraint(
            model_name="productreview",
            constraint=models.UniqueConstraint(
                condition=Q(user__isnull=False),
                fields=("product", "user"),
                name="ecommerce_productreview_unique_product_user",
            ),
        ),
        migrations.AddConstraint(
            model_name="productreview",
            constraint=models.UniqueConstraint(
                condition=Q(session_key__gt=""),
                fields=("product", "session_key"),
                name="ecommerce_productreview_unique_product_session",
            ),
        ),
    ]
