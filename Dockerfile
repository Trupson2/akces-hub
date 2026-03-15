# ============================================================
# AKCES HUB - Production Dockerfile
# ============================================================
FROM python:3.11-slim AS base

# System deps for Pillow, lxml, reportlab, python-escpos
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libjpeg62-turbo-dev \
    zlib1g-dev \
    libxml2-dev \
    libxslt1-dev \
    libusb-1.0-0 \
    libcups2-dev \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r akces && useradd -r -g akces -d /app -s /sbin/nologin akces

WORKDIR /app

# Install Python dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .
COPY modules/ modules/
COPY static/ static/
COPY templates/ templates/
COPY images/ images/
COPY scripts/ scripts/
COPY deploy/ deploy/

# Create data directories (will be overridden by volume mounts)
RUN mkdir -p /app/data /app/backups /app/cloud_exports /app/logs \
    && chown -R akces:akces /app

# Environment
ENV FLASK_ENV=production \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 5000

USER akces

# Use waitress as production WSGI server
CMD ["python", "-m", "waitress", "--host=0.0.0.0", "--port=5000", "--threads=4", "app:app"]
