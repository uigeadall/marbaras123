#!/usr/bin/env python
"""Fix missing slugs for Accessories, Bracelets, and Earrings"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'МагазинСребро.settings')
django.setup()

from ecommerce.models import Category
from django.utils.text import slugify

def fix_missing_slugs():
    """Fix slugs for categories that have numeric slugs"""
    categories_to_fix = {
        'Accessories': 'accessories',
        'Bracelets': 'bracelets',
        'Earrings': 'earrings',
    }
    
    print("Fixing category slugs...")
    print("-" * 60)
    
    for cat_name, expected_slug in categories_to_fix.items():
        try:
            cat = Category.objects.only('id', 'name', 'slug').get(name=cat_name)
            old_slug = cat.slug or "(empty)"
            
            # Check if slug needs fixing
            if not cat.slug or cat.slug.isdigit() or cat.slug != expected_slug:
                # Check if expected slug is already taken
                existing = Category.objects.only('id', 'slug').filter(slug=expected_slug).exclude(pk=cat.pk).first()
                if existing:
                    print(f"WARNING: Slug '{expected_slug}' already taken by category ID {existing.pk}")
                    # Generate unique slug
                    counter = 1
                    new_slug = f"{expected_slug}-{counter}"
                    while Category.objects.only('id', 'slug').filter(slug=new_slug).exclude(pk=cat.pk).exists():
                        counter += 1
                        new_slug = f"{expected_slug}-{counter}"
                    cat.slug = new_slug
                    print(f"ID {cat.pk:2d} | '{cat.name:20s}' | '{old_slug:20s}' -> '{new_slug}' (unique)")
                else:
                    cat.slug = expected_slug
                    print(f"ID {cat.pk:2d} | '{cat.name:20s}' | '{old_slug:20s}' -> '{expected_slug}'")
                cat.save(update_fields=['slug'])
            else:
                print(f"ID {cat.pk:2d} | '{cat.name:20s}' | '{cat.slug}' (already correct)")
        except Category.DoesNotExist:
            print(f"ERROR: Category '{cat_name}' not found!")
        except Category.MultipleObjectsReturned:
            print(f"ERROR: Multiple categories found with name '{cat_name}'!")
    
    print("\n" + "=" * 60)
    print("Final category slugs:")
    print("-" * 60)
    for cat in Category.objects.only('id', 'name', 'slug').all().order_by('id'):
        print(f"ID: {cat.pk:2d} | Name: {cat.name:20s} | Slug: {cat.slug}")
    
    # Check for duplicates
    print("\n" + "=" * 60)
    print("Checking for duplicate slugs...")
    print("-" * 60)
    slugs = {}
    duplicates_found = False
    for cat in Category.objects.only('id', 'name', 'slug').all():
        if cat.slug:
            if cat.slug in slugs:
                print(f"ERROR: Duplicate slug '{cat.slug}' found!")
                print(f"  - ID {slugs[cat.slug]}: {Category.objects.only('name').get(pk=slugs[cat.slug]).name}")
                print(f"  - ID {cat.pk}: {cat.name}")
                duplicates_found = True
            else:
                slugs[cat.slug] = cat.pk
    
    if not duplicates_found:
        print("✓ No duplicate slugs found!")

if __name__ == '__main__':
    fix_missing_slugs()

