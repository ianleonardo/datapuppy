#!/usr/bin/env bash
# Build machine only (TS §24.3 step 2): build the SPA with Vite+, copy it into the server, collect wheels and extensions.
# Usage: scripts/build-release.sh [--platform manylinux2014_x86_64|win_amd64] [--python 3.12]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLATFORM=""; PYVER="3.12"
while [[ $# -gt 0 ]]; do case "$1" in --platform) PLATFORM="$2"; shift 2;; --python) PYVER="$2"; shift 2;; *) echo "unknown arg $1"; exit 1;; esac; done

echo "==> Building web UI with the Vite+ global CLI (vp)"
if ! command -v vp >/dev/null 2>&1; then
  echo "ERROR: the Vite+ CLI 'vp' is not on PATH." >&2
  echo "Install it:  curl -fsSL https://vite.plus | bash   (Windows: run vp-setup.exe from https://setup.viteplus.dev), then open a new shell." >&2
  exit 1
fi
cd "$ROOT/web"
vp install
vp check || echo "WARN: vp check reported issues"
vp test
vp build
rm -rf "$ROOT/server/app/static" && mkdir -p "$ROOT/server/app/static"
cp -r dist/* "$ROOT/server/app/static/"

echo "==> Downloading Python wheels"
cd "$ROOT"
WHEEL_ARGS=(-r server/requirements.lock -d wheels)
if [[ -n "$PLATFORM" ]]; then WHEEL_ARGS+=(--platform "$PLATFORM" --python-version "$PYVER" --only-binary=:all:); fi
python3 -m pip download "${WHEEL_ARGS[@]}"

echo "==> Bundling DuckDB extensions (fts, vss) for offline use"
python3 scripts/bundle_extensions.py --out "$ROOT/extensions" ${PLATFORM:+--platform "$PLATFORM"}

echo "==> Creating archive"
STAMP="$(date +%Y%m%d)"
tar --exclude='server/tests' --exclude='__pycache__' -czf "$ROOT/sqlassist-$STAMP.tar.gz" -C "$ROOT" server scripts config wheels extensions README.md
echo "Release: $ROOT/sqlassist-$STAMP.tar.gz"
