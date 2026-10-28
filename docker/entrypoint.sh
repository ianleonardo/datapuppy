#!/bin/sh
# Container entrypoint: checks the mounted folders, seeds defaults, then runs the server as PID 1's child (docker --init).
set -eu
umask 0002

DATA=${SQLASSIST_DATA_DIR:-/data}
CONF_DIR=$(dirname "${SQLASSIST_CONFIG:-/app/config/sqlassist.toml}")
WHO="uid=$(id -u) gid=$(id -g)"

fail() { echo "ERROR: $*" >&2; exit 1; }

# 1) data folder must be writable by the user the container runs as
mkdir -p "$DATA" 2>/dev/null || true
probe="$DATA/.sqa-write-test.$$"
if ! ( : > "$probe" ) 2>/dev/null; then
  ls -ld "$DATA" >&2 || true
  fail "the data folder mounted at $DATA is not writable by $WHO. Give that user write access to the host folder, or set RUN_AS in docker/docker.conf to its owner (uid:gid)."
fi
rm -f "$probe"

# 2) config folder: needs sqlassist.toml; must be writable on first start (master.key is generated there)
if [ ! -f "${SQLASSIST_CONFIG:-$CONF_DIR/sqlassist.toml}" ]; then
  if ( : > "$CONF_DIR/.sqa-write-test" ) 2>/dev/null; then
    rm -f "$CONF_DIR/.sqa-write-test"
    cp /app/config.defaults/sqlassist.example.toml "$CONF_DIR/sqlassist.toml"
    echo "No sqlassist.toml found: created $CONF_DIR/sqlassist.toml from the example. Review it, then restart." >&2
  else
    fail "no sqlassist.toml in the mounted config folder ($CONF_DIR) and the folder is read-only for $WHO."
  fi
fi
if [ ! -f "${SQLASSIST_SECURITY__MASTER_KEY_FILE:-$CONF_DIR/master.key}" ] && ! ( : > "$CONF_DIR/.sqa-write-test" ) 2>/dev/null; then
  fail "master.key does not exist yet and the config folder ($CONF_DIR) is not writable by $WHO, so it cannot be generated."
fi
rm -f "$CONF_DIR/.sqa-write-test"

# 3) a PID file from a previous run (host install or an old container) means nothing in this PID namespace
rm -f "$DATA/run/sqlassist.pid"

# 4) bundled DuckDB extensions → {data}/extensions (only files that are missing; never overwrites)
SEED=${SQLASSIST_EXTENSIONS_SEED:-}
if [ -n "$SEED" ] && [ -d "$SEED" ] && [ -n "$(ls -A "$SEED" 2>/dev/null)" ]; then
  mkdir -p "$DATA/extensions"
  (cd "$SEED" && find . -type f) | while IFS= read -r f; do
    if [ ! -e "$DATA/extensions/$f" ]; then
      mkdir -p "$(dirname "$DATA/extensions/$f")"
      cp "$SEED/$f" "$DATA/extensions/$f"
    fi
  done
fi

exec "$@"
