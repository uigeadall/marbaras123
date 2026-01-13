
from django.apps import AppConfig

class EcommerceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ecommerce"

    def ready(self):
        from . import signals
        
        # Import template tags to ensure they are registered
        try:
            import ecommerce.templatetags.blog_filters  # noqa
            import ecommerce.templatetags.cart_extras  # noqa
            import ecommerce.templatetags.image_optimization  # noqa
        except ImportError:
            pass
