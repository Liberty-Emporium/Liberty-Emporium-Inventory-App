#!/bin/sh
set -e
PORT="${PORT:-8080}"
DATA_DIR="${RAILWAY_DATA_DIR:-/data}"

echo "[START] Ensuring data directories exist at $DATA_DIR"
mkdir -p "$DATA_DIR/ads" "$DATA_DIR/uploads" "$DATA_DIR/backups" "$DATA_DIR/music"
chmod -R 755 "$DATA_DIR"

echo "[START] Binding to port $PORT"
exec gunicorn app_with_ai:app \
  --bind "0.0.0.0:${PORT}" \
  --timeout 300 \
  --workers 1 \
  --worker-class gthread \
  --threads 4 \
  --capture-output \
  --log-level info
