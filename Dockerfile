FROM python:3.11-slim

# Install system dependencies including ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy entire app
COPY . .

# Create directories for music and ads if they don't exist (local app dirs)
RUN mkdir -p /app/music /app/ads /app/uploads

# Create persistent data directory (will be mounted as volume on Railway)
RUN mkdir -p /data/ads /data/uploads /data/backups /data/music && \
    chmod -R 755 /data

# Set environment variable for persistent storage
ENV RAILWAY_DATA_DIR=/data

# Expose port
EXPOSE 5000

# Run with gunicorn
CMD ["python", "-m", "gunicorn", "app_with_ai:app", "--bind", "0.0.0.0:5000", "--timeout", "300", "--workers", "1", "--worker-class", "gthread", "--threads", "4"]
