"""
Django management command to optimize existing product images using AI.
Usage: python manage.py optimize_images_ai [--all] [--product-id ID]
"""
from django.core.management.base import BaseCommand
from django.db.models import Q
from ecommerce.models import ProductImage, Product
from ecommerce.utils.image_ai import optimize_image_cloudinary_ai, get_optimized_image_url
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Optimize existing product images using AI (Cloudinary AI transformations)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--all',
            action='store_true',
            help='Optimize all product images',
        )
        parser.add_argument(
            '--product-id',
            type=int,
            help='Optimize images for a specific product ID',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=100,
            help='Limit number of images to process (default: 100)',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('🚀 Starting AI image optimization...'))
        
        # Determine which images to optimize
        if options['product_id']:
            product = Product.objects.filter(id=options['product_id']).first()
            if not product:
                self.stdout.write(self.style.ERROR(f'❌ Product with ID {options["product_id"]} not found'))
                return
            images = ProductImage.objects.filter(product=product)
            self.stdout.write(self.style.SUCCESS(f'📦 Optimizing images for product: {product.name}'))
        elif options['all']:
            images = ProductImage.objects.all()[:options['limit']]
            self.stdout.write(self.style.SUCCESS(f'📦 Optimizing all images (limit: {options["limit"]})'))
        else:
            # Default: optimize images without optimization flag (if we add one later)
            images = ProductImage.objects.all()[:options['limit']]
            self.stdout.write(self.style.SUCCESS(f'📦 Optimizing images (limit: {options["limit"]})'))
        
        total = images.count()
        if total == 0:
            self.stdout.write(self.style.WARNING('⚠️  No images found to optimize'))
            return
        
        self.stdout.write(self.style.SUCCESS(f'📊 Found {total} images to optimize'))
        
        optimized_count = 0
        failed_count = 0
        
        for idx, image in enumerate(images, 1):
            try:
                if not image.image or not image.image.name:
                    self.stdout.write(self.style.WARNING(f'⚠️  [{idx}/{total}] Image {image.id} has no file, skipping'))
                    continue
                
                # Get optimized URL (this will use Cloudinary AI transformations)
                optimized_url = get_optimized_image_url(image.image)
                
                if optimized_url:
                    optimized_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'✅ [{idx}/{total}] Optimized image {image.id} '
                            f'({image.product.name})'
                        )
                    )
                else:
                    failed_count += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f'⚠️  [{idx}/{total}] Could not optimize image {image.id}'
                        )
                    )
                    
            except Exception as e:
                failed_count += 1
                self.stdout.write(
                    self.style.ERROR(
                        f'❌ [{idx}/{total}] Error optimizing image {image.id}: {e}'
                    )
                )
        
        # Summary
        self.stdout.write(self.style.SUCCESS('\n' + '='*50))
        self.stdout.write(self.style.SUCCESS('📊 Optimization Summary:'))
        self.stdout.write(self.style.SUCCESS(f'   Total processed: {total}'))
        self.stdout.write(self.style.SUCCESS(f'   ✅ Optimized: {optimized_count}'))
        self.stdout.write(self.style.WARNING(f'   ⚠️  Failed: {failed_count}'))
        self.stdout.write(self.style.SUCCESS('='*50))
        
        if optimized_count > 0:
            self.stdout.write(
                self.style.SUCCESS(
                    '\n💡 Note: Images are now optimized using Cloudinary AI transformations. '
                    'The optimized versions will be served automatically when accessed via Cloudinary URLs.'
                )
            )
