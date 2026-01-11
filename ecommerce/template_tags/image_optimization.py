"""
Template tags for AI-powered image optimization.
"""
from django import template
from django.conf import settings
from ecommerce.utils.image_ai import get_optimized_image_url

register = template.Library()


@register.filter
def optimized_image(image_field, quality='auto'):
    """
    Get optimized image URL using AI.
    
    Usage in templates:
    {{ product.main_image|optimized_image }}
    {{ product.main_image|optimized_image:"best" }}
    
    Args:
        image_field: Django ImageField instance
        quality: Quality setting ("auto", "best", "good", "eco", "low")
    
    Returns:
        Optimized image URL
    """
    if not image_field:
        return ""
    
    return get_optimized_image_url(image_field, quality=quality, format="auto")


@register.simple_tag
def cloudinary_optimized_url(image_url, width=None, height=None, quality='auto', format='auto'):
    """
    Generate Cloudinary optimized URL with AI transformations.
    
    Usage in templates:
    {% cloudinary_optimized_url product.main_image.url width=800 quality="auto" %}
    
    Args:
        image_url: Image URL (can be Cloudinary or local)
        width: Target width (optional)
        height: Target height (optional)
        quality: Quality setting
        format: Format setting
    
    Returns:
        Optimized image URL
    """
    if not image_url:
        return ""
    
    # If using Cloudinary, add transformations
    if hasattr(settings, 'CLOUDINARY_CLOUD_NAME') and settings.CLOUDINARY_CLOUD_NAME:
        try:
            from cloudinary import CloudinaryImage
            
            # Extract public_id from URL
            # Cloudinary URLs format: https://res.cloudinary.com/cloud_name/image/upload/v1234567/path/to/image.jpg
            if 'cloudinary.com' in image_url:
                # Extract public_id from Cloudinary URL
                parts = image_url.split('/upload/')
                if len(parts) > 1:
                    public_id_with_version = parts[1].split('/')[-1]
                    # Remove version prefix if present
                    if public_id_with_version.startswith('v'):
                        public_id = '/'.join(parts[1].split('/')[1:])
                    else:
                        public_id = parts[1]
                    # Remove file extension for public_id
                    if '.' in public_id:
                        public_id = public_id.rsplit('.', 1)[0]
                else:
                    return image_url  # Can't parse, return original
            else:
                # Local file, try to use as public_id
                public_id = image_url.replace('/media/', '').replace(settings.MEDIA_URL, '')
                if '.' in public_id:
                    public_id = public_id.rsplit('.', 1)[0]
            
            img = CloudinaryImage(public_id)
            
            transformation = {
                "quality": quality,
                "fetch_format": format,
                "flags": "auto",  # Enable AI optimizations
            }
            
            if width:
                transformation["width"] = width
            if height:
                transformation["height"] = height
            
            return img.build_url(transformation=[transformation])
            
        except Exception:
            # Fallback to original URL if Cloudinary fails
            return image_url
    
    # Not using Cloudinary, return original URL
    return image_url
