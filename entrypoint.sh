#!/bin/bash
set -e

echo "=========================================="
echo "Starting Marbaras E-commerce Application"
echo "=========================================="

# Check if DATABASE_URL is set (Railway, Render, etc.)
if [ -z "$DATABASE_URL" ] && [ -z "$MYSQL_HOST" ] && [ -z "$POSTGRES_HOST" ]; then
    echo "⚠️  Warning: No database configuration found!"
    echo "Please set DATABASE_URL or MySQL/PostgreSQL environment variables"
    echo "Continuing anyway - application may fail to start..."
else
    echo "Database configuration found, waiting for connection..."
    MAX_RETRIES=30
    RETRY_COUNT=0

    # Use Python script to check database connection (more reliable than dbshell)
    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        if python << EOF
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'МагазинСребро.settings')
django.setup()
from django.db import connection
try:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    exit(0)
except Exception as e:
    exit(1)
EOF
        then
            echo "✅ Database connection successful!"
            break
        fi
        RETRY_COUNT=$((RETRY_COUNT + 1))
        if [ $RETRY_COUNT -le 10 ]; then
            echo "Database is unavailable - retry $RETRY_COUNT/$MAX_RETRIES..."
        fi
        sleep 2
    done

    if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
        echo "⚠️  Warning: Could not connect to database after $MAX_RETRIES retries"
        echo "Continuing anyway - migrations will attempt to connect..."
    fi
fi

# Ensure media directories exist (create if Railway Volume is not mounted)
echo "Ensuring media directories exist..."
mkdir -p /app/media/products /app/media/products/multiple || {
    echo "Could not create media directories, but continuing..."
}

# Migrations run here before Gunicorn. Use Dockerfile ENTRYPOINT on Railway; do not replace with gunicorn-only start.
echo "Running migrations..."
python manage.py migrate --noinput 2>&1 | tee /tmp/migration_output.log
MIGRATION_EXIT_CODE=${PIPESTATUS[0]}

if [ $MIGRATION_EXIT_CODE -ne 0 ]; then
    echo "Migration exited with code $MIGRATION_EXIT_CODE"
    
    # Check if error is about existing table (MySQL error 1050)
    if grep -q "Table.*already exists" /tmp/migration_output.log || grep -q "1050" /tmp/migration_output.log; then
        echo "⚠️  Table already exists error detected. Attempting to mark migration as applied..."
        
        # Try to mark the problematic migration as applied if table exists
        python << EOF
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'МагазинСребро.settings')
django.setup()
from django.db import connection

try:
    with connection.cursor() as cursor:
        # Check if ecommerce_product_categories table exists
        cursor.execute("""
            SELECT COUNT(*) 
            FROM information_schema.tables 
            WHERE table_schema = DATABASE() 
            AND table_name = 'ecommerce_product_categories'
        """)
        table_exists = cursor.fetchone()[0] > 0
        
        if table_exists:
            # Check if migration is already recorded
            cursor.execute("""
                SELECT COUNT(*) 
                FROM django_migrations 
                WHERE app = 'ecommerce' 
                AND name = '0008_add_categories_manytomany'
            """)
            migration_recorded = cursor.fetchone()[0] > 0
            
            if not migration_recorded:
                print("Marking migration 0008_add_categories_manytomany as applied...")
                cursor.execute("""
                    INSERT INTO django_migrations (app, name, applied) 
                    VALUES ('ecommerce', '0008_add_categories_manytomany', NOW())
                """)
                print("✅ Migration marked as applied")
            else:
                print("Migration already recorded")
        else:
            print("Table does not exist, cannot mark migration as applied")
except Exception as e:
    print(f"⚠️  Could not mark migration as applied: {e}")
EOF

        echo "Retrying migrations after 0008 workaround..."
        python manage.py migrate --noinput 2>&1 | tee /tmp/migration_output_retry.log
        RETRY_CODE=${PIPESTATUS[0]}
        if [ $RETRY_CODE -ne 0 ]; then
            echo "Migrations still failing after retry (exit $RETRY_CODE). See logs above."
            exit $RETRY_CODE
        fi
        echo "Migrations completed after retry."
    else
        echo "Migration failed — not starting Gunicorn. Fix the error in logs, then redeploy."
        exit $MIGRATION_EXIT_CODE
    fi
else
    echo "Migrations applied successfully."
fi

# Collect static files
echo "Collecting static files..."
python manage.py collectstatic --noinput --clear || {
    echo "Collectstatic failed, but continuing..."
}
# Ensure banner and payment methods image exist in staticfiles (always copy after collectstatic)
if [ -f "static/images/banner.jpg" ]; then
    echo "✅ Ensuring banner.jpg is in staticfiles..."
    mkdir -p staticfiles/images
    cp static/images/banner.jpg staticfiles/images/banner.jpg && echo "✅ Banner copied successfully" || echo "⚠️  Could not copy banner"
