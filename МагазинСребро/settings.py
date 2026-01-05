from pathlib import Path
import os
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")




def env(key: str, default=None):
    return os.environ.get(key, default)

def env_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key)
    if val is None:
        return default
    return val.lower() in ("1", "true", "yes", "on")

def env_list(key: str, default=None, sep=","):
    val = os.environ.get(key)
    if not val:
        return default or []
    return [item.strip() for item in val.split(sep) if item.strip()]




SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-insecure-change-me")


DEBUG = env_bool("DJANGO_DEBUG", not bool(env("RAILWAY_ENVIRONMENT", "")))


if not DEBUG and SECRET_KEY == "dev-insecure-change-me":
    import warnings
    warnings.warn(
        "SECRET_KEY is using the default insecure value. "
        "Set DJANGO_SECRET_KEY in your environment variables!",
        UserWarning
    )




NGROK_HOST = env("NGROK_HOST", "").strip()

if DEBUG:

    ALLOWED_HOSTS = ["*"]
else:
    base_allowed = {"localhost", "127.0.0.1", "marbaras.com", "www.marbaras.com"}
    ALLOWED_HOSTS = sorted(base_allowed.union(set(env_list("DJANGO_ALLOWED_HOSTS", []))))


default_csrf = {
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "https://*.ngrok-free.app",
    "https://*.loca.lt",
    "https://*.trycloudflare.com",
    "https://marbaras.com",
    "https://www.marbaras.com",
    "http://marbaras.com",
    "http://www.marbaras.com",
}

# Add Railway domain if available
railway_domain = env("RAILWAY_PUBLIC_DOMAIN", "").strip()
if railway_domain:
    default_csrf.add(f"https://{railway_domain}")
    default_csrf.add(f"http://{railway_domain}")

# Also check for Railway custom domain
railway_custom_domain = env("RAILWAY_CUSTOM_DOMAIN", "").strip()
if railway_custom_domain:
    default_csrf.add(f"https://{railway_custom_domain}")
    default_csrf.add(f"http://{railway_custom_domain}")

# Build CSRF_TRUSTED_ORIGINS from defaults and env
CSRF_TRUSTED_ORIGINS = sorted(default_csrf.union(set(env_list("CSRF_TRUSTED_ORIGINS", []))))

# Ensure www.marbaras.com and marbaras.com are always included (critical for production)
if "https://www.marbaras.com" not in CSRF_TRUSTED_ORIGINS:
    CSRF_TRUSTED_ORIGINS.append("https://www.marbaras.com")
if "https://marbaras.com" not in CSRF_TRUSTED_ORIGINS:
    CSRF_TRUSTED_ORIGINS.append("https://marbaras.com")


if NGROK_HOST:
    if ALLOWED_HOSTS != ["*"] and NGROK_HOST not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(NGROK_HOST)
    origin = f"https://{NGROK_HOST}"
    if origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(origin)


for host in env_list("TUNNEL_HOSTS", []):
    if ALLOWED_HOSTS != ["*"] and host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(host)
    origin = f"https://{host}"
    if origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(origin)

SITE_ID = int(env("DJANGO_SITE_ID", "1"))




INSTALLED_APPS = [

    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",


    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "ecommerce.apps.EcommerceConfig",
    
    # Cloudinary for media file storage
    "cloudinary_storage",
    "cloudinary",
]

try:
    import jwt
    import cryptography
    INSTALLED_APPS.append("allauth.socialaccount.providers.google")
except ImportError:
    pass




MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


if not DEBUG:
    MIDDLEWARE.insert(1, 'ecommerce.middleware.WWWRedirectMiddleware')

ROOT_URLCONF = "МагазинСребро.urls"
WSGI_APPLICATION = "МагазинСребро.wsgi.application"





TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.static",
                "ecommerce.context_processors.breadcrumbs",
                "ecommerce.context_processors.cart_count",
                "ecommerce.context_processors.categories",
                "ecommerce.context_processors.meta_pixel_id",
            ],
        },
    },
]





DATABASE_URL = env("DATABASE_URL", "")

