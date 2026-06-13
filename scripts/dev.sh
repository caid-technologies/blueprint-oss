#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"

backend_pid=""
frontend_pid=""
cleaned_up="false"

log() {
  printf '[blueprint-dev] %s\n' "$*"
}

is_port_open() {
  local port="$1"
  ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]${port}$"
}

wait_for_url() {
  local url="$1"
  local label="$2"
  local attempts="${3:-60}"

  for _ in $(seq 1 "$attempts"); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      log "$label is ready at $url"
      return 0
    fi
    sleep 1
  done

  log "$label did not become ready at $url"
  return 1
}

first_free_port() {
  local start_port="$1"
  local port="$start_port"

  while is_port_open "$port"; do
    port=$((port + 1))
    if [ "$port" -gt $((start_port + 20)) ]; then
      log "No free frontend port found from $start_port to $((start_port + 20))."
      exit 1
    fi
  done

  printf '%s' "$port"
}

cleanup() {
  if [ "$cleaned_up" = "true" ]; then
    return
  fi
  cleaned_up="true"

  log "Stopping services..."
  if [ -n "$frontend_pid" ] && kill -0 "$frontend_pid" >/dev/null 2>&1; then
    kill "$frontend_pid" >/dev/null 2>&1 || true
  fi
  if [ -n "$backend_pid" ] && kill -0 "$backend_pid" >/dev/null 2>&1; then
    kill "$backend_pid" >/dev/null 2>&1 || true
  fi
  if [ -n "$frontend_pid" ] || [ -n "$backend_pid" ]; then
    wait ${frontend_pid:+"$frontend_pid"} ${backend_pid:+"$backend_pid"} >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT INT TERM

cd "$ROOT_DIR"

if [ ! -x "$VENV_DIR/bin/python" ]; then
  log "Creating Python virtualenv at $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

if [ ! -x "$VENV_DIR/bin/uvicorn" ]; then
  log "Installing backend dependencies"
  "$VENV_DIR/bin/pip" install -r "$ROOT_DIR/backend/requirements.txt"
fi

if [ ! -d "$ROOT_DIR/frontend/node_modules" ]; then
  log "Installing frontend dependencies"
  (cd "$ROOT_DIR/frontend" && npm install)
fi

if is_port_open "$BACKEND_PORT"; then
  if curl -fsS "http://$BACKEND_HOST:$BACKEND_PORT/" >/dev/null 2>&1; then
    log "Backend already appears to be running at http://$BACKEND_HOST:$BACKEND_PORT"
  else
    log "Port $BACKEND_PORT is already in use, but Blueprint did not respond there."
    exit 1
  fi
else
  log "Starting backend at http://$BACKEND_HOST:$BACKEND_PORT"
  "$VENV_DIR/bin/uvicorn" backend.main:app --host "$BACKEND_HOST" --port "$BACKEND_PORT" &
  backend_pid="$!"
  wait_for_url "http://$BACKEND_HOST:$BACKEND_PORT/" "Backend"
fi

FRONTEND_PORT="$(first_free_port "$FRONTEND_PORT")"
log "Starting frontend at http://$FRONTEND_HOST:$FRONTEND_PORT"
(cd "$ROOT_DIR/frontend" && npm run dev -- --hostname "$FRONTEND_HOST" --port "$FRONTEND_PORT") &
frontend_pid="$!"

wait_for_url "http://$FRONTEND_HOST:$FRONTEND_PORT/" "Frontend"

cat <<EOF

Blueprint is running:
  Backend:  http://$BACKEND_HOST:$BACKEND_PORT
  Frontend: http://$FRONTEND_HOST:$FRONTEND_PORT

Press Ctrl+C to stop both services.
EOF

wait
