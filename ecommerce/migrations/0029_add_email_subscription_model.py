# Generated manually

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('ecommerce', '0028_add_legal_page_model'),
    ]

    operations = [
        migrations.CreateModel(
            name='EmailSubscription',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.EmailField(db_index=True, max_length=254, unique=True)),
                ('subscribed_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('coupon', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='email_subscriptions', to='ecommerce.coupon')),
            ],
            options={
                'verbose_name': 'Email Subscription',
                'verbose_name_plural': 'Email Subscriptions',
                'ordering': ['-subscribed_at'],
            },
        ),
    ]
