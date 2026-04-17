
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.conf import settings
from ecommerce.models import Order, OrderItem, Product, ShippingOption
from ecommerce.utils.emailing import (
    _order_email_money_bundle,
    send_welcome_email,
    send_order_confirmation_email,
    send_order_shipped_email,
    send_password_reset_email,
    send_admin_order_notification,
)
from decimal import Decimal


class Command(BaseCommand):
    help = "Test email sending functionality"

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            type=str,
            help="Email address to send test emails to",
            required=True,
        )
        parser.add_argument(
            "--type",
            type=str,
            choices=["welcome", "order", "admin_order", "shipped", "reset", "all"],
            default="all",
            help="Type of email to test",
        )

    def handle(self, *args, **options):
        email = options["email"]
        email_type = options["type"]
        base_url = (
            getattr(settings, "SITE_URL", None)
            or getattr(settings, "BASE_URL", None)
            or "https://www.marbaras.com"
        )

        self.stdout.write(self.style.SUCCESS(f"\nSending test emails to: {email}"))
        self.stdout.write(self.style.SUCCESS(f"Base URL: {base_url}\n"))

        if email_type in ["welcome", "all"]:
            self.stdout.write(self.style.WARNING("Testing welcome email..."))
            try:
                user, created = User.objects.get_or_create(
                    username="test_user",
                    defaults={"email": email, "first_name": "Test", "last_name": "User"},
                )
                if not created:
                    user.email = email
                    user.save()

                if send_welcome_email(user, base_url):
                    self.stdout.write(self.style.SUCCESS("Welcome email sent.\n"))
                else:
                    self.stdout.write(self.style.ERROR("Welcome email failed to send.\n"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error: {e}\n"))

        if email_type in ["order", "all"]:
            self.stdout.write(self.style.WARNING("Testing order confirmation email..."))
            try:
                user, _ = User.objects.get_or_create(
                    username="test_user",
                    defaults={"email": email},
                )
                if user.email != email:
                    user.email = email
                    user.save()

                shipping, _ = ShippingOption.objects.get_or_create(
                    name="Standard",
                    defaults={"price": Decimal("5.00"), "delivery_time": "3-5 days"},
                )

                product = Product.objects.first()
                if not product:
                    self.stdout.write(
                        self.style.ERROR("No products in database. Create at least one product.\n")
                    )
                    return

                order = Order.objects.create(
                    user=user,
                    email=email,
                    full_name="Test Customer",
                    address="123 Test Street",
                    city="London",
                    postal_code="SW1A 1AA",
                    phone="+441234567890",
                    shipping_option=shipping,
                    total_price=Decimal("99.99"),
                )

                variant = product.variants.first()
                OrderItem.objects.create(
                    order=order,
                    product=product,
                    variant=variant,
                    quantity=2,
                )

                if send_order_confirmation_email(order, base_url, notify_admin=False):
                    self.stdout.write(self.style.SUCCESS("Order confirmation email sent.\n"))
                else:
                    self.stdout.write(self.style.ERROR("Order confirmation email failed to send.\n"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error: {e}\n"))

        if email_type in ["admin_order", "all"]:
            self.stdout.write(self.style.WARNING("Testing admin order notification email..."))
            try:
                order = Order.objects.filter(email=email).first()
                if not order:
                    user, _ = User.objects.get_or_create(
                        username="test_user",
                        defaults={"email": email},
                    )
                    shipping, _ = ShippingOption.objects.get_or_create(
                        name="Standard",
                        defaults={"price": Decimal("5.00"), "delivery_time": "3-5 days"},
                    )
                    product = Product.objects.first()
                    if not product:
                        self.stdout.write(
                            self.style.ERROR("No products in database. Create at least one product.\n")
                        )
                        return

                    order = Order.objects.create(
                        user=user,
                        email=email,
                        full_name="Test Customer",
                        address="123 Test Street",
                        city="London",
                        postal_code="SW1A 1AA",
                        phone="+441234567890",
                        shipping_option=shipping,
                        total_price=Decimal("99.99"),
                    )

                    variant = product.variants.first()
                    OrderItem.objects.create(
                        order=order,
                        product=product,
                        variant=variant,
                        quantity=2,
                    )

                items = list(order.items.select_related("product", "variant").all())
                money = _order_email_money_bundle(order, items)

                if send_admin_order_notification(order, base_url, items, money):
                    self.stdout.write(self.style.SUCCESS("Admin order notification sent.\n"))
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"   To: {getattr(settings, 'ADMIN_EMAIL', 'ADMINS setting')}\n"
                        )
                    )
                else:
                    self.stdout.write(
                        self.style.ERROR("Admin order notification failed to send.\n")
                    )
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error: {e}\n"))
                import traceback

                self.stdout.write(self.style.ERROR(traceback.format_exc()))

        if email_type in ["shipped", "all"]:
            self.stdout.write(self.style.WARNING("Testing order shipped email..."))
            try:
                order = Order.objects.filter(email=email).first()
                if not order:
                    user, _ = User.objects.get_or_create(
                        username="test_user",
                        defaults={"email": email},
                    )
                    shipping, _ = ShippingOption.objects.get_or_create(
                        name="Standard",
                        defaults={"price": Decimal("5.00"), "delivery_time": "3-5 days"},
                    )
                    product = Product.objects.first()
                    if not product:
                        self.stdout.write(self.style.ERROR("No products in database.\n"))
                        return

                    order = Order.objects.create(
                        user=user,
                        email=email,
                        full_name="Test Customer",
                        address="123 Test Street",
                        city="London",
                        postal_code="SW1A 1AA",
                        phone="+441234567890",
                        shipping_option=shipping,
                        total_price=Decimal("99.99"),
                    )
                    variant = product.variants.first()
                    OrderItem.objects.create(
                        order=order,
                        product=product,
                        variant=variant,
                        quantity=1,
                    )

                if send_order_shipped_email(order, base_url, tracking_number="TEST123456"):
                    self.stdout.write(self.style.SUCCESS("Order shipped email sent.\n"))
                else:
                    self.stdout.write(self.style.ERROR("Order shipped email failed to send.\n"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error: {e}\n"))

        if email_type in ["reset", "all"]:
            self.stdout.write(self.style.WARNING("Testing password reset email..."))
            try:
                user, _ = User.objects.get_or_create(
                    username="test_user",
                    defaults={"email": email},
                )
                if user.email != email:
                    user.email = email
                    user.save()

                reset_url = f"{base_url}/accounts/password/reset/?token=test_token_12345"
                if send_password_reset_email(user, reset_url, base_url):
                    self.stdout.write(self.style.SUCCESS("Password reset email sent.\n"))
                else:
                    self.stdout.write(self.style.ERROR("Password reset email failed to send.\n"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error: {e}\n"))

        self.stdout.write(self.style.SUCCESS("\nDone.\n"))
        self.stdout.write(self.style.WARNING("Check inbox and spam folder.\n"))
