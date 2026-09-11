#!/bin/sh
set -eu
# Same image, two roles. Cloud Run Job sets APP_ROLE=worker or overrides command.
if [ "${APP_ROLE:-api}" = "worker" ]; then
  exec /app/.venv/bin/python -m app.worker
fi
# Cloud Run injects PORT; bind immediately so the health check can succeed.
exec /app/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"
