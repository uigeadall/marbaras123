"""
Custom storage backend that supports both Cloudinary (for new files) and local storage (for old files).
"""
from django.core.files.storage import FileSystemStorage
from django.conf import settings
import os


class HybridMediaStorage(FileSystemStorage):
    """
    Storage backend that checks local storage first, then falls back to Cloudinary.
    This allows old files in Railway volume to still be accessible.
    """
    
    def __init__(self, location=None, base_url=None):
        if location is None:
            location = getattr(settings, 'MEDIA_ROOT', None)
        if base_url is None:
            base_url = getattr(settings, 'MEDIA_URL', None)
        super().__init__(location, base_url)
    
    def url(self, name):
        """
        Return the URL where the file can be accessed.
        First check if file exists locally, otherwise use Cloudinary URL.
        """
        # Check if file exists in local storage
        if self.location and os.path.exists(os.path.join(self.location, name)):
            # File exists locally, serve from local storage
            return super().url(name)
        
        # File doesn't exist locally, try Cloudinary
        # Import here to avoid circular imports
        try:
            from cloudinary_storage.storage import MediaCloudinaryStorage
            cloudinary_storage = MediaCloudinaryStorage()
            return cloudinary_storage.url(name)
        except Exception:
            # Fallback to local URL if Cloudinary fails
            return super().url(name)
    
    def exists(self, name):
        """Check if file exists in either local storage or Cloudinary."""
        # Check local storage first
        if self.location and os.path.exists(os.path.join(self.location, name)):
            return True
        
        # Check Cloudinary
        try:
            from cloudinary_storage.storage import MediaCloudinaryStorage
            cloudinary_storage = MediaCloudinaryStorage()
            return cloudinary_storage.exists(name)
        except Exception:
            return False
    
    def _save(self, name, content):
        """
        Save file to Cloudinary (for new files).
        Old files remain in local storage.
        """
        # Use Cloudinary for new uploads
        try:
            from cloudinary_storage.storage import MediaCloudinaryStorage
            cloudinary_storage = MediaCloudinaryStorage()
            return cloudinary_storage._save(name, content)
        except Exception:
            # Fallback to local storage if Cloudinary fails
            return super()._save(name, content)

