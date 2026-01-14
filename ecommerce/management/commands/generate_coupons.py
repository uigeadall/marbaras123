"""
Management command to generate new coupon codes.
Usage: python manage.py generate_coupons --count 100 --percent 15 --usage_limit 1
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from ecommerce.utils.coupons import create_batch


class Command(BaseCommand):
    help = "Generate a batch of promo codes"

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=100, help="Number of coupons to generate")
        parser.add_argument("--prefix", type=str, default="SUMMER-", help="Prefix for coupon codes")
        parser.add_argument("--percent", type=float, default=15.0, help="Discount percentage")
        parser.add_argument("--amount", type=float, default=None, help="Fixed discount amount")
        parser.add_argument("--days", type=int, default=365, help="Number of days coupon is valid")
        parser.add_argument("--usage", type=int, default=1, help="Usage limit per coupon")

    def handle(self, *args, **opts):
        count = opts.get("count", 100)
        prefix = opts.get("prefix", "SUMMER-")
        percent = opts.get("percent", 15.0)
        amount = opts.get("amount")
        days = opts.get("days", 365)
        usage = opts.get("usage", 1)

        self.stdout.write(f'Generating {count} coupons...')
        self.stdout.write(f'  - Discount: {percent}%' if percent else f'  - Amount: ${amount}')
        self.stdout.write(f'  - Prefix: {prefix}')
        self.stdout.write(f'  - Usage limit: {usage}')
        self.stdout.write(f'  - Valid for: {days} days')

        now = timezone.now()
        starts_at = now
        ends_at = now + timezone.timedelta(days=days)

        codes = create_batch(
            count,
            prefix=prefix,
            percent_off=percent if percent else None,
            amount_off=amount,
            starts_at=starts_at,
            ends_at=ends_at,
            usage_limit=usage,
            active=True,
        )
        
        self.stdout.write(self.style.SUCCESS(f'\n✅ Successfully generated {len(codes)} coupons!'))
        self.stdout.write('\nFirst 10 codes:')
        for i, code in enumerate(codes[:10], 1):
            self.stdout.write(f'  {i}. {code}')
        if len(codes) > 10:
            self.stdout.write(f'  ... and {len(codes) - 10} more')
