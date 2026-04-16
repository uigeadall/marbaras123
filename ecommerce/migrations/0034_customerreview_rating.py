import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ecommerce", "0033_customerreview"),
    ]

    operations = [
        migrations.AddField(
            model_name="customerreview",
            name="rating",
            field=models.PositiveSmallIntegerField(
                default=5,
                help_text="Star rating from 1 to 5 (shown with the review on the home page).",
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(5),
                ],
            ),
        ),
    ]
