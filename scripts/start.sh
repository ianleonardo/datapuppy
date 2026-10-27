#!/usr/bin/env bash
# Start the SQL Assistant. Usage: start.sh [--foreground]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
export SQLASSIST_CONFIG="${SQLASSIST_CONFIG:-$INSTALL_DIR/config/sqlassist.toml}"
PY="$INSTALL_DIR/.venv/bin/python"
FOREGROUND=false; [[ "${1:-}" == "--foreground" ]] && FOREGROUND=true

[[ -x "$PY" ]] || { echo "ERROR: Python venv not found at $INSTALL_DIR/.venv" >&2; exit 1; }
cd "$INSTALL_DIR/server"

# Resolve paths/host/port from the app's own config loader (honours SQLASSIST_DATA_DIR / SQLASSIST_LOG_DIR)
eval "$("$PY" -m app.cli paths --format=shell)"   # sets DATA_DIR LOG_DIR HOST PORT SCHEME
RUN_DIR="$DATA_DIR/run"; PID_FILE="$RUN_DIR/sqlassist.pid"
mkdir -p "$RUN_DIR" "$LOG_DIR"
chmod 700 "$DATA_DIR" "$RUN_DIR" 2>/dev/null || true

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "SQL Assistant is already running (PID $(cat "$PID_FILE"))."; exit 2
fi
rm -f "$PID_FILE"

# Rotate console logs (keep 5)
for f in stdout stderr; do
  for i in 4 3 2 1; do [[ -f "$LOG_DIR/$f.log.$i" ]] && mv -f "$LOG_DIR/$f.log.$i" "$LOG_DIR/$f.log.$((i+1))"; done
  [[ -f "$LOG_DIR/$f.log" ]] && mv -f "$LOG_DIR/$f.log" "$LOG_DIR/$f.log.1"
done

CMD=("$PY" -m app.serve)   # wraps uvicorn: 1 worker, host/port/ssl from config, writes PID file

if $FOREGROUND; then
  exec "${CMD[@]}"
fi

nohup "${CMD[@]}" >>"$LOG_DIR/stdout.log" 2>>"$LOG_DIR/stderr.log" < /dev/null &
echo $! > "$PID_FILE"

echo -n "Starting SQL Assistant (PID $(cat "$PID_FILE"))"
CURL_OPTS=(-s -o /dev/null -w '%{http_code}' --max-time 2 --noproxy '*')
[[ "$SCHEME" == "https" ]] && CURL_OPTS+=(-k)
for _ in $(seq 1 60); do
  if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo; echo "ERROR: process exited during startup. See $LOG_DIR/stderr.log and $LOG_DIR/app.log" >&2
    rm -f "$PID_FILE"; exit 1
  fi
  if [[ "$(curl "${CURL_OPTS[@]}" "$SCHEME://127.0.0.1:$PORT/healthz" || true)" == "200" ]]; then
    echo; echo "SQL Assistant is up: $SCHEME://$HOST:$PORT  (data: $DATA_DIR, logs: $LOG_DIR)"
    [[ -f "$RUN_DIR/initial-admin-password.txt" ]] && \
      echo "First start: initial admin password is in $RUN_DIR/initial-admin-password.txt"
    exit 0
  fi
  echo -n "."; sleep 1
done
echo; echo "WARNING: not healthy after 60 s; check $LOG_DIR/app.log" >&2; exit 1
