#!/usr/bin/env bash
# Stop the SQL Assistant gracefully (falls back to SIGTERM, then SIGKILL).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
export SQLASSIST_CONFIG="${SQLASSIST_CONFIG:-$INSTALL_DIR/config/sqlassist.toml}"
PY="$INSTALL_DIR/.venv/bin/python"
cd "$INSTALL_DIR/server"
eval "$("$PY" -m app.cli paths --format=shell)"
RUN_DIR="$DATA_DIR/run"; PID_FILE="$RUN_DIR/sqlassist.pid"; TOKEN_FILE="$RUN_DIR/shutdown.token"

if [[ ! -f "$PID_FILE" ]] || ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "SQL Assistant is not running."; rm -f "$PID_FILE"; exit 2
fi
PID="$(cat "$PID_FILE")"

# 1) Graceful: shutdown endpoint (checkpoints DuckDB, drains jobs)
if [[ -f "$TOKEN_FILE" ]]; then
  CURL_OPTS=(-s -o /dev/null --max-time 5 --noproxy '*' -X POST -H "X-Shutdown-Token: $(cat "$TOKEN_FILE")")
  [[ "$SCHEME" == "https" ]] && CURL_OPTS+=(-k)
  curl "${CURL_OPTS[@]}" "$SCHEME://127.0.0.1:$PORT/internal/shutdown" || true
else
  kill -TERM "$PID" 2>/dev/null || true      # 2) SIGTERM also triggers graceful shutdown
fi

echo -n "Stopping SQL Assistant (PID $PID)"
for _ in $(seq 1 30); do
  if ! kill -0 "$PID" 2>/dev/null; then echo; echo "Stopped."; rm -f "$PID_FILE"; exit 0; fi
  echo -n "."; sleep 1
done

kill -TERM "$PID" 2>/dev/null || true; sleep 5
if kill -0 "$PID" 2>/dev/null; then
  echo; echo "WARNING: forcing stop (SIGKILL). DuckDB will replay its WAL on next start." >&2
  kill -KILL "$PID" 2>/dev/null || true
fi
rm -f "$PID_FILE"; echo "Stopped."; exit 0
