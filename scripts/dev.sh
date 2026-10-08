#!/usr/bin/env bash
# Start the full local stack: API (8000), preview proxy (8100), worker, web (3000).
# Requires PostgreSQL (with pgvector) and optionally Redis and Docker; see docs/SETUP.md.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/logs
export MIND_STORAGE_DIR="${MIND_STORAGE_DIR:-$PWD/data/storage}"
.venv/bin/python -m mind_api.cli migrate
.venv/bin/uvicorn mind_api.main:app --host 127.0.0.1 --port 8000 >data/logs/api.log 2>&1 &
.venv/bin/uvicorn mind_api.preview_app:app --host 127.0.0.1 --port 8100 >data/logs/preview.log 2>&1 &
.venv/bin/python -m mind_api.worker >data/logs/worker.log 2>&1 &
if [[ "${1:-}" == "--prod-web" ]]; then
  pnpm --filter @mind/web build && pnpm --filter @mind/web start >data/logs/web.log 2>&1 &
else
  pnpm --filter @mind/web dev >data/logs/web.log 2>&1 &
fi
trap 'kill $(jobs -p) 2>/dev/null' EXIT INT TERM
echo "MIND AI: web http://localhost:3000 · API docs http://localhost:8000/api/docs · logs in data/logs/"
wait
