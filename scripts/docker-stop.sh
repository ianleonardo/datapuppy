#!/usr/bin/env bash
# Stop the AI SQL Assistant container gracefully (SIGTERM → DuckDB checkpoint, jobs drained; forced after 40 s).
# Usage: scripts/docker-stop.sh [--remove]     --remove also deletes the container (data and config stay on the host)
# Exit codes: 0 stopped, 1 failed, 2 not running.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/docker-common.sh"
load_settings
REMOVE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --remove) REMOVE=1; shift;;
    -h|--help) sed -n '2,4p' "$0"; exit 0;;
    *) echo "Unknown option: $1 (see --help)" >&2; exit 1;;
  esac
done
need_docker

if ! docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  echo "SQL Assistant container '$CONTAINER_NAME' does not exist."; exit 2
fi
if [[ "$(docker container inspect -f '{{.State.Running}}' "$CONTAINER_NAME")" == "true" ]]; then
  echo "Stopping SQL Assistant container '$CONTAINER_NAME' (up to 40 s)..."
  docker stop -t 40 "$CONTAINER_NAME" >/dev/null
  CODE="$(docker container inspect -f '{{.State.ExitCode}}' "$CONTAINER_NAME")"
  if [[ "$CODE" == "137" ]]; then echo "WARNING: had to force-stop; DuckDB will replay its WAL on next start." >&2; fi
  echo "Stopped."
  RC=0
else
  echo "SQL Assistant container '$CONTAINER_NAME' is not running."
  RC=2
fi
if [[ $REMOVE -eq 1 ]]; then docker rm "$CONTAINER_NAME" >/dev/null && echo "Container removed."; fi
exit $RC
