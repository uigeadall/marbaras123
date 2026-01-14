"""
Management command to update coupon dates to make them valid.
Usage: python manage.py update_coupon_dates --days 365
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from ecommerce.models import Coupon


class Command(BaseCommand):
    help = 'Update coupon dates to make them valid'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=365,
            help='Number of days from now the coupon should be valid (default: 365)'
        )
        parser.add_argument(
            '--reset_used',
            action='store_true',
            help='Reset used_count to 0 for all coupons'
        )

    def handle(self, *args, **options):
        days = options['days']
        reset_used = options.get('reset_used', False)

        now = timezone.now()
        starts_at = now
        ends_at = now + timezone.timedelta(days=days)

        self.stdout.write(f'Updating coupon dates...')
        self.stdout.write(f'  - Start date: {starts_at}')
        self.stdout.write(f'  - End date: {ends_at}')
        self.stdout.write(f'  - Reset used_count: {reset_used}')

        # Update all active coupons
        coupons = Coupon.objects.filter(active=True).exclude(code__iexact="WELCOME5")
        
        updated_count = coupons.update(
            starts_at=starts_at,
            ends_at=ends_at
        )

        if reset_used:
            coupons.update(used_count=0)
            self.stdout.write(self.style.SUCCESS(f'\n✅ Updated {updated_count} coupons with new dates and reset used_count to 0'))
        else:
            self.stdout.write(self.style.SUCCESS(f'\n✅ Updated {updated_count} coupons with new dates'))

        # Show some examples
        sample_coupons = coupons[:5]
        self.stdout.write('\nSample updated coupons:')
        for coupon in sample_coupons:
            self.stdout.write(f'  - {coupon.code}: valid until {coupon.ends_at.date()}')
