# Shared helpers for docker-build.sh / docker-start.sh / docker-stop.sh (sourced, not run).
# shellcheck shell=bash

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# KEY=VALUE settings: environment wins over docker/docker.conf, which wins over the defaults below.
load_settings() {
  local conf="${SQA_DOCKER_CONF:-$ROOT/docker/docker.conf}" line key val
  if [[ -f "$conf" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      line="${line%$'\r'}"
      [[ -z "${line//[[:space:]]/}" || "$line" =~ ^[[:space:]]*# ]] && continue
      [[ "$line" == *=* ]] || continue
      key="${line%%=*}"; val="${line#*=}"
      key="${key//[[:space:]]/}"
      val="${val#"${val%%[![:space:]]*}"}"; val="${val%"${val##*[![:space:]]}"}"
      [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]] || continue
      if [[ -z "${!key+x}" ]]; then export "$key=$val"; fi
    done < "$conf"
  fi
  : "${IMAGE_NAME:=sqlassist}" "${CONTAINER_NAME:=sqlassist}" "${IMAGE_DIR:=docker/images}" "${KEEP_IMAGES:=3}"
  : "${DATA_DIR:=data}" "${CONFIG_DIR:=config}" "${HOST_BIND:=127.0.0.1}" "${HOST_PORT:=8080}" "${RUN_AS:=auto}"
  : "${TZ:=}" "${BASE_IMAGE:=python:3.12-slim-bookworm}" "${DRIVERS:=oracledb}" "${PIP_INDEX_URL:=}" "${BUILD_NETWORK:=}"
}

abspath() {  # absolute path; relative paths are taken from the project folder
  local p="$1"
  [[ "$p" = /* ]] || p="$ROOT/$p"
  mkdir -p "$p" 2>/dev/null || true
  (cd "$p" 2>/dev/null && pwd) || echo "$p"
}

need_docker() {
  command -v docker >/dev/null 2>&1 || { echo "ERROR: docker is not installed or not on PATH." >&2; exit 1; }
  docker info >/dev/null 2>&1 || { echo "ERROR: cannot reach the Docker daemon. Start Docker (Docker Desktop / systemctl start docker) and try again." >&2; exit 1; }
}

image_id() { docker image inspect -f '{{.Id}}' "$1" 2>/dev/null || true; }

# Image files are named <IMAGE_NAME>-<STAMP>-linux-amd64.tar.gz (or .tar); STAMP = build time YYYYMMDD-HHMMSS (UTC).
# The same stamp is stored in the image as label org.sqlassist.build, so "is this file newer?" is a string compare
# that works on any Docker installation (image IDs differ between the classic and containerd image stores).
newest_image_file() {  # the file with the highest stamp
  local dir="$1"
  ls -1 "$dir"/"$IMAGE_NAME"-*-linux-amd64.tar.gz "$dir"/"$IMAGE_NAME"-*-linux-amd64.tar 2>/dev/null \
    | awk -F/ '{print $NF "\t" $0}' | sort -r | head -n 1 | cut -f2 || true
}
file_stamp() {
  local b; b="$(basename "$1")"; b="${b#"$IMAGE_NAME"-}"; echo "${b%%-linux-amd64.tar*}"
}
image_stamp() { docker image inspect -f '{{index .Config.Labels "org.sqlassist.build"}}' "$1" 2>/dev/null || true; }
