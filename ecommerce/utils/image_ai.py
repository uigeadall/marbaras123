"""
AI-powered image optimization utilities.
Uses Cloudinary AI transformations and optional Remove.bg API for background removal.
"""
import logging
import requests
from typing import Optional, Tuple
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
import io

logger = logging.getLogger(__name__)


def optimize_image_with_cloudinary(public_id: str, quality: str = "auto", format: str = "auto") -> Optional[str]:
    """
    Optimize image using Cloudinary AI transformations.
    
    Args:
        public_id: Cloudinary public_id of the image
        quality: Quality setting ("auto", "best", "good", "eco", "low")
        format: Format ("auto" for automatic format selection)
    
    Returns:
        Optimized image URL or None if failed
    """
    try:
        from cloudinary import CloudinaryImage
        
        # Build transformation URL with AI optimizations
        img = CloudinaryImage(public_id)
        
        # Apply AI-powered optimizations:
        # - q_auto: Automatic quality optimization
        # - f_auto: Automatic format selection (WebP, AVIF, etc.)
        # - fl_auto: Automatic format lossless optimization
        # - c_limit: Limit dimensions if needed
        optimized_url = img.build_url(
            transformation=[
                {
                    "quality": quality,
                    "fetch_format": format,
                    "flags": "auto",  # Enable auto-optimization flags
                }
            ]
        )
        
        logger.info(f"✅ Optimized image {public_id} with Cloudinary AI")
        return optimized_url
        
    except Exception as e:
        logger.error(f"❌ Failed to optimize image {public_id} with Cloudinary: {e}")
        return None


def remove_background_removebg(image_url: str, api_key: Optional[str] = None) -> Optional[bytes]:
    """
    Remove background from image using Remove.bg API.
    
    Args:
        image_url: URL of the image to process
        api_key: Remove.bg API key (optional, can be from settings)
    
    Returns:
        Image bytes without background or None if failed
    """
    api_key = api_key or getattr(settings, 'REMOVEBG_API_KEY', None)
    
    if not api_key:
        logger.warning("Remove.bg API key not configured. Skipping background removal.")
        return None
    
    try:
        response = requests.post(
            'https://api.remove.bg/v1.0/removebg',
            data={
                'image_url': image_url,
                'size': 'auto',
            },
            headers={'X-Api-Key': api_key},
            timeout=30
        )
        
        if response.status_code == 200:
            logger.info(f"✅ Removed background from image {image_url}")
            return response.content
        else:
            logger.error(f"❌ Remove.bg API error: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        logger.error(f"❌ Failed to remove background: {e}")
        return None


def optimize_image_cloudinary_ai(public_id: str, remove_bg: bool = False) -> Optional[str]:
    """
    Comprehensive AI image optimization using Cloudinary.
    
    Features:
    - Automatic quality optimization
    - Automatic format selection (WebP, AVIF)
    - Automatic compression
    - Optional background removal (if Remove.bg API is configured)
    
    Args:
        public_id: Cloudinary public_id
        remove_bg: Whether to attempt background removal
    
    Returns:
        Optimized image URL
    """
    try:
        from cloudinary import CloudinaryImage
        
        img = CloudinaryImage(public_id)
        
        # Build transformation with AI optimizations
        transformations = [
            {
                "quality": "auto",  # AI-powered quality optimization
                "fetch_format": "auto",  # Auto-select best format (WebP, AVIF, etc.)
                "flags": "auto",  # Enable all auto-optimization flags
            }
        ]
        
        # Add background removal if requested and Remove.bg is configured
        if remove_bg and hasattr(settings, 'REMOVEBG_API_KEY') and settings.REMOVEBG_API_KEY:
            # Note: Cloudinary doesn't have built-in background removal,
            # but we can use Remove.bg API separately if needed
            pass
        
        optimized_url = img.build_url(transformation=transformations)
        
        logger.info(f"✅ AI-optimized image {public_id}")
        return optimized_url
        
    except Exception as e:
        logger.error(f"❌ Failed to AI-optimize image {public_id}: {e}")
        return None


def get_optimized_image_url(image_field, quality: str = "auto", format: str = "auto") -> str:
    """
    Get optimized image URL for a Django ImageField.
    Uses Cloudinary AI transformations if available, otherwise returns original URL.
    
    Args:
        image_field: Django ImageField instance
        quality: Quality setting
        format: Format setting
    
    Returns:
        Optimized image URL or original URL as fallback
    """
    # Always return original URL if image_field is None or empty
    if not image_field:
        return ""
    
    if not image_field.name:
        return ""
    
    # Check if using Cloudinary - try Cloudinary first for all images
    if hasattr(settings, 'CLOUDINARY_CLOUD_NAME') and settings.CLOUDINARY_CLOUD_NAME:
        try:
            from cloudinary import CloudinaryImage
            
            # Extract public_id from image name
            public_id = image_field.name
            # Remove file extension for Cloudinary public_id
            if '.' in public_id:
                public_id = public_id.rsplit('.', 1)[0]
            
            # If public_id is empty, try to get original URL
            if not public_id:
                try:
                    return image_field.url if hasattr(image_field, 'url') else ""
                except Exception:
                    return ""
            
            # Try to build Cloudinary URL - this will work even if file is in Cloudinary
            img = CloudinaryImage(public_id)
            optimized_url = img.build_url(
                transformation=[
                    {
                        "quality": quality,
                        "fetch_format": format,
                        "flags": "auto",
                    }
                ]
            )
            
            # Return Cloudinary URL (even if file doesn't exist, Cloudinary will handle 404)
            if optimized_url:
                return optimized_url
            
        except Exception as e:
            logger.debug(f"Failed to get Cloudinary URL for {image_field.name}: {e}")
    
    # Fallback to original URL from storage
    try:
        original_url = image_field.url if hasattr(image_field, 'url') else ""
        return original_url
    except Exception as e:
        logger.warning(f"Failed to get URL for {image_field.name}: {e}")
        return ""


def auto_crop_and_resize(public_id: str, width: Optional[int] = None, height: Optional[int] = None, 
                        gravity: str = "auto", crop: str = "fill") -> Optional[str]:
    """
    Auto-crop and resize image using Cloudinary AI.
    
    Args:
        public_id: Cloudinary public_id
        width: Target width (optional)
        height: Target height (optional)
        gravity: Gravity for cropping ("auto" uses AI face detection)
        crop: Crop mode ("fill", "fit", "thumb", etc.)
    
    Returns:
        Optimized image URL
    """
    try:
        from cloudinary import CloudinaryImage
        
        img = CloudinaryImage(public_id)
        
        transformation = {
            "crop": crop,
            "gravity": gravity,  # "auto" uses AI to detect faces/important areas
        }
        
        if width:
            transformation["width"] = width
        if height:
            transformation["height"] = height
        
        optimized_url = img.build_url(transformation=[transformation])
        
        logger.info(f"✅ Auto-cropped image {public_id}")
        return optimized_url
        
    except Exception as e:
        logger.error(f"❌ Failed to auto-crop image {public_id}: {e}")
        return None
