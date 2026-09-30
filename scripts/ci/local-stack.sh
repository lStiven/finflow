#!/usr/bin/env bash
# Start the whole local stack in the background — emulator, seeded data, API,
# workers and the frontend — and wait until it can actually be used.
#
#   scripts/ci/local-stack.sh start   # returns once the e2e suites can run
#   scripts/ci/local-stack.sh stop
#
# "Up" means three things, not one: the API answers, Vite answers, and the
# alerts worker has started — the e2e for alerts waits on a message that only
# that worker delivers, so a green API alone would be a flaky suite.
set -euo pipefail

logs="${CI_LOGS:-.ci-logs}"
mkdir -p "$logs"

wait_for() {
  local what="$1" check="$2" seconds="${3:-240}"
  for _ in $(seq 1 "$seconds"); do
    if eval "$check" >/dev/null 2>&1; then
      echo "listo: $what"
      return 0
    fi
    sleep 1
  done
  echo "no arrancó a tiempo: $what" >&2
  tail -n 80 "$logs"/*.log >&2 || true
  return 1
}

case "${1:-}" in
  start)
    [ -f .env ] || cp .env.example .env
    [ -f frontend/.env.development ] || cp frontend/.env.example frontend/.env.development
    (just up > "$logs/stack.log" 2>&1 &)
    wait_for "la API" "curl -sf http://localhost:8000/health"
    wait_for "el worker de avisos" "grep -q \"worker started | worker='alerts'\" '$logs/stack.log'"
    (just web > "$logs/web.log" 2>&1 &)
    wait_for "el frontend" "curl -sf http://localhost:5173"
    ;;
  stop)
    pkill -f "[r]un_stack.py" || true
    pkill -f "[v]ite" || true
    just aws-down || true
    ;;
  *)
    echo "uso: $0 start|stop" >&2
    exit 2
    ;;
esac
