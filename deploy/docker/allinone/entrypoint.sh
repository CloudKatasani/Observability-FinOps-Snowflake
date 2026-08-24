#!/usr/bin/env bash
# Process supervisor for the all-in-one image.
#
# The image is one artefact running more than one process, so something has to
# own their lifetimes. This is deliberately a readable 60 lines rather than an
# init system: a reviewer can see exactly what runs, and the container still
# dies when any component dies, which is what the orchestrator needs in order
# to restart it.
#
# Components are named as arguments, so the same image serves several shapes
# without an environment switch:
#
#   snowobs-allinone api worker    # default: full platform (needs Redis)
#   snowobs-allinone api           # read-only OFFLINE demo, no Redis needed
#   snowobs-allinone seed          # generate + ingest the demo dataset, then exit
#
set -euo pipefail

# The container always listens on 8080; publish it elsewhere with `docker -p`.
APP_PORT=8080
PIDS=()
NAMES=()

log() { printf '%s allinone: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

start() {
  local name="$1"
  shift
  log "starting ${name}"
  "$@" &
  PIDS+=("$!")
  NAMES+=("${name}")
}

terminate() {
  local pid
  for pid in "${PIDS[@]:-}"; do
    [[ -n "${pid}" ]] && kill "${pid}" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap terminate EXIT INT TERM

if [[ $# -eq 0 ]]; then
  set -- api worker
fi

# `seed` is a one-shot: it must run to completion and exit, not be supervised.
if [[ "$1" == "seed" ]]; then
  shift
  log "seeding demo data"
  exec python /app/scripts/demo_seed.py "$@"
fi

for component in "$@"; do
  case "${component}" in
    api)
      start api uvicorn allinone.asgi:create_app --factory \
        --host 0.0.0.0 --port "${APP_PORT}" --proxy-headers --forwarded-allow-ips '*'
      ;;
    worker)
      start worker arq snowobs_worker.main.WorkerSettings
      ;;
    *)
      log "unknown component '${component}' (expected: api, worker, seed)"
      exit 2
      ;;
  esac
done

log "supervising: ${NAMES[*]}"
# Exit as soon as any component exits, with its status, so a crash-looping
# worker is visible to the orchestrator instead of being masked by a healthy API.
wait -n
status=$?
log "a component exited with status ${status}; shutting the container down"
exit "${status}"
