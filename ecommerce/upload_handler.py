"""
Custom file upload handler that uploads directly to Cloudinary without creating temporary files.
"""
import io
from django.core.files.uploadhandler import MemoryFileUploadHandler
from django.conf import settings


class CloudinaryMemoryUploadHandler(MemoryFileUploadHandler):
    """
    Upload handler that keeps files in memory and uploads directly to Cloudinary.
    This prevents temporary file creation on disk.
    """
    
    def file_complete(self, file_size):
        """Called when file upload is complete."""
        # Get the file content from memory
        self.file.seek(0)
        content = self.file.read()
        
        # Create a new file-like object for Cloudinary
        file_obj = io.BytesIO(content)
        file_obj.name = self.file_name
        
        # Upload directly to Cloudinary
        try:
            import cloudinary.uploader
            result = cloudinary.uploader.upload(
                file_obj,
                folder="media",
                resource_type="auto"
            )
            # Store Cloudinary URL in the file object
            self.file.url = result['secure_url']
            self.file.public_id = result['public_id']
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to upload to Cloudinary: {e}")
            raise
        
        return super().file_complete(file_size)

