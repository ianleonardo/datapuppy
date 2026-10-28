#!/usr/bin/env bash
# Build machine only (TS §24.3 step 2): build the SPA with Vite+, copy it into the server, collect wheels and extensions.
# Usage: scripts/build-release.sh [--platform manylinux2014_x86_64|win_amd64] [--python 3.12] [--glibc 2.28]
#   --platform  the SERVER's platform (pip tag). Without it, wheels and DuckDB extensions are for THIS machine.
#   --glibc     Linux targets: newest glibc the server has (default 2.28 = RHEL/Rocky 8+, Ubuntu 20.04+, Debian 10+).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLATFORM=""; PYVER="3.12"; GLIBC="2.28"
while [[ $# -gt 0 ]]; do case "$1" in --platform) PLATFORM="$2"; shift 2;; --python) PYVER="$2"; shift 2;;
  --glibc) GLIBC="$2"; shift 2;; *) echo "unknown arg $1"; exit 1;; esac; done

# Python for the helper steps: the project's virtualenv if there is one (it has pip, certifi), else python3
if [[ -x "$ROOT/.venv/bin/python" ]]; then PY="$ROOT/.venv/bin/python"
elif [[ -x "$ROOT/.venv/Scripts/python.exe" ]]; then PY="$ROOT/.venv/Scripts/python.exe"
else PY="$(command -v python3 || command -v python)"; fi
[[ -n "$PY" ]] || { echo "ERROR: no Python found (create the venv: python3.12 -m venv .venv)." >&2; exit 1; }
if [[ -z "$PLATFORM" ]]; then
  echo "NOTE: no --platform given: wheels and DuckDB extensions are for this machine ($(uname -sm))."
  echo "      For a Linux server use --platform manylinux2014_x86_64; for Windows, --platform win_amd64."
fi

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
if [[ -n "$PLATFORM" ]]; then
  if [[ "$PLATFORM" == manylinux* ]]; then
    # newer packages ship manylinux_2_26/2_28 wheels: accept every glibc baseline from 2.17 up to --glibc
    ARCH="${PLATFORM##*_}"; [[ "$PLATFORM" == *aarch64 ]] && ARCH="aarch64"; [[ "$PLATFORM" == *x86_64 ]] && ARCH="x86_64"
    WHEEL_ARGS+=(--platform "manylinux2014_$ARCH")
    for minor in $(seq 17 "${GLIBC#2.}"); do WHEEL_ARGS+=(--platform "manylinux_2_${minor}_$ARCH"); done
    echo "     Linux wheels for glibc ${GLIBC} or newer (RHEL/Rocky 8+, Ubuntu 20.04+, Debian 10+ at 2.28)."
  else
    WHEEL_ARGS+=(--platform "$PLATFORM")
  fi
  WHEEL_ARGS+=(--python-version "$PYVER" --only-binary=:all:)
fi
PIP_PY="$PY"
if ! "$PIP_PY" -m pip --version >/dev/null 2>&1; then PIP_PY="$(command -v python3 || command -v python)"; fi
"$PIP_PY" -m pip download "${WHEEL_ARGS[@]}"

echo "==> Bundling DuckDB extensions (fts, vss) for offline use"
if ! "$PY" scripts/bundle_extensions.py --out "$ROOT/extensions" ${PLATFORM:+--platform "$PLATFORM"}; then
  echo "WARN: DuckDB extensions not bundled; the release is still usable (search falls back to slower methods)."
fi
mkdir -p "$ROOT/extensions"

echo "==> Creating archive"
STAMP="$(date +%Y%m%d)"
tar --exclude='server/tests' --exclude='__pycache__' -czf "$ROOT/sqlassist-$STAMP.tar.gz" -C "$ROOT" server scripts config wheels extensions README.md
echo "Release: $ROOT/sqlassist-$STAMP.tar.gz"
