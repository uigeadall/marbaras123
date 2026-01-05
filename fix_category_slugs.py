#!/usr/bin/env python
"""Fix category slugs to ensure they are unique and correct"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'МагазинСребро.settings')
django.setup()

from ecommerce.models import Category
from django.utils.text import slugify

def fix_category_slugs():
    """Fix all category slugs to ensure they are unique and correct"""
    categories = Category.objects.only('id', 'name', 'slug').all()
    
    print("Current category slugs:")
    print("-" * 60)
    for cat in categories:
        print(f"ID: {cat.pk:2d} | Name: {cat.name:20s} | Slug: {cat.slug or '(empty)'}")
    
    print("\n" + "=" * 60)
    print("Fixing slugs...")
    print("=" * 60)
    
    # Dictionary to track slugs and ensure uniqueness
    used_slugs = {}
    
    for cat in categories:
        # Generate slug from name
        if cat.name:
            proper_slug = slugify(cat.name.lower())
        else:
            proper_slug = f"category-{cat.pk}"
        
        # If slug is empty, numeric, or doesn't match the name, regenerate it
        if not cat.slug or cat.slug.isdigit() or cat.slug != proper_slug:
            # Ensure uniqueness
            base_slug = proper_slug
            counter = 1
            while proper_slug in used_slugs and used_slugs[proper_slug] != cat.pk:
                proper_slug = f"{base_slug}-{counter}"
                counter += 1
            
            # Update the category
            old_slug = cat.slug or "(empty)"
            cat.slug = proper_slug
            cat.save(update_fields=['slug'])
            print(f"ID {cat.pk:2d} | '{cat.name:20s}' | '{old_slug:20s}' -> '{proper_slug}'")
            used_slugs[proper_slug] = cat.pk
        else:
            # Slug is already correct, but check for duplicates
            if proper_slug in used_slugs and used_slugs[proper_slug] != cat.pk:
                # Duplicate found, need to make unique
                base_slug = proper_slug
                counter = 1
                new_slug = f"{base_slug}-{counter}"
                while new_slug in used_slugs:
                    counter += 1
                    new_slug = f"{base_slug}-{counter}"
                
                old_slug = cat.slug
                cat.slug = new_slug
                cat.save(update_fields=['slug'])
                print(f"ID {cat.pk:2d} | '{cat.name:20s}' | '{old_slug:20s}' -> '{new_slug}' (duplicate fixed)")
                used_slugs[new_slug] = cat.pk
            else:
                used_slugs[proper_slug] = cat.pk
                print(f"ID {cat.pk:2d} | '{cat.name:20s}' | '{cat.slug}' (already correct)")
    
    print("\n" + "=" * 60)
    print("Final category slugs:")
    print("-" * 60)
    for cat in Category.objects.only('id', 'name', 'slug').all():
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
                print(f"  - ID {slugs[cat.slug]}: {Category.objects.get(pk=slugs[cat.slug]).name}")
                print(f"  - ID {cat.pk}: {cat.name}")
                duplicates_found = True
            else:
                slugs[cat.slug] = cat.pk
    
    if not duplicates_found:
        print("✓ No duplicate slugs found!")
    else:
        print("\nWARNING: Duplicate slugs found! Please fix manually.")

if __name__ == '__main__':
    fix_category_slugs()

