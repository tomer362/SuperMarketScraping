#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEBAPP_DIR="$ROOT_DIR/webapp"
BACKEND_DIR="$WEBAPP_DIR/backend"
FRONTEND_DIR="$WEBAPP_DIR/frontend"
LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/supermarket-webapp.XXXXXX")"

BACKEND_PORT=8000
FRONTEND_START_PORT=5173
FRONTEND_END_PORT=5205
RUN_MODE="full"
DEBUG_MODE=0

for arg in "$@"; do
  case "$arg" in
    --seed)
      RUN_MODE="seed"
      ;;
    --debug)
      DEBUG_MODE=1
      ;;
    *)
      fail "Unknown argument: $arg"
      ;;
  esac
done

BACKEND_PID=""
FRONTEND_PID=""
BACKEND_STARTED=0
FRONTEND_STARTED=0
FRONTEND_PORT=""

cleanup() {
  if [ "$FRONTEND_STARTED" -eq 1 ] && [ -n "$FRONTEND_PID" ] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
    kill "$FRONTEND_PID" 2>/dev/null || true
  fi
  if [ "$BACKEND_STARTED" -eq 1 ] && [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    kill "$BACKEND_PID" 2>/dev/null || true
  fi
}

trap cleanup INT TERM

log() {
  printf '%s\n' "$*"
}

fail() {
  printf '%s\n' "$*" >&2
  exit 1
}

find_python() {
  local candidates=()
  local candidate

  candidates+=("$ROOT_DIR/venv/bin/python3")
  candidates+=("$ROOT_DIR/venv/bin/python")
  candidates+=("$ROOT_DIR/.venv/bin/python3")
  candidates+=("$ROOT_DIR/.venv/bin/python")
  candidates+=("/Library/Frameworks/Python.framework/Versions/3.10/bin/python3")
  candidates+=("$(command -v python3 2>/dev/null || true)")
  candidates+=("$(command -v python 2>/dev/null || true)")

  for candidate in "${candidates[@]}"; do
    [ -x "$candidate" ] || continue
    if "$candidate" -c 'import fastapi, sqlalchemy' >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  return 1
}

backend_ready() {
  curl -fsS "http://127.0.0.1:${BACKEND_PORT}/api/chains" >/dev/null 2>&1
}

detect_frontend_port() {
  local port response

  port=$FRONTEND_START_PORT
  while [ "$port" -le "$FRONTEND_END_PORT" ]; do
    response="$(curl -fsS "http://127.0.0.1:${port}" 2>/dev/null || true)"
    if [[ "$response" == *"/@vite/client"* && "$response" == *"השוואת מחירים סופרמרקט"* ]]; then
      printf '%s\n' "$port"
      return 0
    fi
    port=$((port + 1))
  done

  return 1
}

wait_for_backend() {
  local attempts=60
  while [ "$attempts" -gt 0 ]; do
    if backend_ready; then
      return 0
    fi
    if [ -n "$BACKEND_PID" ] && ! kill -0 "$BACKEND_PID" 2>/dev/null; then
      break
    fi
    sleep 1
    attempts=$((attempts - 1))
  done

  return 1
}

wait_for_frontend() {
  local attempts=60
  while [ "$attempts" -gt 0 ]; do
    FRONTEND_PORT="$(detect_frontend_port || true)"
    if [ -n "$FRONTEND_PORT" ]; then
      return 0
    fi
    if [ -n "$FRONTEND_PID" ] && ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
      break
    fi
    sleep 1
    attempts=$((attempts - 1))
  done

  return 1
}

if backend_ready; then
  log "Backend already running at http://127.0.0.1:${BACKEND_PORT}"
else
  PYTHON_BIN="$(find_python)" || fail 'Could not find a Python interpreter with backend dependencies.'
  if [ "$RUN_MODE" = "seed" ]; then
    BACKEND_DB_PATH="$BACKEND_DIR/webapp_seed.sqlite3"
    SEED_TEST_DATA_FLAG=1
    RESET_TEST_DB_FLAG=1
    log "Starting backend in seed mode (small demo catalog) at http://127.0.0.1:${BACKEND_PORT}"
  else
    BACKEND_DB_PATH="$BACKEND_DIR/webapp_local.sqlite3"
    SEED_TEST_DATA_FLAG=0
    RESET_TEST_DB_FLAG=0
    log "Starting backend in full-data mode at http://127.0.0.1:${BACKEND_PORT}"
  fi
  (
    cd "$BACKEND_DIR"
    ENABLE_SCHEDULER=0 \
    AUTO_REFRESH_ON_START=0 \
    CATALOG_DEBUG="$DEBUG_MODE" \
    LOG_LEVEL="${LOG_LEVEL:-INFO}" \
    SEED_TEST_DATA="$SEED_TEST_DATA_FLAG" \
    RESET_TEST_DB_ON_START="$RESET_TEST_DB_FLAG" \
    SESSION_COOKIE_SECURE=0 \
    DATABASE_URL="sqlite+aiosqlite:///$BACKEND_DB_PATH" \
    "$PYTHON_BIN" -m uvicorn main:app --host 127.0.0.1 --port "$BACKEND_PORT"
  ) >"$LOG_DIR/backend.log" 2>&1 &
  BACKEND_PID=$!
  BACKEND_STARTED=1

  if ! wait_for_backend; then
    log "Backend failed to start. Log: $LOG_DIR/backend.log"
    exit 1
  fi
fi

if [ -d "$FRONTEND_DIR/node_modules" ]; then
  :
else
  log "Installing frontend dependencies"
  (
    cd "$FRONTEND_DIR"
    npm install
  )
fi

FRONTEND_PORT="$(detect_frontend_port || true)"
if [ -n "$FRONTEND_PORT" ]; then
  log "Frontend already running at http://127.0.0.1:${FRONTEND_PORT}"
else
  log "Starting frontend"
  (
    cd "$FRONTEND_DIR"
    VITE_API_DEBUG="$DEBUG_MODE" npm run dev -- --host 127.0.0.1 --port "$FRONTEND_START_PORT"
  ) >"$LOG_DIR/frontend.log" 2>&1 &
  FRONTEND_PID=$!
  FRONTEND_STARTED=1

  if ! wait_for_frontend; then
    log "Frontend failed to start. Log: $LOG_DIR/frontend.log"
    exit 1
  fi

  log "Frontend ready at http://127.0.0.1:${FRONTEND_PORT}"
fi

log "Web app ready"
log "  Frontend: http://127.0.0.1:${FRONTEND_PORT}"
log "  Backend:  http://127.0.0.1:${BACKEND_PORT}"
log "  Logs:     $LOG_DIR"
if [ "$DEBUG_MODE" -eq 1 ]; then
  log "  Debug:    enabled (CATALOG_DEBUG=1, VITE_API_DEBUG=1)"
fi
if [ "$RUN_MODE" = "full" ]; then
  log "  Mode:     full-data (persistent SQLite at $BACKEND_DIR/webapp_local.sqlite3)"
else
  log "  Mode:     seed demo (small dataset)"
fi

if [ "$BACKEND_STARTED" -eq 0 ] && [ "$FRONTEND_STARTED" -eq 0 ]; then
  exit 0
fi

log "Press Ctrl-C to stop the services started by this script."
log "Or run ./stop-webapp.sh to stop local webapp services later."

while true; do
  if [ "$BACKEND_STARTED" -eq 1 ] && ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    cleanup
    fail 'Backend exited unexpectedly.'
  fi

  if [ "$FRONTEND_STARTED" -eq 1 ] && ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
    cleanup
    fail 'Frontend exited unexpectedly.'
  fi

  sleep 2
done