if DATABASE_URL:

    import re
    from urllib.parse import urlparse

    try:

        parsed = urlparse(DATABASE_URL)
        db_type = parsed.scheme
        db_user = parsed.username
        db_password = parsed.password
        db_host = parsed.hostname
        db_port = parsed.port or (5432 if 'postgres' in db_type else 3306)
        db_name = parsed.path.lstrip('/')

        if db_type in ('postgresql', 'postgres'):
            DATABASES = {
                "default": {
                    "ENGINE": "django.db.backends.postgresql",
                    "NAME": db_name,
                    "USER": db_user,
                    "PASSWORD": db_password,
                    "HOST": db_host,
                    "PORT": db_port,
                    "CONN_MAX_AGE": int(env("DB_CONN_MAX_AGE", "600")),
                    "OPTIONS": {
                        "connect_timeout": 10,
                    },
                }
            }
        elif db_type == 'mysql':
            DATABASES = {
                "default": {
                    "ENGINE": "django.db.backends.mysql",
                    "NAME": db_name,
                    "USER": db_user,
                    "PASSWORD": db_password,
                    "HOST": db_host,
                    "PORT": db_port,
                    "OPTIONS": {
                        "init_command": "SET sql_mode='STRICT_TRANS_TABLES', time_zone='+00:00'",
                        "charset": "utf8mb4",
                        "connect_timeout": 10,
                    },
                    "CONN_MAX_AGE": int(env("DB_CONN_MAX_AGE", "600")),
                    "ATOMIC_REQUESTS": env_bool("DB_ATOMIC_REQUESTS", False),
                }
            }
        else:

            db_match = re.match(r'(\w+)://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)', DATABASE_URL)
            if db_match:
                db_type, db_user, db_password, db_host, db_port, db_name = db_match.groups()
                if db_type in ('postgresql', 'postgres'):
                    DATABASES = {
                        "default": {
                            "ENGINE": "django.db.backends.postgresql",
                            "NAME": db_name,
                            "USER": db_user,
                            "PASSWORD": db_password,
                            "HOST": db_host,
                            "PORT": db_port,
                            "CONN_MAX_AGE": int(env("DB_CONN_MAX_AGE", "600")),
                            "OPTIONS": {"connect_timeout": 10},
                        }
                    }
                else:
                    DATABASES = {
                        "default": {
                            "ENGINE": "django.db.backends.mysql",
                            "NAME": db_name,
                            "USER": db_user,
                            "PASSWORD": db_password,
                            "HOST": db_host,
                            "PORT": db_port,
                            "OPTIONS": {
                                "init_command": "SET sql_mode='STRICT_TRANS_TABLES', time_zone='+00:00'",
                                "charset": "utf8mb4",
                                "connect_timeout": 10,
                            },
                            "CONN_MAX_AGE": int(env("DB_CONN_MAX_AGE", "600")),
                            "ATOMIC_REQUESTS": env_bool("DB_ATOMIC_REQUESTS", False),
                        }
                    }
            else:

                raise ValueError("Could not parse DATABASE_URL")
    except Exception as e:

        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Failed to parse DATABASE_URL: {e}. Falling back to individual env vars.")
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.mysql",
                "NAME": env("MYSQL_DATABASE", "silvershop"),
                "USER": env("MYSQL_USER", "root"),
                "PASSWORD": env("MYSQL_PASSWORD", ""),
                "HOST": env("MYSQL_HOST", "localhost"),
                "PORT": env("MYSQL_PORT", "3306"),
                "OPTIONS": {
                    "init_command": "SET sql_mode='STRICT_TRANS_TABLES', time_zone='+00:00'",
                    "charset": "utf8mb4",
                },
                "CONN_MAX_AGE": int(env("DB_CONN_MAX_AGE", "600")),
                "ATOMIC_REQUESTS": env_bool("DB_ATOMIC_REQUESTS", False),
            }
        }
else:

    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": env("MYSQL_DATABASE", "silvershop"),
            "USER": env("MYSQL_USER", "root"),
            "PASSWORD": env("MYSQL_PASSWORD", ""),
            "HOST": env("MYSQL_HOST", "localhost"),
            "PORT": env("MYSQL_PORT", "3306"),
            "OPTIONS": {
                "init_command": "SET sql_mode='STRICT_TRANS_TABLES', time_zone='+00:00'",
                "charset": "utf8mb4",
                "connect_timeout": 10,
            },
            "CONN_MAX_AGE": int(env("DB_CONN_MAX_AGE", "600")),
            "ATOMIC_REQUESTS": env_bool("DB_ATOMIC_REQUESTS", False),
        }
    }




AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
ACCOUNT_LOGOUT_REDIRECT_URL = "/"


SESSION_COOKIE_AGE = int(env("SESSION_COOKIE_AGE", "1209600"))
SESSION_SAVE_EVERY_REQUEST = env_bool("SESSION_SAVE_EVERY_REQUEST", False)
SESSION_EXPIRE_AT_BROWSER_CLOSE = env_bool("SESSION_EXPIRE_AT_BROWSER_CLOSE", False)


CSRF_COOKIE_AGE = int(env("CSRF_COOKIE_AGE", "31449600"))
CSRF_COOKIE_HTTPONLY = False  # Set to False to allow JavaScript access if needed
# In production, CSRF_COOKIE_SECURE should be True for HTTPS, but let's be flexible
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", not DEBUG)  # Only secure in production (HTTPS)
CSRF_COOKIE_SAMESITE = 'Lax'  # Lax allows POST from same site
CSRF_USE_SESSIONS = False  # Use cookie-based CSRF (default)
CSRF_FAILURE_VIEW = "django.views.csrf.csrf_failure"
# Ensure CSRF cookie is set for all views
CSRF_COOKIE_NAME = 'csrftoken'
CSRF_HEADER_NAME = 'HTTP_X_CSRFTOKEN'
# Disable CSRF referer check if needed (less secure but works in some edge cases)
CSRF_FAILURE_VIEW = "django.views.csrf.csrf_failure"

ACCOUNT_LOGIN_METHODS = {"username", "email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "username*", "password1*", "password2*"]
ACCOUNT_ADAPTER = "ecommerce.adapters.CustomAccountAdapter"

SOCIALACCOUNT_LOGIN_ON_GET = True
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_EMAIL_REQUIRED = True
SOCIALACCOUNT_EMAIL_VERIFICATION = "none"

SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APP": {
            "client_id": env("GOOGLE_CLIENT_ID", ""),
            "secret": env("GOOGLE_CLIENT_SECRET", ""),
            "key": "",
        },
        "SCOPE": [
            "profile",
            "email",
        ],
        "AUTH_PARAMS": {
            "access_type": "online",
        },
    }
}





LANGUAGE_CODE = "en-us"
TIME_ZONE = "Europe/Sofia"
USE_I18N = True
USE_TZ = True




STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

import os
static_dirs = []
if os.path.exists(BASE_DIR / "static"):
    static_dirs.append(BASE_DIR / "static")
STATICFILES_DIRS = static_dirs


STATICFILES_FINDERS = [
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
]

# Cloudinary Configuration for Media Files
# If Cloudinary credentials are provided, use Cloudinary for media storage
# Otherwise, fall back to local storage
CLOUDINARY_CLOUD_NAME = env("CLOUDINARY_CLOUD_NAME", "")
CLOUDINARY_API_KEY = env("CLOUDINARY_API_KEY", "")
CLOUDINARY_API_SECRET = env("CLOUDINARY_API_SECRET", "")

if CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET:
    # Use Cloudinary for ALL new media storage
    # Old files will still be served from local storage via /media/ URL
    import cloudinary
    import cloudinary.uploader
    import cloudinary.api
    
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
        secure=True
    )
    
    # Use Cloudinary storage directly for new uploads to prevent any local writes
    DEFAULT_FILE_STORAGE = "cloudinary_storage.storage.MediaCloudinaryStorage"
    # Cloudinary storage will generate URLs automatically via url() method
    # MEDIA_URL is used for serving old files from local storage (backward compatibility)
    MEDIA_URL = "/media/"
    # Keep MEDIA_ROOT for old files that are still in Railway volume
    # This allows old images to still be served via /media/ URLs
    # NOTE: We don't create directories here to avoid "No space left on device" errors
    # New files go directly to Cloudinary, old files are read-only from Railway volume
    import os
    if os.path.exists("/app/media"):
        MEDIA_ROOT = "/app/media"
    else:
        MEDIA_ROOT = BASE_DIR / "media"
    # Don't create directories - they should already exist for old files
    # New files will go to Cloudinary, not local storage
    
    # Also configure Cloudinary storage settings
    CLOUDINARY_STORAGE = {
        'CLOUD_NAME': CLOUDINARY_CLOUD_NAME,
        'API_KEY': CLOUDINARY_API_KEY,
        'API_SECRET': CLOUDINARY_API_SECRET,
    }
