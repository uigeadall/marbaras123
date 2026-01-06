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
    
    def url(self, name):
        """
        Override url() to generate correct Cloudinary URLs for video files.
        Video files need /video/upload/ instead of /image/upload/ in the URL.
        """
        # Check if this is a video file by checking the name/path
        is_video = "video" in name.lower() or any(ext in name.lower() for ext in ['.mp4', '.webm', '.ogg', '.mov', '.avi'])
        
        if is_video:
            # For video files, we need to generate the URL manually to ensure correct format
            import cloudinary
            from django.conf import settings
            
            # Get public_id from name - remove any folder prefixes and extensions
            # The name format from Django is usually: "banners/videos/filename.mp4"
            # But Cloudinary might store it as: "media/banners/videos/filename" or "banners/videos/filename"
            public_id = name
            
            # Remove all folder prefixes (media/, banners/videos/, banners/)
            if public_id.startswith('media/banners/videos/'):
                public_id = public_id.replace('media/banners/videos/', '')
            elif public_id.startswith('media/banners/'):
                public_id = public_id.replace('media/banners/', '')
            elif public_id.startswith('media/'):
                public_id = public_id.replace('media/', '')
            elif public_id.startswith('banners/videos/'):
                public_id = public_id.replace('banners/videos/', '')
            elif public_id.startswith('banners/'):
                public_id = public_id.replace('banners/', '')
            
            # Remove file extension for public_id
            import os
            if public_id.endswith(('.mp4', '.webm', '.ogg', '.mov', '.avi')):
                public_id = os.path.splitext(public_id)[0]
            
            # If public_id still contains slashes, it means it has folder structure
            # We need to keep the folder structure but remove 'media/' prefix
            # Cloudinary public_id format: "folder/subfolder/filename" (without extension)
            logger.info(f"Extracted public_id '{public_id}' from name '{name}'")
            
            # Generate video URL using CloudinaryVideo
            try:
                video_url = cloudinary.CloudinaryVideo(public_id).build_url(secure=True)
                logger.info(f"Generated video URL for {name} (public_id: {public_id}): {video_url}")
                return video_url
            except Exception as e:
                logger.warning(f"Failed to generate video URL using CloudinaryVideo for {name}, trying parent method: {e}")
                # Fallback to parent method and fix URL
                url = super().url(name)
                if '/image/upload/' in url:
                    url = url.replace('/image/upload/', '/video/upload/')
                return url
        else:
            # For images and other files, use parent method
            return super().url(name)
    
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
            # For large files (>10MB), we need to use unsigned upload with a preset
            # This requires an unsigned upload preset to be configured in Cloudinary dashboard
            # Steps to create preset:
            # 1. Go to Cloudinary Dashboard > Settings > Upload
            # 2. Create a new unsigned upload preset
            # 3. Set it to allow videos and increase max file size
            # 4. Add the preset name to CLOUDINARY_UNSIGNED_PRESET environment variable
            
            from django.conf import settings
            unsigned_preset = getattr(settings, 'CLOUDINARY_UNSIGNED_PRESET', None)
            
            if not unsigned_preset:
                error_msg = (
                    f"File size ({file_size / 1024 / 1024:.2f}MB) exceeds Cloudinary free plan signed upload limit (10MB). "
                    f"To upload large files, you need to:\n"
                    f"1. Create an unsigned upload preset in Cloudinary Dashboard (Settings > Upload > Add upload preset)\n"
                    f"2. Set the preset name in Railway environment variable: CLOUDINARY_UNSIGNED_PRESET\n"
                    f"3. Or compress the video file to under 10MB\n"
                    f"4. Or upgrade your Cloudinary plan"
                )
                logger.error(error_msg)
                raise Exception(error_msg)
            
            try:
                # Use unsigned upload with preset for large files
                is_video = hasattr(content, 'name') and any(ext in content.name.lower() for ext in ['.mp4', '.webm', '.ogg', '.mov', '.avi'])
                
                if is_video:
                    # Try upload_large first (works on paid plans)
                    try:
                        result = cloudinary.uploader.upload_large(
                            content,
                            resource_type="video",
                            folder="banners/videos/",
                            upload_preset=unsigned_preset,
                            chunk_size=6000000,  # 6MB chunks
                            timeout=600,  # 10 minutes timeout
                        )
                        return result['public_id']
                    except AttributeError:
                        # upload_large not available, use unsigned upload
                        result = cloudinary.uploader.upload(
                            content,
                            resource_type="video",
                            folder="banners/videos/",
                            upload_preset=unsigned_preset,
                            timeout=600,
                        )
                        return result['public_id']
                else:
                    # For other large files
                    result = cloudinary.uploader.upload(
                        content,
                        folder="banners/videos/" if "video" in name.lower() else "banners/",
                        resource_type="auto",
                        upload_preset=unsigned_preset,
                        timeout=600,
                    )
                    return result['public_id']
            except Exception as e:
                error_msg = (
                    f"Failed to upload large file ({file_size / 1024 / 1024:.2f}MB) to Cloudinary. "
                    f"Error: {e}. "
                    f"Please check that CLOUDINARY_UNSIGNED_PRESET is correctly configured in Railway environment variables."
                )
                logger.error(error_msg)
                raise Exception(error_msg)
        else:
            # For files under 10MB, use regular upload
            # But check if it's a video file and use correct resource_type
            is_video = hasattr(content, 'name') and any(ext in content.name.lower() for ext in ['.mp4', '.webm', '.ogg', '.mov', '.avi'])
            if is_video or "video" in name.lower():
                # For video files, we need to explicitly set resource_type="video"
                # Otherwise Cloudinary might try to validate it as an image
                try:
                    import cloudinary.uploader
                    result = cloudinary.uploader.upload(
                        content,
                        resource_type="video",
                        folder="banners/videos/" if "video" in name.lower() else "banners/",
                    )
                    return result['public_id']
                except Exception as e:
                    logger.error(f"Failed to upload video file to Cloudinary: {e}")
                    # Fallback to parent method
                    return super()._save(name, content)
            else:
                # For images and other files, use parent method
                return super()._save(name, content)

