# AI SQL Assistant — container image (linux/amd64).
# Build with scripts/docker-build.sh (or .cmd); it builds the web UI first if asked, then runs:
#   docker build --platform linux/amd64 -f docker/Dockerfile .
#
# Runtime layout inside the container
#   /app/server     application code (+ built web UI in app/static)
#   /app/config     mounted from the host: sqlassist.toml, master.key, TLS cert/key
#   /data           mounted from the host: DuckDB store, uploads, exports, logs, backups …
# Every file baked into the image is readable and writable by any user ID, so the container can run as the
# host user's UID (docker run --user) without hitting read-only files.

ARG BASE_IMAGE=python:3.12-slim-bookworm

# Source stage: the files from the build context. Files created on macOS carry extended attributes
# (com.apple.provenance, com.apple.quarantine …); Docker would store them in the image layer and `docker load`
# then fails with "lsetxattr … operation not supported". Nothing from this stage is copied into the final image
# with COPY; the final stage copies with a plain `cp`, which drops extended attributes.
FROM scratch AS src
COPY server/ /server/
COPY config/sqlassist.example.toml /config/sqlassist.example.toml
COPY docker/entrypoint.sh /entrypoint.sh

FROM --platform=linux/amd64 ${BASE_IMAGE}

# Space-separated optional database drivers: "oracledb", "mariadb" (psycopg for PostgreSQL is always included).
ARG DRIVERS="oracledb"
ARG PIP_INDEX_URL=""
ARG PIP_TRUSTED_HOST=""
ARG BUILD_STAMP=dev

LABEL org.opencontainers.image.title="AI SQL Assistant" \
      org.opencontainers.image.authors="Ian Leonardo" \
      org.sqlassist.build="${BUILD_STAMP}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    SQLASSIST_CONFIG=/app/config/sqlassist.toml \
    SQLASSIST_DATA_DIR=/data \
    SQLASSIST_LOG_DIR=/data/logs \
    SQLASSIST_SECURITY__MASTER_KEY_FILE=/app/config/master.key \
    SQLASSIST_SERVER__HOST=0.0.0.0 \
    SQLASSIST_SERVER__PORT=8080 \
    SQLASSIST_EXTENSIONS_SEED=/opt/sqlassist/duckdb-extensions

# OS libraries: libpq for psycopg, tzdata for TZ=…, MariaDB Connector/C only when that driver is requested.
RUN set -eux; \
    export DEBIAN_FRONTEND=noninteractive; \
    apt-get update; \
    pkgs="ca-certificates tzdata libpq5"; \
    case " $DRIVERS " in *" mariadb "*) pkgs="$pkgs libmariadb3 libmariadb-dev gcc";; esac; \
    apt-get install -y --no-install-recommends $pkgs; \
    rm -rf /var/lib/apt/lists/*

RUN --mount=type=bind,from=src,source=/server/requirements.lock,target=/src/requirements.lock \
    set -eux; \
    PY=$(command -v python3.12 || command -v python3); \
    "$PY" -c 'import sys; assert sys.version_info[:2] == (3, 12), sys.version'; \
    "$PY" -m venv /opt/venv; \
    if [ -n "$PIP_INDEX_URL" ]; then export PIP_INDEX_URL; fi; \
    if [ -n "$PIP_TRUSTED_HOST" ]; then export PIP_TRUSTED_HOST; fi; \
    pip install --upgrade pip; \
    pip install -r /src/requirements.lock; \
    extra=""; \
    case " $DRIVERS " in *" oracledb "*) extra="$extra oracledb>=2.4";; esac; \
    case " $DRIVERS " in *" mariadb "*) extra="$extra mariadb>=1.1";; esac; \
    if [ -n "$extra" ]; then pip install $extra; fi; \
    case " $DRIVERS " in *" mariadb "*) apt-get purge -y --auto-remove gcc libmariadb-dev || true;; esac; \
    true

# DuckDB fts / vss extensions for offline use (DD-14). Seeded into /data/extensions on first start.
# Best effort: without network access to extensions.duckdb.org the app still runs (keyword/vector fallbacks).
RUN mkdir -p /opt/sqlassist/duckdb-extensions && \
    python3 -c "import duckdb; c = duckdb.connect(); c.execute(\"SET extension_directory = '/opt/sqlassist/duckdb-extensions'\"); [c.execute('INSTALL ' + e) for e in ('fts', 'vss')]; print('duckdb extensions bundled')" \
    || echo "WARNING: DuckDB fts/vss extensions could not be downloaded; the app will use its fallbacks."

RUN --mount=type=bind,from=src,target=/src \
    set -eux; \
    mkdir -p /app/server /app/config.defaults; \
    cp -r /src/server/. /app/server/; \
    cp /src/config/sqlassist.example.toml /app/config.defaults/sqlassist.example.toml; \
    cp /src/entrypoint.sh /usr/local/bin/sqlassist-entrypoint

# Permissions: everything the app may touch is read/write for every user ID (rwX keeps dirs/executables
# traversable); the virtualenv is read/execute for everyone.
RUN set -eux; \
    rm -rf /app/server/tests /app/server/.pytest_cache; \
    find /app -name '__pycache__' -type d -prune -exec rm -rf {} +; \
    mkdir -p /app/config /data; \
    chmod -R a+rwX /app /data /opt/sqlassist; \
    chmod -R a+rX /opt/venv; \
    chmod 0755 /usr/local/bin/sqlassist-entrypoint

WORKDIR /app/server
EXPOSE 8080
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD ["python3", "-m", "app.cli", "health"]

ENTRYPOINT ["sqlassist-entrypoint"]
CMD ["python3", "-m", "app.serve"]
