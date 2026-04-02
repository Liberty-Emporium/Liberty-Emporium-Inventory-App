#!/bin/sh
set -e
PORT="${PORT:-5000}"
echo "[START] Binding to port $PORT"
exec gunicorn app_with_ai:app \
  --bind "0.0.0.0:${PORT}" \
  --timeout 300 \
  --workers 1 \
  --worker-class gthread \
  --threads 4 \
  --capture-output \
  --log-level debug \
  --error-logfile - \
  --access-logfile -
