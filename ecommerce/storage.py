"""
Custom storage backend that supports both Cloudinary (for new files) and local storage (for old files).
"""
from django.core.files.storage import Storage
from django.conf import settings
import os


class HybridMediaStorage(Storage):
    """
    Storage backend that checks local storage first, then falls back to Cloudinary.
    This allows old files in Railway volume to still be accessible.
    New files are saved directly to Cloudinary without touching local storage.
    """
    
    def __init__(self):
        self.location = getattr(settings, 'MEDIA_ROOT', None)
        self.base_url = getattr(settings, 'MEDIA_URL', '/media/')
    
    def url(self, name):
        """
        Return the URL where the file can be accessed.
        First check if file exists locally, otherwise use Cloudinary URL.
        """
        # Check if file exists in local storage
        if self.location and os.path.exists(os.path.join(self.location, name)):
            # File exists locally, serve from local storage
            if self.base_url and not self.base_url.endswith('/'):
                return f"{self.base_url}/{name}"
            return f"{self.base_url}{name}"
        
        # File doesn't exist locally, try Cloudinary
        # Import here to avoid circular imports
        try:
            from cloudinary_storage.storage import MediaCloudinaryStorage
            cloudinary_storage = MediaCloudinaryStorage()
            return cloudinary_storage.url(name)
        except Exception:
            # Fallback to local URL if Cloudinary fails
            if self.base_url and not self.base_url.endswith('/'):
                return f"{self.base_url}/{name}"
            return f"{self.base_url}{name}"
    
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
        NEVER writes to local storage to avoid "No space left on device" errors.
        """
        # Always use Cloudinary for new uploads to avoid Railway storage issues
        try:
            from cloudinary_storage.storage import MediaCloudinaryStorage
            cloudinary_storage = MediaCloudinaryStorage()
            # Save directly to Cloudinary without touching local storage
            return cloudinary_storage._save(name, content)
        except Exception as e:
            # If Cloudinary fails, raise the error instead of falling back to local
            # This prevents "No space left on device" errors
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to save to Cloudinary: {e}")
            raise Exception(f"Failed to upload to Cloudinary. Please check your Cloudinary credentials. Error: {e}")
    
    def _open(self, name, mode='rb'):
        """Open file for reading - check local first, then Cloudinary."""
        # Check local storage first
        if self.location and os.path.exists(os.path.join(self.location, name)):
            return open(os.path.join(self.location, name), mode)
        
        # Try Cloudinary
        try:
            from cloudinary_storage.storage import MediaCloudinaryStorage
            cloudinary_storage = MediaCloudinaryStorage()
            return cloudinary_storage._open(name, mode)
        except Exception as e:
            raise FileNotFoundError(f"File {name} not found in local storage or Cloudinary: {e}")