elif [ -f "staticfiles/images/banner.jpg" ]; then
    echo "✅ Banner already exists in staticfiles"
else
    echo "⚠️  Warning: Banner not found in static/images/"
fi

if [ -f "static/images/payment-methods.jpg" ]; then
    echo "✅ Ensuring payment-methods.jpg is in staticfiles..."
    mkdir -p staticfiles/images
    cp static/images/payment-methods.jpg staticfiles/images/payment-methods.jpg && echo "✅ Payment methods image copied successfully" || echo "⚠️  Could not copy payment methods image"
elif [ -f "staticfiles/images/payment-methods.jpg" ]; then
    echo "✅ Payment methods image already exists in staticfiles"
fi

if [ -f "static/images/image0.png" ]; then
    echo "✅ Ensuring image0.png is in staticfiles..."
    mkdir -p staticfiles/images
    cp static/images/image0.png staticfiles/images/image0.png && echo "✅ Payment methods image (image0.png) copied successfully" || echo "⚠️  Could not copy image0.png"
elif [ -f "staticfiles/images/image0.png" ]; then
    echo "✅ image0.png already exists in staticfiles"
fi

if [ -f "static/images/image2.jpeg" ]; then
    echo "✅ Ensuring image2.jpeg (logo) is in staticfiles..."
    mkdir -p staticfiles/images
    cp static/images/image2.jpeg staticfiles/images/image2.jpeg && echo "✅ Logo image (image2.jpeg) copied successfully" || echo "⚠️  Could not copy image2.jpeg"
elif [ -f "staticfiles/images/image2.jpeg" ]; then
    echo "✅ image2.jpeg already exists in staticfiles"
fi

# Ensure admin custom static files are copied
if [ -f "static/admin/js/sale_timer.js" ]; then
    echo "✅ Ensuring admin/js/sale_timer.js is in staticfiles..."
    mkdir -p staticfiles/admin/js
    cp static/admin/js/sale_timer.js staticfiles/admin/js/sale_timer.js && echo "✅ Admin JS copied successfully" || echo "⚠️  Could not copy admin JS"
fi

if [ -f "static/admin/css/sale_timer.css" ]; then
    echo "✅ Ensuring admin/css/sale_timer.css is in staticfiles..."
    mkdir -p staticfiles/admin/css
    cp static/admin/css/sale_timer.css staticfiles/admin/css/sale_timer.css && echo "✅ Admin CSS copied successfully" || echo "⚠️  Could not copy admin CSS"
fi

# Import data if database is empty (only on first run)
echo "Checking if database needs initial data import..."
python << EOF
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'МагазинСребро.settings')
django.setup()
from django.db import connection
from django.core.management import call_command

try:
    # Check if database has any data
    with connection.cursor() as cursor:
        # Check if any tables exist and have data
        cursor.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE()")
        table_count = cursor.fetchone()[0]
        
        if table_count > 0:
            # Check if Product table exists and has data
            try:
                cursor.execute("SELECT COUNT(*) FROM ecommerce_product")
                product_count = cursor.fetchone()[0]
                
                if product_count == 0:
                    print("📦 Database is empty, importing initial data...")
                    try:
                        # Try to import from local file first (after deploy)
                        import os
                        local_file = '/app/data.json'
                        if os.path.exists(local_file):
                            print(f"Found local data.json, importing...")
                            call_command('import_data', '--file', local_file)
                        else:
                            # Fallback: try to download from GitHub
                            print("Local file not found, trying GitHub...")
                            call_command('import_data', '--url', 'https://raw.githubusercontent.com/uigeadall/marbaras123/newone/data.json')
                        print("✅ Data imported successfully!")
                    except Exception as e:
                        print(f"⚠️  Could not import data: {e}")
                        print("You can import data manually later using: python manage.py import_data")
                else:
                    print(f"✅ Database already has {product_count} products, skipping import.")
            except Exception as e:
                # Table doesn't exist yet, skip import
                print(f"⚠️  Could not check database: {e}")
        else:
            print("⚠️  No tables found, skipping data import.")
except Exception as e:
    print(f"⚠️  Could not check database: {e}")
EOF

# Create superuser if it doesn't exist (optional)
# Uncomment if you want to auto-create superuser
# python manage.py shell << EOF
# from django.contrib.auth import get_user_model
# User = get_user_model()
# if not User.objects.filter(username='admin').exists():
#     User.objects.create_superuser('admin', 'admin@example.com', 'changeme')
# EOF

echo "=========================================="
echo "Starting Gunicorn..."
echo "=========================================="

# Use PORT environment variable if provided (Railway, Render, etc.), otherwise default to 8000
PORT=${PORT:-8000}

echo "Starting Gunicorn on port $PORT..."

# Start Gunicorn
exec gunicorn \
    --bind 0.0.0.0:$PORT \
    --workers 4 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile - \
    --log-level info \
    МагазинСребро.wsgi:application

