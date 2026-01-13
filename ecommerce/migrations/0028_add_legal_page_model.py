# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ecommerce', '0027_add_recently_sold_field'),
    ]

    operations = [
        migrations.CreateModel(
            name='LegalPage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('page_type', models.CharField(choices=[('privacy', 'Privacy Policy'), ('terms', 'Terms & Conditions')], help_text='Type of legal page', max_length=20, unique=True)),
                ('title', models.CharField(help_text='Page title', max_length=200)),
                ('content', models.TextField(help_text='HTML content of the page')),
                ('last_updated', models.DateTimeField(auto_now=True, help_text='Last update timestamp')),
            ],
            options={
                'verbose_name': 'Legal Page',
                'verbose_name_plural': 'Legal Pages',
                'ordering': ['page_type'],
            },
        ),
    ]