else:
    # Fall back to local storage
    MEDIA_URL = "/media/"
    import os
    if os.path.exists("/app/media"):
        MEDIA_ROOT = "/app/media"
    else:
        MEDIA_ROOT = BASE_DIR / "media"
    os.makedirs(MEDIA_ROOT, exist_ok=True)


# Increased for video file uploads (100MB = 104857600 bytes)
FILE_UPLOAD_MAX_MEMORY_SIZE = int(env("FILE_UPLOAD_MAX_MEMORY_SIZE", "104857600"))
DATA_UPLOAD_MAX_MEMORY_SIZE = int(env("DATA_UPLOAD_MAX_MEMORY_SIZE", "104857600"))
DATA_UPLOAD_MAX_NUMBER_FIELDS = int(env("DATA_UPLOAD_MAX_NUMBER_FIELDS", "1000"))




DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "support@marbaras.com")
SERVER_EMAIL = DEFAULT_FROM_EMAIL
ADMINS = [("Site Admin", env("ADMIN_EMAIL", "support@marbaras.com"))]

# Email Configuration
# Django uses its built-in email functions - no 3rd party library needed!
# You just need to configure an SMTP server (Gmail, Outlook, or any SMTP server)

# Email Backend Options:
# 1. SMTP Backend (for production) - requires SMTP server credentials
#    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
# 2. Console Backend (for development) - prints emails to console
#    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
# 3. File Backend (for testing) - saves emails to files
#    EMAIL_BACKEND = "django.core.mail.backends.filebased.EmailBackend"
#    EMAIL_FILE_PATH = BASE_DIR / "emails"

# Email Backend Configuration
# Railway Hobby plan does NOT support SMTP connections
# Use API-based email services instead:
#
# Option 1: Resend (Railway's recommended service)
#   EMAIL_BACKEND = "ecommerce.utils.resend_backend.ResendBackend"
#   RESEND_API_KEY = "your-resend-api-key"
#
# Option 2: SendGrid
#   EMAIL_BACKEND = "ecommerce.utils.sendgrid_backend.SendGridBackend"
#   SENDGRID_API_KEY = "your-sendgrid-api-key"
#
# Option 3: SMTP (only works on Railway Pro plan or other hosting)
#   EMAIL_BACKEND = "ecommerce.utils.smtp_backend.SMTPSBackend"
#   EMAIL_HOST = "mail.marbaras.com"
#   EMAIL_PORT = 465
#   EMAIL_USE_SSL = True
#
# Default: Try Resend first (works on Railway Hobby), fallback to SMTP if API key not set
RESEND_API_KEY = env("RESEND_API_KEY", "")
SENDGRID_API_KEY = env("SENDGRID_API_KEY", "")

if RESEND_API_KEY:
    EMAIL_BACKEND = "ecommerce.utils.resend_backend.ResendBackend"
elif SENDGRID_API_KEY:
    EMAIL_BACKEND = "ecommerce.utils.sendgrid_backend.SendGridBackend"
else:
    # Fallback to SMTP (will fail on Railway Hobby plan)
    EMAIL_BACKEND = env("EMAIL_BACKEND", "ecommerce.utils.smtp_backend.SMTPSBackend")

# SMTP Configuration
# Jump.bg Email Hosting Configuration
# Jump.bg typically uses these SMTP settings:
#   EMAIL_HOST = "mail.jump.bg" or "smtp.jump.bg"
#   EMAIL_PORT = 587 (for TLS) or 465 (for SSL)
#   EMAIL_USE_TLS = True (for port 587)
#   EMAIL_USE_SSL = True (for port 465)

# Default to jump.bg SMTP settings
# For SMTPS (SMTP over SSL) on port 465:
#   EMAIL_PORT = 465
#   EMAIL_USE_SSL = True
#   EMAIL_USE_TLS = False
# For SMTP with STARTTLS on port 587:
#   EMAIL_PORT = 587
#   EMAIL_USE_TLS = True
#   EMAIL_USE_SSL = False
EMAIL_HOST = env("EMAIL_HOST", "mail.marbaras.com")
EMAIL_PORT = int(env("EMAIL_PORT", "465"))  # Default to 465 for SMTPS
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", False)  # False for SMTPS on port 465
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", True)  # True for SMTPS on port 465
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "support@marbaras.com")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
# Increased timeout for SMTP connections (especially SSL connections can take longer)
EMAIL_TIMEOUT = int(env("EMAIL_TIMEOUT", "60"))

