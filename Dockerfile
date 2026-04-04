FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data dirs in the container layer - Railway's volume mount will overlay /data
# at runtime, so we need to ensure subdirs exist AFTER the mount
COPY start.sh .
RUN chmod +x start.sh

# Let Railway set PORT (default 8080)
EXPOSE 8080

CMD ["sh", "start.sh"]