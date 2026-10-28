#!/usr/bin/env bash
# Start the AI SQL Assistant container.
#  1. Looks in IMAGE_DIR for the newest image file (<name>-<stamp>-linux-amd64.tar.gz/.tar). If it is newer than the
#     image Docker has (or Docker has none), loads it.
#  2. If the container was created from an older image, replaces it (data and config are on the host, nothing is lost).
#  3. Mounts DATA_DIR (read-write) at /data and CONFIG_DIR at /app/config, runs as your uid:gid, waits until healthy.
#
# Usage: scripts/docker-start.sh [--data-dir DIR] [--config-dir DIR] [--port N] [--bind ADDR]
#                                [--image-file FILE] [--no-load] [--recreate]
# Settings: docker/docker.conf. Exit codes: 0 OK, 1 failed, 2 already running.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/docker-common.sh"
load_settings

IMAGE_FILE=""; NO_LOAD=0; RECREATE=0; EXPLICIT=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --data-dir) DATA_DIR="$2"; shift 2;;
    --config-dir) CONFIG_DIR="$2"; shift 2;;
    --port) HOST_PORT="$2"; shift 2;;
    --bind) HOST_BIND="$2"; shift 2;;
    --image-file) IMAGE_FILE="$2"; EXPLICIT=1; shift 2;;
    --no-load) NO_LOAD=1; shift;;
    --recreate) RECREATE=1; shift;;
    -h|--help) sed -n '2,11p' "$0"; exit 0;;
    *) echo "Unknown option: $1 (see --help)" >&2; exit 1;;
  esac
done
need_docker
IMAGE="$IMAGE_NAME:latest"

# ---- 1) newer image file? ------------------------------------------------------------------------------------------
if [[ $NO_LOAD -eq 0 ]]; then
  [[ -n "$IMAGE_FILE" ]] || IMAGE_FILE="$(newest_image_file "$(abspath "$IMAGE_DIR")")"
  CUR_STAMP="$(image_stamp "$IMAGE")"; [[ "$CUR_STAMP" == "<no value>" ]] && CUR_STAMP=""
  if [[ -n "$IMAGE_FILE" ]]; then
    [[ -f "$IMAGE_FILE" ]] || { echo "ERROR: image file not found: $IMAGE_FILE" >&2; exit 1; }
    FILE_STAMP="$(file_stamp "$IMAGE_FILE")"
    # the newest file loads only if it's newer; a file named with --image-file always loads (e.g. to roll back)
    if [[ $EXPLICIT -eq 1 || -z "$CUR_STAMP" || "$FILE_STAMP" > "$CUR_STAMP" ]]; then
      echo "==> Loading image file $(basename "$IMAGE_FILE") (build $FILE_STAMP; Docker has ${CUR_STAMP:-none})"
      docker load -i "$IMAGE_FILE"
      # make sure :latest points at what we just loaded (files from other machines may carry only the stamp tag)
      # point :latest at what was just loaded (an older file's :latest tag doesn't move it back by itself)
      if [[ "$(image_stamp "$IMAGE")" != "$FILE_STAMP" ]] && docker image inspect "$IMAGE_NAME:$FILE_STAMP" >/dev/null 2>&1; then
        docker tag "$IMAGE_NAME:$FILE_STAMP" "$IMAGE"
      fi
    else
      echo "Image is up to date (build $CUR_STAMP; newest file is $FILE_STAMP)."
    fi
  fi
fi
docker image inspect "$IMAGE" >/dev/null 2>&1 || {
  echo "ERROR: no $IMAGE image in Docker and no image file in $IMAGE_DIR. Run scripts/docker-build.sh first," >&2
  echo "       or copy an image file ($IMAGE_NAME-<stamp>-linux-amd64.tar.gz) into $IMAGE_DIR." >&2
  exit 1; }
IMG_ID="$(image_id "$IMAGE")"
echo "Using $IMAGE (build $(image_stamp "$IMAGE"))"

