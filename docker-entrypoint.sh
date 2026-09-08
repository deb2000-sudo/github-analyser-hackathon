#!/bin/sh
set -eu
# Cloud Run injects PORT; bind immediately so the health check can succeed.
exec /app/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"
