
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.conf import settings
from ecommerce.models import Order, OrderItem, Product, ShippingOption
from ecommerce.utils.emailing import (
    send_welcome_email,
    send_order_confirmation_email,
    send_order_shipped_email,
    send_password_reset_email,
    send_admin_order_notification,
)
from decimal import Decimal


class Command(BaseCommand):
    help = 'Test email sending functionality'

    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            type=str,
            help='Email address to send test emails to',
            required=True,
        )
        parser.add_argument(
            '--type',
            type=str,
            choices=['welcome', 'order', 'admin_order', 'shipped', 'reset', 'all'],
            default='all',
            help='Type of email to test',
        )

    def handle(self, *args, **options):
        email = options['email']
        email_type = options['type']
        # Try to get base URL from settings, or use production domain
        base_url = getattr(settings, 'SITE_URL', None) or \
                   getattr(settings, 'BASE_URL', None) or \
                   'https://www.marbaras.com'

        self.stdout.write(self.style.SUCCESS(f'\n📧 Тестване на имейли до: {email}'))
        self.stdout.write(self.style.SUCCESS(f'🌐 Base URL: {base_url}\n'))


        if email_type in ['welcome', 'all']:
            self.stdout.write(self.style.WARNING('Тестване на Welcome Email...'))
            try:

                user, created = User.objects.get_or_create(
                    username='test_user',
                    defaults={'email': email, 'first_name': 'Тест', 'last_name': 'Потребител'}
                )
                if not created:
                    user.email = email
                    user.save()

                if send_welcome_email(user, base_url):
                    self.stdout.write(self.style.SUCCESS('✅ Welcome email изпратен успешно!\n'))
                else:
                    self.stdout.write(self.style.ERROR('❌ Грешка при изпращане на Welcome email\n'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'❌ Грешка: {e}\n'))


        if email_type in ['order', 'all']:
            self.stdout.write(self.style.WARNING('Тестване на Order Confirmation Email...'))
            try:

                user, _ = User.objects.get_or_create(
                    username='test_user',
                    defaults={'email': email}
                )
                if user.email != email:
                    user.email = email
                    user.save()


                shipping, _ = ShippingOption.objects.get_or_create(
                    name='Standard',
                    defaults={'price': Decimal('5.00'), 'delivery_time': '3-5 дни'}
                )


                product = Product.objects.first()
                if not product:
                    self.stdout.write(self.style.ERROR('❌ Няма продукти в базата данни. Създайте поне един продукт.\n'))
                    return


                order = Order.objects.create(
                    user=user,
                    email=email,
                    full_name='Тест Потребител',
                    address='Тестова Адрес 123',
                    city='София',
                    postal_code='1000',
                    phone='+359888123456',
                    shipping_option=shipping,
                    total_price=Decimal('99.99'),
                )


                variant = product.variants.first()
                OrderItem.objects.create(
                    order=order,
                    product=product,
                    variant=variant,
                    quantity=2,
                )

                if send_order_confirmation_email(order, base_url, notify_admin=False):
                    self.stdout.write(self.style.SUCCESS('✅ Order confirmation email изпратен успешно!\n'))
                else:
                    self.stdout.write(self.style.ERROR('❌ Грешка при изпращане на Order confirmation email\n'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'❌ Грешка: {e}\n'))


        if email_type in ['admin_order', 'all']:
            self.stdout.write(self.style.WARNING('Тестване на Admin Order Notification Email...'))
            try:
                # Get or create test order
                order = Order.objects.filter(email=email).first()
                if not order:
                    user, _ = User.objects.get_or_create(
                        username='test_user',
                        defaults={'email': email}
                    )
                    shipping, _ = ShippingOption.objects.get_or_create(
                        name='Standard',
                        defaults={'price': Decimal('5.00'), 'delivery_time': '3-5 дни'}
                    )
                    product = Product.objects.first()
                    if not product:
                        self.stdout.write(self.style.ERROR('❌ Няма продукти в базата данни. Създайте поне един продукт.\n'))
                        return
                    
                    order = Order.objects.create(
                        user=user,
                        email=email,
                        full_name='Тест Потребител',
                        address='Тестова Адрес 123',
                        city='София',
                        postal_code='1000',
                        phone='+359888123456',
                        shipping_option=shipping,
                        total_price=Decimal('99.99'),
                    )
                    
                    variant = product.variants.first()
                    OrderItem.objects.create(
                        order=order,
                        product=product,
                        variant=variant,
                        quantity=2,
                    )
                
                # Get order items with calculations
                items = order.items.select_related("product", "variant").all()
                subtotal = sum(
                    (item.product.get_discounted_price() * Decimal(item.quantity))
                    for item in items
                )
                shipping_cost = order.shipping_option.price if order.shipping_option else Decimal("0.00")
                discount_amount = Decimal("0.00")
                if order.coupon:
                    discount_amount = subtotal - order.coupon.apply(subtotal)
                total = order.total_price
                
                if send_admin_order_notification(order, base_url, items, subtotal, shipping_cost, discount_amount, total):
                    self.stdout.write(self.style.SUCCESS('✅ Admin order notification email изпратен успешно!\n'))
                    self.stdout.write(self.style.SUCCESS(f'   📧 Изпратен до: {getattr(settings, "ADMIN_EMAIL", "ADMINS setting")}\n'))
                else:
                    self.stdout.write(self.style.ERROR('❌ Грешка при изпращане на Admin order notification email\n'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'❌ Грешка: {e}\n'))
                import traceback
                self.stdout.write(self.style.ERROR(traceback.format_exc()))


        if email_type in ['shipped', 'all']:
            self.stdout.write(self.style.WARNING('Тестване на Order Shipped Email...'))
            try:

                order = Order.objects.filter(email=email).first()
                if not order:
                    user, _ = User.objects.get_or_create(
                        username='test_user',
                        defaults={'email': email}
                    )
                    shipping, _ = ShippingOption.objects.get_or_create(
                        name='Standard',
                        defaults={'price': Decimal('5.00'), 'delivery_time': '3-5 дни'}
                    )
                    product = Product.objects.first()
                    if not product:
                        self.stdout.write(self.style.ERROR('❌ Няма продукти в базата данни.\n'))
                        return

                    order = Order.objects.create(
                        user=user,
                        email=email,
                        full_name='Тест Потребител',
                        address='Тестова Адрес 123',
                        city='София',
                        postal_code='1000',
                        phone='+359888123456',
                        shipping_option=shipping,
                        total_price=Decimal('99.99'),
                    )
                    variant = product.variants.first()
                    OrderItem.objects.create(
                        order=order,
                        product=product,
                        variant=variant,
                        quantity=1,
                    )

                if send_order_shipped_email(order, base_url, tracking_number='TEST123456'):
                    self.stdout.write(self.style.SUCCESS('✅ Order shipped email изпратен успешно!\n'))
                else:
                    self.stdout.write(self.style.ERROR('❌ Грешка при изпращане на Order shipped email\n'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'❌ Грешка: {e}\n'))


        if email_type in ['reset', 'all']:
            self.stdout.write(self.style.WARNING('Тестване на Password Reset Email...'))
            try:
                user, _ = User.objects.get_or_create(
                    username='test_user',
                    defaults={'email': email}
                )
                if user.email != email:
                    user.email = email
                    user.save()

                reset_url = f"{base_url}/accounts/password/reset/?token=test_token_12345"
                if send_password_reset_email(user, reset_url, base_url):
                    self.stdout.write(self.style.SUCCESS('✅ Password reset email изпратен успешно!\n'))
                else:
                    self.stdout.write(self.style.ERROR('❌ Грешка при изпращане на Password reset email\n'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'❌ Грешка: {e}\n'))

        self.stdout.write(self.style.SUCCESS('\n✨ Тестването приключи!\n'))
        self.stdout.write(self.style.WARNING('💡 Съвет: Проверете вашия email inbox (и spam папката).\n'))