# SSL Context settings for SMTPS (optional, Django handles this automatically)
# If you have certificate issues, you can uncomment and configure:
# import ssl
# EMAIL_SSL_CERTFILE = None  # Path to certificate file if needed
# EMAIL_SSL_KEYFILE = None   # Path to key file if needed

# For development/testing, you can use console backend:
# Set EMAIL_BACKEND=console in your .env file to see emails in console instead of sending them





def _clean_stripe_key(key: str) -> str:
    """Clean Stripe API key by removing all whitespace, newlines, and non-printable characters."""
    if not key:
        return ""
    # Remove all whitespace characters (spaces, tabs, newlines, etc.)
    cleaned = ''.join(key.split())
    # Remove any remaining non-printable characters except alphanumeric and underscore/hyphen
    cleaned = ''.join(c for c in cleaned if c.isprintable() and (c.isalnum() or c in ['_', '-']))
    return cleaned

STRIPE_SECRET_KEY = _clean_stripe_key(env("STRIPE_SECRET_KEY", ""))

# Meta Pixel (Facebook Pixel) Configuration
# Get your Pixel ID from Facebook Events Manager: https://business.facebook.com/events_manager
META_PIXEL_ID = env("META_PIXEL_ID", "")
STRIPE_PUBLISHABLE_KEY = _clean_stripe_key(env("STRIPE_PUBLISHABLE_KEY", ""))
STRIPE_WEBHOOK_SECRET = _clean_stripe_key(env("STRIPE_WEBHOOK_SECRET", ""))

# -------------------------
# Caching (Redis/Memcached)
# -------------------------
REDIS_URL = env("REDIS_URL", "")
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
            },
            "KEY_PREFIX": "marbaras",
            "TIMEOUT": 300,  # 5 minutes default
        }
    }
    # Use Redis for session storage in production
    if not DEBUG:
        SESSION_ENGINE = "django.contrib.sessions.backends.cache"
        SESSION_CACHE_ALIAS = "default"
else:
    # Default cache (local memory)
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "unique-snowflake",
        }
    }

# -------------------------
# Security (production)
# -------------------------
if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    # CSRF_COOKIE_SECURE is set above based on env, allow override
    # CSRF_COOKIE_SECURE = True  # Commented to use env-based setting above
    SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_BROWSER_XSS_FILTER = True
    X_FRAME_OPTIONS = "DENY"
    
    # Additional security headers
    SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
    
    # Support for reverse proxy (nginx, etc.)
    USE_X_FORWARDED_HOST = env_bool("USE_X_FORWARDED_HOST", True)
    USE_X_FORWARDED_PORT = env_bool("USE_X_FORWARDED_PORT", True)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# -------------------------
# Logging
# -------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose" if not DEBUG else "simple",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": BASE_DIR / "logs" / "django.log",
            "maxBytes": 1024 * 1024 * 10,  # 10 MB
            "backupCount": 5,
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"] + (["file"] if not DEBUG else []),
        "level": "INFO" if not DEBUG else "DEBUG",
    },
    "loggers": {
        "django": {
            "handlers": ["console"] + (["file"] if not DEBUG else []),
            "level": "INFO",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"] + (["file"] if not DEBUG else []),
            "level": "ERROR",
            "propagate": False,
        },
        "django.security": {
            "handlers": ["console"] + (["file"] if not DEBUG else []),
            "level": "WARNING",
            "propagate": False,
        },
        "django.core.mail": {
            "handlers": ["console"],
            "level": "DEBUG" if DEBUG else "INFO",
            "propagate": False,
        },
        "ecommerce": {
            "handlers": ["console"] + (["file"] if not DEBUG else []),
            "level": "DEBUG" if DEBUG else "INFO",
            "propagate": False,
        },
        "stripe": {
            "handlers": ["console"] + (["file"] if not DEBUG else []),
            "level": "WARNING",
            "propagate": False,
        },
    },
}

# -------------------------
# Defaults
# -------------------------
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
