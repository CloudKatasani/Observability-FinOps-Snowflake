#!/usr/bin/env bash
# make dev — infra in containers, application processes on the host with hot reload.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT/deploy/compose/docker-compose.yml"

echo "==> Starting infrastructure (postgres, redis, minio)"
docker compose -f "$COMPOSE_FILE" up -d --wait postgres redis minio minio-init

PIDS=()
cleanup() {
  echo
  echo "==> Stopping application processes"
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  echo "==> Infrastructure containers left running; stop them with: make infra-down"
}
trap cleanup EXIT INT TERM

echo "==> Starting API (http://localhost:8000)"
(cd "$ROOT" && SNOWOBS_LOG_JSON=false uv run uvicorn snowobs_api.main:app --reload --port 8000) &
PIDS+=($!)

echo "==> Starting worker"
(cd "$ROOT" && SNOWOBS_LOG_JSON=false uv run arq snowobs_worker.main.WorkerSettings --watch apps/worker/src) &
PIDS+=($!)

echo "==> Starting web (http://localhost:5173)"
(cd "$ROOT/apps/web" && npm run dev) &
PIDS+=($!)

echo "==> All services started. Ctrl-C to stop."
wait -n || true
