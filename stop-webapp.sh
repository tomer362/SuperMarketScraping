#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_PORT=8000
FRONTEND_START_PORT=5173
FRONTEND_END_PORT=5205

stopped_any=0

log() {
  printf '%s\n' "$*"
}

stop_pid() {
  local pid="$1"
  local label="$2"

  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    sleep 0.25
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
    log "Stopped $label (pid $pid)"
    stopped_any=1
  fi
}

for pid in $(lsof -ti tcp:"$BACKEND_PORT" 2>/dev/null || true); do
  cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  if [[ "$cmd" == *"uvicorn main:app"* ]] && [[ "$cmd" == *"$ROOT_DIR/webapp/backend"* ]]; then
    stop_pid "$pid" "backend"
  fi
done

port="$FRONTEND_START_PORT"
while [ "$port" -le "$FRONTEND_END_PORT" ]; do
  for pid in $(lsof -ti tcp:"$port" 2>/dev/null || true); do
    cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    if [[ "$cmd" == *"vite"* ]] && [[ "$cmd" == *"$ROOT_DIR/webapp/frontend"* ]]; then
      stop_pid "$pid" "frontend"
    fi
  done
  port=$((port + 1))
done

if [ "$stopped_any" -eq 0 ]; then
  log "No local webapp services found to stop."
fi
