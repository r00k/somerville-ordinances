#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load project-local env vars when present.
if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
RELOAD="${RELOAD:-true}"

is_port_available() {
  local host="$1"
  local port="$2"
  python3 - "$host" "$port" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    sock.bind((host, port))
except OSError:
    sys.exit(1)
finally:
    sock.close()
sys.exit(0)
PY
}

if ! [[ "$PORT" =~ ^[0-9]+$ ]]; then
  echo "Invalid PORT value: $PORT" >&2
  exit 1
fi

ORIGINAL_PORT="$PORT"
SEARCH_LIMIT=100
PORT_FOUND=""
for ((offset = 0; offset < SEARCH_LIMIT; offset++)); do
  CANDIDATE_PORT=$((ORIGINAL_PORT + offset))
  if is_port_available "$HOST" "$CANDIDATE_PORT"; then
    PORT="$CANDIDATE_PORT"
    PORT_FOUND="yes"
    break
  fi
done

if [[ -z "$PORT_FOUND" ]]; then
  echo "Could not find an open port in range ${ORIGINAL_PORT}-$((ORIGINAL_PORT + SEARCH_LIMIT - 1))." >&2
  exit 1
fi

if [[ "$PORT" != "$ORIGINAL_PORT" ]]; then
  echo "Port ${ORIGINAL_PORT} is in use; using ${PORT} instead."
fi

UVICORN_ARGS=(app.api:app --host "$HOST" --port "$PORT")
if [[ "$RELOAD" == "true" ]]; then
  UVICORN_ARGS+=(--reload)
fi

echo "Starting Somerville Law Assistant on http://${HOST}:${PORT}"
echo "Two-pass LLM mode"

exec python3 -m uvicorn "${UVICORN_ARGS[@]}"
