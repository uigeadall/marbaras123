"""
Custom storage backend that supports both Cloudinary (for new files) and local storage (for old files).
"""
import logging
import os

from django.conf import settings
from django.core.files.storage import Storage

logger = logging.getLogger(__name__)


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

    def delete(self, name):
        if self.location:
            path = os.path.join(self.location, name)
            if os.path.isfile(path):
                os.remove(path)
                return
        try:
            from cloudinary_storage.storage import MediaCloudinaryStorage

            MediaCloudinaryStorage().delete(name)
        except Exception as e:
            logger.warning("HybridMediaStorage.delete(%r): %s", name, e)

    def size(self, name):
        if self.location:
            path = os.path.join(self.location, name)
            if os.path.isfile(path):
                return os.path.getsize(path)
        try:
            from cloudinary_storage.storage import MediaCloudinaryStorage

            return MediaCloudinaryStorage().size(name)
        except Exception:
            return None

    def get_available_name(self, name, max_length=None):
        """
        Align with MediaCloudinaryStorage: avoid calling exists() in a tight loop
        (Cloudinary uses HTTP HEAD per check; failures/timeouts caused admin 500s).
        """
        name = str(name).replace("\\", "/")
        if max_length is None or len(name) <= max_length:
            return name
        return name[:max_length]
    
    def _save(self, name, content):
        """
        Save file to Cloudinary (for new files).
        Old files remain in local storage.
        NEVER writes to local storage to avoid "No space left on device" errors.
        """
        # Django ImageField validates by reading the file (PIL); pointer stays at EOF.
        # Cloudinary upload must start from the beginning or upload fails / 500 in admin.
        if hasattr(content, "seek"):
            try:
                content.seek(0)
            except (OSError, TypeError, ValueError):
                pass
        # Always use Cloudinary for new uploads to avoid Railway storage issues
        try:
            from cloudinary_storage.storage import MediaCloudinaryStorage
            cloudinary_storage = MediaCloudinaryStorage()
            # Save directly to Cloudinary without touching local storage
            return cloudinary_storage._save(name, content)
        except Exception as e:
            logger.exception("Failed to save to Cloudinary (name=%r)", name)
            raise
    
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

