# Dockerfile for Marbaras E-commerce
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (for better caching)
COPY requirements.txt /app/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy project files (large files excluded via .dockerignore)
COPY . /app/
RUN chmod +x /app/entrypoint.sh

# Create logs, static, and media directories (if not using Railway Volume)
# Railway Volume should be mounted at /app/media
RUN mkdir -p /app/logs /app/staticfiles /app/media /app/media/products /app/media/products/multiple

# Expose port
EXPOSE 8000

# Migrations run inside entrypoint.sh before Gunicorn. In Railway: use Dockerfile builder
# and leave "Custom Start Command" empty so this ENTRYPOINT is used (do not set only gunicorn).
ENTRYPOINT ["/app/entrypoint.sh"]
CMD []

