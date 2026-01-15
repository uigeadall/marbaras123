"""
Fast image loading template tags with responsive images and optimization.
"""
from django import template
from django.conf import settings

register = template.Library()


@register.simple_tag
def fast_image_url(image_field, width=None, quality='auto'):
    """
    Generate optimized image URL for fast loading.
    Uses Cloudinary transformations if available.
    
    Usage:
    {% fast_image_url product.main_image width=400 %}
    """
    if not image_field:
        return ''
    
    try:
        url = image_field.url if hasattr(image_field, 'url') else ''
    except Exception:
        return ''
    
    if not url:
        return ''
    
    # If Cloudinary is configured and URL is from Cloudinary, add transformations
    if hasattr(settings, 'CLOUDINARY_CLOUD_NAME') and settings.CLOUDINARY_CLOUD_NAME:
        if 'cloudinary.com' in url or 'res.cloudinary.com' in url:
            try:
                from cloudinary import CloudinaryImage
                
                # Extract public_id from Cloudinary URL
                if '/upload/' in url:
                    parts = url.split('/upload/')
                    if len(parts) > 1:
                        public_id_with_params = parts[1].split('?')[0]  # Remove query params
                        # Remove version prefix if present (v1234567/)
                        if '/' in public_id_with_params:
                            path_parts = public_id_with_params.split('/')
                            if path_parts[0].startswith('v') and path_parts[0][1:].isdigit():
                                public_id = '/'.join(path_parts[1:])
                            else:
                                public_id = public_id_with_params
                        else:
                            public_id = public_id_with_params
                        
                        # Remove file extension for public_id
                        if '.' in public_id:
                            public_id = public_id.rsplit('.', 1)[0]
                        
                        img = CloudinaryImage(public_id)
                        
                        transformation = {
                            'quality': quality,
                            'fetch_format': 'auto',  # Auto WebP/AVIF
                            'flags': 'progressive',  # Progressive JPEG
                        }
                        
                        if width:
                            transformation['width'] = width
                            transformation['crop'] = 'limit'  # Don't crop, just resize
                        
                        return img.build_url(transformation=[transformation])
            except Exception:
                pass
    
    return url


@register.inclusion_tag('templatetags/fast_image.html', takes_context=False)
def fast_image(image_field, alt='', width=None, height=None, class_name='', lazy=True, priority=False):
    """
    Generate optimized responsive image with srcset.
    
    Usage:
    {% fast_image product.main_image alt=product.name width=400 lazy=True %}
    """
    if not image_field:
        return {
            'url': '',
            'srcset': '',
            'sizes': '',
            'alt': alt,
            'width': width or 400,
            'height': height or width or 400,
            'class_name': class_name,
            'lazy': lazy,
            'priority': priority,
        }
    
    try:
        if hasattr(image_field, 'url'):
            base_url = image_field.url
        elif hasattr(image_field, 'name') and image_field.name:
            # Try to get URL from storage if url attribute doesn't exist
            try:
                from django.core.files.storage import default_storage
                base_url = default_storage.url(image_field.name)
            except Exception:
                base_url = ''
        else:
            base_url = ''
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Error getting image URL: {e}")
        base_url = ''
    
    if not base_url:
        return {
            'url': '',
            'srcset': '',
            'sizes': '',
            'alt': alt,
            'width': width or 400,
            'height': height or width or 400,
            'class_name': class_name,
            'lazy': lazy,
            'priority': priority,
        }
    
    # Default dimensions for product images (square)
    img_width = width or 400
    img_height = height or img_width
    
    # Generate srcset if Cloudinary is available
    srcset_parts = []
    if hasattr(settings, 'CLOUDINARY_CLOUD_NAME') and settings.CLOUDINARY_CLOUD_NAME:
        if 'cloudinary.com' in base_url or 'res.cloudinary.com' in base_url:
            try:
                from cloudinary import CloudinaryImage
                
                # Extract public_id
                if '/upload/' in base_url:
                    parts = base_url.split('/upload/')
                    if len(parts) > 1:
                        public_id_with_params = parts[1].split('?')[0]
                        if '/' in public_id_with_params:
                            path_parts = public_id_with_params.split('/')
                            if path_parts[0].startswith('v') and path_parts[0][1:].isdigit():
                                public_id = '/'.join(path_parts[1:])
                            else:
                                public_id = public_id_with_params
                        else:
                            public_id = public_id_with_params
                        
                        if '.' in public_id:
                            public_id = public_id.rsplit('.', 1)[0]
                        
                        img = CloudinaryImage(public_id)
                        
                        # Generate srcset with multiple sizes
                        widths = [200, 400, 600, 800, 1200] if not width else [width // 2, width, width * 2]
                        for w in widths:
                            optimized_url = img.build_url(transformation=[{
                                'width': w,
                                'quality': 'auto',
                                'fetch_format': 'auto',
                                'flags': 'progressive',
                                'crop': 'limit',
                            }])
                            srcset_parts.append(f"{optimized_url} {w}w")
                        
                        # Optimize base URL
                        base_url = img.build_url(transformation=[{
                            'width': img_width,
                            'quality': 'auto',
                            'fetch_format': 'auto',
                            'flags': 'progressive',
                            'crop': 'limit',
                        }])
            except Exception:
                pass
    
    srcset = ', '.join(srcset_parts) if srcset_parts else ''
    
    # Generate sizes attribute for responsive images
    if srcset:
        sizes = f"(max-width: 640px) 50vw, (max-width: 1024px) 33vw, {img_width}px"
    else:
        sizes = ''
    
    return {
        'url': base_url,
        'srcset': srcset,
        'sizes': sizes,
        'alt': alt,
        'width': img_width,
        'height': img_height,
        'class_name': class_name,
        'lazy': lazy,
        'priority': priority,
    }
