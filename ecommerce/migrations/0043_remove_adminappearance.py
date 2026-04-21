from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("ecommerce", "0042_adminappearance"),
    ]

    operations = [
        migrations.DeleteModel(name="AdminAppearance"),
    ]
