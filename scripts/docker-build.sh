#!/usr/bin/env bash
# Build the AI SQL Assistant Docker image for linux/amd64 and save it as an image file in IMAGE_DIR.
#
# Usage: scripts/docker-build.sh [--rebuild-web] [--no-save] [--tag STAMP] [--drivers "oracledb mariadb"]
#                                [--base-image IMAGE] [--pip-index-url URL] [--no-cache]
#   --rebuild-web   run "vp build" in web/ first and copy the result to server/app/static
#                   (otherwise the web UI already in server/app/static is used)
#   --no-save       only build into Docker; don't write the image file
# Settings: docker/docker.conf (see docker/docker.example.conf). Exit codes: 0 OK, 1 failed.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/docker-common.sh"
load_settings

REBUILD_WEB=0; SAVE=1; NO_CACHE=(); STAMP="$(date -u +%Y%m%d-%H%M%S)"  # UTC, so builds from any machine order correctly
while [[ $# -gt 0 ]]; do
  case "$1" in
    --rebuild-web) REBUILD_WEB=1; shift;;
    --no-save) SAVE=0; shift;;
    --no-cache) NO_CACHE=(--no-cache); shift;;
    --tag) STAMP="$2"; shift 2;;
    --drivers) DRIVERS="$2"; shift 2;;
    --base-image) BASE_IMAGE="$2"; shift 2;;
    --pip-index-url) PIP_INDEX_URL="$2"; shift 2;;
    -h|--help) sed -n '2,9p' "$0"; exit 0;;
    *) echo "Unknown option: $1 (see --help)" >&2; exit 1;;
  esac
done
need_docker

if [[ $REBUILD_WEB -eq 1 ]]; then
  echo "==> Building the web UI with Vite+ (vp)"
  command -v vp >/dev/null 2>&1 || { echo "ERROR: 'vp' is not on PATH. Install: curl -fsSL https://vite.plus | bash" >&2; exit 1; }
  (cd "$ROOT/web" && vp install && vp build)
  rm -rf "$ROOT/server/app/static" && mkdir -p "$ROOT/server/app/static"
  cp -R "$ROOT/web/dist/." "$ROOT/server/app/static/"
fi
[[ -f "$ROOT/server/app/static/index.html" ]] || {
  echo "ERROR: server/app/static/index.html is missing. Run with --rebuild-web (needs vp) first." >&2; exit 1; }

# macOS tags files with extended attributes (com.apple.provenance …); clear them so they never reach the image.
if [[ "$(uname -s)" == "Darwin" ]] && command -v xattr >/dev/null 2>&1; then
  xattr -cr "$ROOT/server" "$ROOT/config" "$ROOT/docker" 2>/dev/null || true
fi

echo "==> Building image $IMAGE_NAME:$STAMP for linux/amd64"
ARGS=(--platform linux/amd64 -f "$ROOT/docker/Dockerfile"
      -t "$IMAGE_NAME:$STAMP" -t "$IMAGE_NAME:latest"
      --build-arg "BUILD_STAMP=$STAMP" --build-arg "BASE_IMAGE=$BASE_IMAGE" --build-arg "DRIVERS=$DRIVERS")
if [[ -n "$BUILD_NETWORK" ]]; then ARGS+=(--network "$BUILD_NETWORK"); fi
if [[ -n "$PIP_INDEX_URL" ]]; then ARGS+=(--build-arg "PIP_INDEX_URL=$PIP_INDEX_URL"); fi
for v in HTTP_PROXY HTTPS_PROXY NO_PROXY http_proxy https_proxy no_proxy; do
  if [[ -n "${!v:-}" ]]; then ARGS+=(--build-arg "$v=${!v}"); fi
done
if docker buildx version >/dev/null 2>&1; then
  docker buildx build --load ${NO_CACHE[@]+"${NO_CACHE[@]}"} "${ARGS[@]}" "$ROOT"
else
  docker build ${NO_CACHE[@]+"${NO_CACHE[@]}"} "${ARGS[@]}" "$ROOT"
fi

ARCH="$(docker image inspect -f '{{.Os}}/{{.Architecture}}' "$IMAGE_NAME:$STAMP")"
[[ "$ARCH" == "linux/amd64" ]] || { echo "ERROR: built image is $ARCH, expected linux/amd64." >&2; exit 1; }
echo "Built $IMAGE_NAME:$STAMP ($ARCH), also tagged $IMAGE_NAME:latest"

if [[ $SAVE -eq 1 ]]; then
  OUT_DIR="$(abspath "$IMAGE_DIR")"
  OUT="$OUT_DIR/$IMAGE_NAME-$STAMP-linux-amd64.tar.gz"
  echo "==> Saving image file $OUT"
  docker save "$IMAGE_NAME:$STAMP" "$IMAGE_NAME:latest" | gzip -c > "$OUT.part"
  mv "$OUT.part" "$OUT"
  chmod a+r "$OUT"
  echo "Saved $(du -h "$OUT" | cut -f1)  $OUT"
  # keep the newest KEEP_IMAGES files
  ls -1 "$OUT_DIR"/"$IMAGE_NAME"-*-linux-amd64.tar.gz "$OUT_DIR"/"$IMAGE_NAME"-*-linux-amd64.tar 2>/dev/null \
    | awk -F/ '{print $NF "\t" $0}' | sort -r | cut -f2 | tail -n +"$((KEEP_IMAGES + 1))" \
    | while IFS= read -r f; do echo "Removing old image file $(basename "$f")"; rm -f "$f"; done || true
fi
echo "Done. Start it with scripts/docker-start.sh"
