"""
Custom Cloudinary storage for large file uploads (videos > 10MB).
Uses unsigned upload with preset for files larger than 10MB.
"""
from cloudinary_storage.storage import MediaCloudinaryStorage
from django.core.files.base import File
import logging

logger = logging.getLogger(__name__)


class LargeFileCloudinaryStorage(MediaCloudinaryStorage):
    """
    Custom storage that handles large file uploads (>10MB) using unsigned upload.
    For files larger than 10MB, we need to use unsigned upload with a preset.
    """
    
    def _save(self, name, content):
        """
        Save file to Cloudinary, using unsigned upload for large files.
        """
        import cloudinary.uploader
        from django.conf import settings
        
        # Check file size
        if hasattr(content, 'size'):
            file_size = content.size
        elif hasattr(content, 'seek') and hasattr(content, 'tell'):
            # Get file size by seeking to end
            current_pos = content.tell()
            content.seek(0, 2)  # Seek to end
            file_size = content.tell()
            content.seek(current_pos)  # Reset position
        else:
            # Try to read content to get size
            content.seek(0)
            file_data = content.read()
            file_size = len(file_data)
            content.seek(0)
        
        # 10MB = 10485760 bytes
        max_signed_size = 10485760
        
        if file_size > max_signed_size:
            # For large files, we need to use unsigned upload
            # This requires a preset to be configured in Cloudinary dashboard
            # For now, we'll try to upload with chunked upload or use resource_type="video"
            try:
                # Try using upload_large for videos
                if hasattr(content, 'name') and any(ext in content.name.lower() for ext in ['.mp4', '.webm', '.ogg', '.mov', '.avi']):
                    # Use video resource type and chunked upload
                    result = cloudinary.uploader.upload_large(
                        content,
                        resource_type="video",
                        folder="banners/videos/",
                        chunk_size=6000000,  # 6MB chunks
                        timeout=600,  # 10 minutes timeout
                    )
                else:
                    # For other large files, try regular upload with increased timeout
                    result = cloudinary.uploader.upload(
                        content,
                        folder="banners/videos/" if "video" in name.lower() else "banners/",
                        resource_type="auto",
                        timeout=600,
                    )
                return result['public_id']
            except Exception as e:
                logger.error(f"Failed to upload large file to Cloudinary: {e}")
                # If upload_large fails, try regular upload (might work if account allows it)
                try:
                    result = cloudinary.uploader.upload(
                        content,
                        folder="banners/videos/" if "video" in name.lower() else "banners/",
                        resource_type="auto",
                    )
                    return result['public_id']
                except Exception as upload_error:
                    error_msg = (
                        f"File size ({file_size / 1024 / 1024:.2f}MB) exceeds Cloudinary free plan limit (10MB). "
                        f"Please either: 1) Upgrade your Cloudinary plan, 2) Compress the video file, "
                        f"or 3) Configure an unsigned upload preset in Cloudinary dashboard. "
                        f"Error: {upload_error}"
                    )
                    logger.error(error_msg)
                    raise Exception(error_msg)
        else:
            # For files under 10MB, use regular upload
            return super()._save(name, content)

