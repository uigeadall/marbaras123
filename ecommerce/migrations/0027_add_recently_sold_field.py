# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ecommerce', '0026_add_database_indexes'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='recently_sold',
            field=models.PositiveIntegerField(default=0, help_text='Number of items recently sold (displayed on product page)'),
        ),
    ]
