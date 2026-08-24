#!/usr/bin/env bash
# make demo — the whole platform on synthetic data, one command (§19, §24).
#
#   scripts/demo.sh              containers: docker compose -f docker-compose.demo.yml
#   scripts/demo.sh --native     host processes: uv + node, no Docker at all
#
# Neither path needs a Snowflake account, cloud credentials, or an LLM API key.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT/docker-compose.demo.yml"
PORT="${SNOWOBS_DEMO_PORT:-8080}"
NATIVE=0

for arg in "$@"; do
  case "$arg" in
    --native) NATIVE=1 ;;
    *) echo "usage: $0 [--native]" >&2; exit 2 ;;
  esac
done

base_url="http://localhost:${PORT}"

# ── smoke: prove the app really answers before telling anyone to open it ─────
smoke() {
  local url="$1" failures=0
  echo "==> Checking the app answers"
  for path in /healthz /api/v1/meta /api/v1/datasets/coverage \
              /api/v1/metrics/cost.total_credits/tile \
              /api/v1/chargeback/allocation; do
    if curl -fsS --max-time 30 -o /dev/null "${url}${path}"; then
      echo "    ok   ${path}"
    else
      echo "    FAIL ${path}" >&2
      failures=$((failures + 1))
    fi
  done
  return "$failures"
}

banner() {
  local url="$1"
  cat <<EOF

  ────────────────────────────────────────────────────────────────────────
   Observability & FinOps Platform for Snowflake — demo is up.

     App          ${url}/
     API docs     ${url}/docs
     Coverage     ${url}/api/v1/datasets/coverage

   The data is a synthetic Snowflake account: 120 days, 12 warehouses,
   8 teams, planted cost and reliability phenomena. Nothing here came from
   a real account. Walk-through: docs/DEMO.md
  ────────────────────────────────────────────────────────────────────────

EOF
}

if [[ "$NATIVE" -eq 1 ]]; then
  echo "==> Native demo (no Docker)"
  command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/" >&2; exit 1; }
  command -v npm >/dev/null || { echo "Node 22+ (npm) is required" >&2; exit 1; }

  echo "==> Syncing the Python workspace"
  (cd "$ROOT" && uv sync --all-packages --dev)

  echo "==> Seeding the demo dataset"
  (cd "$ROOT" && uv run python scripts/demo_seed.py --root .data)

  echo "==> Building the SPA"
  (cd "$ROOT/apps/web" && npm install --no-audit --no-fund && npm run build)

  echo "==> Starting the app on ${base_url}"
  (cd "$ROOT" && SNOWOBS_MODE=offline SNOWOBS_LOG_JSON=false \
    uv run uvicorn allinone.asgi:create_app --factory \
      --app-dir deploy/docker --host 127.0.0.1 --port "$PORT") &
  APP_PID=$!
  trap 'kill "$APP_PID" 2>/dev/null || true' EXIT INT TERM

  for _ in $(seq 1 60); do
    curl -fsS --max-time 2 -o /dev/null "${base_url}/healthz" && break
    sleep 1
  done

  smoke "$base_url"
  banner "$base_url"
  echo "Ctrl-C to stop."
  wait "$APP_PID"
  exit 0
fi

command -v docker >/dev/null || {
  echo "Docker is required for 'make demo'. Without it, run: make demo-native" >&2
  exit 1
}
docker compose version >/dev/null 2>&1 || {
  echo "Docker Compose v2 is required (docker compose version)." >&2
  exit 1
}

echo "==> Building and starting the demo stack (first run pulls and builds; later runs are cached)"
docker compose -f "$COMPOSE_FILE" up -d --build --wait

smoke "$base_url"
banner "$base_url"
echo "Logs:  docker compose -f docker-compose.demo.yml logs -f app"
echo "Stop:  make demo-down"