# ---- 2) host folders ----------------------------------------------------------------------------------------------
DATA_ABS="$(abspath "$DATA_DIR")"; CONFIG_ABS="$(abspath "$CONFIG_DIR")"
[[ -w "$DATA_ABS" ]] || { echo "ERROR: data folder $DATA_ABS is not writable by $(id -un)." >&2; exit 1; }
if [[ ! -f "$CONFIG_ABS/sqlassist.toml" ]]; then
  if [[ -f "$ROOT/config/sqlassist.example.toml" ]]; then
    cp "$ROOT/config/sqlassist.example.toml" "$CONFIG_ABS/sqlassist.toml"
    echo "NOTE: created $CONFIG_ABS/sqlassist.toml from the example. Review it (LLM endpoints, TLS / allow_insecure_http)."
  fi
fi

USER_ARGS=()
case "$RUN_AS" in
  auto) if [[ "$(id -u)" != "0" ]]; then USER_ARGS=(--user "$(id -u):$(id -g)"); fi;;
  root|"") ;;
  *) USER_ARGS=(--user "$RUN_AS");;
esac

# ---- 3) container --------------------------------------------------------------------------------------------------
if docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  C_IMG="$(docker container inspect -f '{{.Image}}' "$CONTAINER_NAME")"
  RUNNING="$(docker container inspect -f '{{.State.Running}}' "$CONTAINER_NAME")"
  if [[ "$C_IMG" == "$IMG_ID" && $RECREATE -eq 0 ]]; then
    if [[ "$RUNNING" == "true" ]]; then
      echo "SQL Assistant container '$CONTAINER_NAME' is already running."; exit 2
    fi
    echo "==> Starting existing container $CONTAINER_NAME"
    docker start "$CONTAINER_NAME" >/dev/null
  else
    [[ $RECREATE -eq 1 ]] && echo "==> Re-creating container $CONTAINER_NAME" \
                          || echo "==> Image changed: replacing container $CONTAINER_NAME (data and config stay on the host)"
    [[ "$RUNNING" == "true" ]] && docker stop -t 40 "$CONTAINER_NAME" >/dev/null
    docker rm "$CONTAINER_NAME" >/dev/null
  fi
fi
if ! docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  echo "==> Creating container $CONTAINER_NAME"
  RUN=(docker run -d --name "$CONTAINER_NAME" --init --restart unless-stopped --platform linux/amd64
       --stop-timeout 40 -p "$HOST_BIND:$HOST_PORT:8080"
       -v "$DATA_ABS:/data" -v "$CONFIG_ABS:/app/config"
       --label "org.sqlassist.role=app")
  if [[ ${#USER_ARGS[@]} -gt 0 ]]; then RUN+=("${USER_ARGS[@]}"); fi
  if [[ -n "$TZ" ]]; then RUN+=(-e "TZ=$TZ"); fi
  RUN+=("$IMAGE")
  "${RUN[@]}" >/dev/null
fi

# ---- 4) wait for /healthz ------------------------------------------------------------------------------------------
printf 'Waiting for SQL Assistant'
for _ in $(seq 1 90); do
  STATE="$(docker container inspect -f '{{.State.Status}} {{.RestartCount}}' "$CONTAINER_NAME")"
  if [[ "${STATE%% *}" != "running" || "${STATE##* }" != "0" ]]; then
    echo; echo "ERROR: the container stopped during startup. Last log lines:" >&2
    docker logs --tail 25 "$CONTAINER_NAME" >&2 || true
    docker update --restart no "$CONTAINER_NAME" >/dev/null 2>&1 || true   # stop the restart loop
    docker stop -t 5 "$CONTAINER_NAME" >/dev/null 2>&1 || true
    exit 1
  fi
  if URL="$(docker exec "$CONTAINER_NAME" python3 -m app.cli health --print-url 2>/dev/null)"; then
    SCHEME="${URL%%://*}"
    SHOW_HOST="$HOST_BIND"; [[ "$SHOW_HOST" == "0.0.0.0" ]] && SHOW_HOST="$(hostname)"
    echo; echo "SQL Assistant is up: $SCHEME://$SHOW_HOST:$HOST_PORT  (data: $DATA_ABS, config: $CONFIG_ABS)"
    [[ -f "$DATA_ABS/run/initial-admin-password.txt" ]] && echo "First start: initial admin password is in $DATA_ABS/run/initial-admin-password.txt"
    exit 0
  fi
  printf '.'; sleep 1
done
echo; echo "WARNING: not healthy after 90 s. Check: docker logs $CONTAINER_NAME" >&2
exit 1
