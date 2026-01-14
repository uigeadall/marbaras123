#!/usr/bin/env python
"""
Script to generate 5% discount coupons.
Can be run directly on Railway or locally.
"""
import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'МагазинСребро.settings')
django.setup()

from django.core.management import call_command

if __name__ == '__main__':
    print("Generating 200 coupons with 5% discount...")
    call_command('generate_coupons', 
                 count=200,
                 percent=5.0,
                 prefix='WELCOME-',
                 usage_limit=1,
                 days=365)
    print("✅ Done!")
