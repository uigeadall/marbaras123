"""
Management command to clean up old media files from local storage.
This helps free up disk space on Railway by removing files that are already in Cloudinary.
Usage: python manage.py cleanup_old_media [--dry-run] [--older-than-days 30]
"""
from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
import os
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Clean up old media files from local storage to free up disk space'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting',
        )
        parser.add_argument(
            '--older-than-days',
            type=int,
            default=30,
            help='Only delete files older than this many days (default: 30)',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force cleanup even if Cloudinary is not configured',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        older_than_days = options['older_than_days']
        force = options['force']
        
        # Check if Cloudinary is configured
        cloudinary_configured = (
            hasattr(settings, 'CLOUDINARY_CLOUD_NAME') and 
            settings.CLOUDINARY_CLOUD_NAME
        )
        
        if not cloudinary_configured and not force:
            self.stdout.write(
                self.style.WARNING(
                    '⚠️  Cloudinary is not configured. '
                    'This command is designed to clean up local files that are already in Cloudinary. '
                    'Use --force to proceed anyway.'
                )
            )
            return
        
        # Get media root
        media_root = getattr(settings, 'MEDIA_ROOT', None)
        if not media_root or not os.path.exists(media_root):
            self.stdout.write(
                self.style.WARNING(f'⚠️  Media root not found: {media_root}')
            )
            return
        
        self.stdout.write(f'📁 Scanning media directory: {media_root}')
        self.stdout.write(f'⏰ Looking for files older than {older_than_days} days')
        
        if dry_run:
            self.stdout.write(self.style.WARNING('🔍 DRY RUN MODE - No files will be deleted'))
        
        cutoff_date = timezone.now() - timedelta(days=older_than_days)
        total_size = 0
        deleted_count = 0
        error_count = 0
        
        # Walk through media directory
        for root, dirs, files in os.walk(media_root):
            for filename in files:
                file_path = os.path.join(root, filename)
                
                try:
                    # Get file modification time
                    mtime = os.path.getmtime(file_path)
                    file_date = timezone.datetime.fromtimestamp(mtime, tz=timezone.utc)
                    
                    # Skip if file is newer than cutoff
                    if file_date > cutoff_date:
                        continue
                    
                    # Get file size
                    file_size = os.path.getsize(file_path)
                    total_size += file_size
                    
                    # Delete file
                    if not dry_run:
                        try:
                            os.remove(file_path)
                            deleted_count += 1
                            self.stdout.write(f'✅ Deleted: {file_path} ({self._format_size(file_size)})')
                        except Exception as e:
                            error_count += 1
                            self.stdout.write(
                                self.style.ERROR(f'❌ Error deleting {file_path}: {e}')
                            )
                    else:
                        deleted_count += 1
                        self.stdout.write(f'🔍 Would delete: {file_path} ({self._format_size(file_size)})')
                
                except Exception as e:
                    error_count += 1
                    self.stdout.write(
                        self.style.ERROR(f'❌ Error processing {file_path}: {e}')
                    )
        
        # Summary
        self.stdout.write('')
        self.stdout.write('=' * 60)
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN SUMMARY:'))
        else:
            self.stdout.write(self.style.SUCCESS('CLEANUP SUMMARY:'))
        self.stdout.write(f'  Files processed: {deleted_count}')
        self.stdout.write(f'  Total space freed: {self._format_size(total_size)}')
        if error_count > 0:
            self.stdout.write(self.style.ERROR(f'  Errors: {error_count}'))
        self.stdout.write('=' * 60)
        
        if not dry_run and deleted_count > 0:
            self.stdout.write(
                self.style.SUCCESS(f'\n✅ Successfully freed {self._format_size(total_size)} of disk space!')
            )
    
    def _format_size(self, size_bytes):
        """Format file size in human-readable format."""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.2f} PB"
