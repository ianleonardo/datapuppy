# AI SQL Assistant (QueryPup · Tiangou · DataBark)

An AI-assisted SQL workbench built from `specs/Technical Specification_ AI SQL Assistant.md` (v0.2):
natural-language → SQL with an Execute/Refine gate, read-only safeguards, size interception, paginated
results, Excel export, RAG references, schema governance, an audit trail, and a puppy mascot whose
brand and personality change with the time of day.

| Time (local) | Theme | Brand & mascot | Login animation |
| --- | --- | --- | --- |
| 05:00–11:59 | `morning` | **QueryPup**, a golden pup with a red bandana | Sunrise over hills, drifting clouds, a trail of paw prints trotting across; the pup wakes up and stretches |
| 12:00–17:59 | `afternoon` | **Tiangou**, the mythic heavenly dog with a rainbow mane, horns and bell | Sketch-style auspicious (xiangyun) clouds, twinkling sparkles, falling paw confetti, and the sun being nibbled (the Tiangou eclipse legend) while Tiangou floats |
| 18:00–04:59 | `night` | **DataBark**, a beagle on a neon-dark UI | Star field, a neon constellation that draws a dog's head with data packets running along its edges, shooting stars and drifting SQL keywords; the pup sleeps until you start typing |

On every theme the pup covers its eyes while you type your password. Add `?theme=morning|afternoon|night`
to the login URL, or use the preview chips on the login page, to see each one.

## Repository layout

```
server/            FastAPI app (Python 3.12), DuckDB app store, migrations, tests
  app/core/        config (TOML + env), paths, JSON file logging + redaction, AES-256-GCM keyring, errors
  app/store/       DuckDB writer thread + read cursors, migration runner, user-scoped repositories
  app/auth/        local Argon2id + LDAP (ldap3), sessions, CSRF, lockout, token-bucket rate limits
  app/services/    safeguards (sqlglot), connectors (DuckDB/Postgres/MariaDB/Oracle), schema governance,
                   RAG (extract/chunk/embed/hybrid retrieval), LLM providers (Spirare/Gemini via httpx),
                   orchestrator (intents, prompts, repair loop), execution/pagination/export, audit, purge
  app/jobs/        persisted in-process job runner + cron scheduler
  app/api/v1/      REST + SSE endpoints (TS §20)
  migrations/      0001_init.sql (TS §7.3 DDL + seeds), 0002_embeddings.sql ({embed_dim})
  tests/           pytest: E2E flow, forbidden-SQL corpus, isolation, lint rules, fake LLM gateway
web/               React 18 + TypeScript, built with Vite+ (`vp`)
  src/mascot/      original SVG puppy, 9 moods (idle, thinking/digging, happy, confused, protective,
                   sniffing, sleepy, waking, excited) + debounced mood state machine
  src/features/    auth (login scenes), chat, sql-editor (Monaco), results grid, erd (Mermaid), rag,
                   schema tree, admin (13 sections)
scripts/           start/stop/status for Linux (.sh) and Windows (.cmd, plain batch — no PowerShell), systemd unit, release builder
config/            sqlassist.example.toml
specs/             PRD, FS, TS and the design references
```

## Quick start (development)

Prerequisites: Python 3.12 and the **Vite+ global CLI** (`vp`), which also manages Node.js for you:

```bash
curl -fsSL https://vite.plus | bash          # macOS / Linux
# Windows (no PowerShell needed): download and run vp-setup.exe from https://setup.viteplus.dev
vp help                                      # in a new shell, to confirm
```

```bash
# backend
python3.12 -m venv .venv
.venv/bin/pip install -r server/requirements-dev.txt      # Windows: .venv\Scripts\pip
cp config/sqlassist.example.toml config/sqlassist.toml
# for a local pilot over plain HTTP set  [server] allow_insecure_http = true  and  host = "127.0.0.1"

# web
cd web && vp install && vp build && cd ..
mkdir -p server/app/static && cp -r web/dist/* server/app/static/

scripts/start.sh            # Windows: scripts\start.cmd   (status: scripts\status.cmd)
# → prints the URL and where the one-time admin password is (data/run/initial-admin-password.txt)
scripts/stop.sh             # graceful: checkpoints DuckDB, drains jobs
```

For UI work, run the API with `scripts/start.sh` and the dev server with `cd web && vp dev`
(it proxies `/api` to `127.0.0.1:8080`).

### Try it without a real database or LLM

```bash
cd server
../.venv/bin/python -m app.cli seed-demo                      # writes demo/demo_sales.duckdb
../.venv/bin/python -m tests.fake_llm --port 9911 &           # OpenAI-compatible fake gateway
```

Then in `config/sqlassist.toml` point Spirare at the fake gateway and set a dummy key:

```toml
[llm.spirare]
chat_url  = "http://127.0.0.1:9911/nlp/v1/chat/completions"
embed_url = "http://127.0.0.1:9911/embedding/v1/embeddings"
api_key   = "demo"
```

Sign in as `admin`, change the password, then go to **Administration**:
1. **Connections**: add a DuckDB connection with the `demo_sales.duckdb` path, then click **Test**.
2. **Schema governance**: run **Crawl schema**, hide the tables flagged for review, then **Lock schema**.
3. **Users & roles**: add an analyst and grant them country `SG`.

Ask "Show me recent Singapore orders with their payment amounts", click **Execute**, and try "show me every order" to see the guardian-mode interception.

## Tests

```bash
cd server && ../.venv/bin/python -m pytest -q     # 84 tests
cd web && vp test                                  # 41 tests (WCAG contrast per theme, theme resolver)
```

The backend suite covers the full flow: bootstrap admin → crawl/review/lock → chat → Execute gate →
interception → pagination/sort → Excel export → insights → purge. It also runs the forbidden-statement
corpus across four dialects, cross-tenant 404s, PRIVATE-RAG isolation, lockout, the audit hash chain against
the file mirror, the absence of secrets in logs, RBAC, and lint rules (user-owned tables only via repositories; audit
append-only outside purge).

## Production install

Follow TS §24. In short: `scripts/build-release.sh --platform manylinux2014_x86_64` (Linux server; `win_amd64` for
Windows; without `--platform` the wheels are for the build machine itself) on a build machine with `vp` installed (web
build, wheels for glibc 2.28+ — use `--glibc 2.17` for older servers — and DuckDB `fts`/`vss` extensions), extract on the host, `pip install --no-index --find-links wheels -r server/requirements.lock`
(+ `requirements-drivers.txt` as needed), copy extensions to `{data_dir}/extensions`, configure
`config/sqlassist.toml`, `scripts/start.sh` / `scripts\start.cmd`. Use `scripts/sqlassist.service`
(systemd) on Linux. On Windows, register `scripts\start.cmd --foreground` with Task Scheduler ("At startup") or NSSM, and use `scripts\stop.cmd` to stop. CLI: `python -m app.cli start|stop|status|paths|create-admin|rotate-master-key|backup|verify-audit|doctor|seed-demo`.

The master key is generated at `config/master.key` (outside `data_dir`). Back it up separately from data backups.

## Docker

Runs the whole app (server + web UI) in one Linux x86-64 container. Your data and configuration stay in normal
folders on the host, so you can rebuild, update or remove the container without losing anything.

Every command has a Linux/macOS version (`.sh`, run in Terminal) and a Windows version (`.cmd`, run in Command Prompt
or by double-clicking). Run them from the project folder.

| Step | Linux / macOS | Windows |
|---|---|---|
| Build the image | `scripts/docker-build.sh` | `scripts\docker-build.cmd` |
| Start (loads a newer image file automatically) | `scripts/docker-start.sh` | `scripts\docker-start.cmd` |
| Stop | `scripts/docker-stop.sh` | `scripts\docker-stop.cmd` |

### 1. Before you start

- **Docker:** Docker Desktop on macOS or Windows, Docker Engine on Linux. Check it's running with `docker info`.
- **Apple Silicon Macs:** the image is built for x86-64 and runs under emulation. In Docker Desktop → Settings →
  General, turn on **Use Rosetta for x86_64/amd64 emulation on Apple Silicon** for faster builds and runs.
- **Network for the build:** the build downloads the base image from Docker Hub and Python packages from PyPI. Behind
  a corporate proxy or with internal mirrors, see [Troubleshooting](#docker-troubleshooting).
- **`vp`:** only needed if you rebuild the web UI as part of the build (`--rebuild-web`). Otherwise the already-built
  UI in `server/app/static` is used.

### 2. One-time setup

1. **Docker settings:** copy the example and edit it.

   ```bash
   cp docker/docker.example.conf docker/docker.conf        # Windows: copy docker\docker.example.conf docker\docker.conf
   ```

   The defaults run the app at `http://127.0.0.1:8080`, keep data in `./data` and config in `./config`. All settings
   are listed in [Settings](#docker-settings).

2. **App config:** `config/sqlassist.toml` is the same file a normal install uses. If it doesn't exist, the start
   script creates it from the example. Check these lines:

   ```toml
   [server]
   allow_insecure_http = true     # plain HTTP for a local pilot; otherwise set ssl_certfile / ssl_keyfile below
   ssl_certfile = ""              # with TLS: "./config/server.crt"  (put the files in the config folder)
   ssl_keyfile  = ""              #           "./config/server.key"
   ```

   `host` and `port` in this file are ignored inside the container: it always listens on 8080, and Docker publishes
   that as `HOST_BIND:HOST_PORT` from `docker/docker.conf`. Paths for data, logs and `master.key` are also fixed
   inside the container, so you don't need to change them.

### 3. Build the image

```bash
scripts/docker-build.sh                 # uses the web UI already in server/app/static
scripts/docker-build.sh --rebuild-web   # runs `vp build` first, so the image has your latest web changes
```

The first build takes a few minutes; later builds reuse Docker's cache. When it finishes you have:

- the image `sqlassist:latest` (plus `sqlassist:<stamp>`) in Docker, and
- an image file `docker/images/sqlassist-<stamp>-linux-amd64.tar.gz` (about 280 MB) for copying to other machines.
  The stamp is the build time in UTC, e.g. `20261028-021500`. The newest 3 files are kept (`KEEP_IMAGES`).

Build options:

| Option | What it does |
|---|---|
| `--rebuild-web` | Build the web UI with `vp` first and copy it into the server |
| `--no-save` | Only build into Docker; don't write the image file |
| `--no-cache` | Rebuild every layer (e.g. to pick up new OS security updates) |
| `--drivers "oracledb mariadb"` | Database drivers to include (PostgreSQL and DuckDB are always included) |
| `--base-image IMAGE` | Base image, e.g. one from an internal registry |
| `--pip-index-url URL` | Internal PyPI mirror |
| `--tag STAMP` | Use your own stamp instead of the current time |

### 4. Start

```bash
scripts/docker-start.sh
```

The script:

1. looks in `docker/images` for the newest image file and loads it if it's newer than the image Docker has,
2. creates the container (or replaces it if it was made from an older image),
3. waits until the app answers, then prints its address.

```
Image is up to date (build 20261028-021500; newest file is 20261028-021500).
==> Creating container sqlassist
Waiting for SQL Assistant....
SQL Assistant is up: http://127.0.0.1:8080  (data: /Users/you/intelligent-sql/data, config: …/config)
First start: initial admin password is in /Users/you/intelligent-sql/data/run/initial-admin-password.txt
```

Open the address in your browser. On a brand-new data folder, sign in as `admin` with the password from that file;
you'll be asked to change it.

The container restarts by itself after a reboot or a Docker restart, until you stop it with the stop script.

Start options (they override `docker/docker.conf` for this run):

| Option | What it does |
|---|---|
| `--data-dir DIR` | Use another data folder |
| `--config-dir DIR` | Use another config folder |
| `--port N` / `--bind ADDR` | Publish on another port / address (`0.0.0.0` = reachable from other machines; set up TLS first) |
| `--image-file FILE` | Load this image file instead of the newest one in `docker/images` |
| `--no-load` | Don't look for image files; use the image Docker already has |
| `--recreate` | Re-create the container even if the image hasn't changed (e.g. after changing settings) |

Exit codes: `0` started, `1` failed (the last log lines are shown), `2` already running.

### 5. Stop

```bash
scripts/docker-stop.sh            # graceful: finishes running jobs and saves the database (up to 40 s)
scripts/docker-stop.sh --remove   # also deletes the container; data and config stay on the host
```

### 6. Update, or install on another machine

On the build machine, run `scripts/docker-build.sh`. Then, on the target machine (which only needs Docker and this
project folder, or just the `scripts`, `docker` and `config` folders):

1. Copy the new `docker/images/sqlassist-<stamp>-linux-amd64.tar.gz` into the target's `docker/images` folder.
2. Run `scripts/docker-start.sh`. It sees the newer file, loads it and replaces the running container:

   ```
   ==> Loading image file sqlassist-20261029-010000-linux-amd64.tar.gz (build 20261029-010000; Docker has 20261028-021500)
   ==> Image changed: replacing container sqlassist (data and config stay on the host)
   ```

The newest file is only loaded when it's newer than the image Docker has. To roll back on purpose, name the file:
`scripts/docker-start.sh --image-file docker/images/<older file>`. That always loads it and switches the container to
it. A plain start later moves forward again if a newer file is still in `docker/images`, so delete or move the newer
file, or start with `--no-load`, to stay on the older build.

<a id="docker-settings"></a>
### Settings (`docker/docker.conf`)

One `KEY=VALUE` per line, no quotes, no comments at the end of a line. An environment variable with the same name
wins over the file, and a command-line option wins over both. Relative paths are relative to the project folder.

| Setting | Default | Meaning |
|---|---|---|
| `DATA_DIR` | `data` | Data folder (database, uploads, exports, logs, backups), mounted read-write at `/data` |
| `CONFIG_DIR` | `config` | Folder with `sqlassist.toml`, `master.key` and TLS files, mounted at `/app/config` |
| `HOST_BIND` | `127.0.0.1` | IP address to publish on (not a host name); `127.0.0.1` = this machine only, `0.0.0.0` = all interfaces, so other computers can connect |
| `HOST_PORT` | `8080` | Port to publish on |
| `RUN_AS` | `auto` | `auto` = run as your own user on Linux/macOS (files in the data folder stay yours); `root`; or `uid:gid` |
| `TZ` | `Asia/Singapore` | Time zone in the container (backup schedule, log display) |
| `IMAGE_NAME` / `CONTAINER_NAME` | `sqlassist` | Names in Docker |
| `IMAGE_DIR` | `docker/images` | Where image files are written and looked for |
| `KEEP_IMAGES` | `3` | How many image files the build keeps |
| `BASE_IMAGE` | `python:3.12-slim-bookworm` | Base image (any Debian/Ubuntu image with Python 3.12 and `venv`) |
| `DRIVERS` | `oracledb` | Extra database drivers: `oracledb`, `mariadb` |
| `PIP_INDEX_URL` | | Internal PyPI mirror |
| `BUILD_NETWORK` | | `host` when your proxy runs on this machine (cntlm, px) |

### Everyday commands

```bash
docker logs -f sqlassist          # follow the server output (app logs are also in data/logs/)
docker ps --filter name=sqlassist # is it running, and is it healthy?
scripts/docker-start.sh --recreate   # apply changed docker.conf settings
```

Backups run automatically every night into `data/backups` (`[duckdb] backup_cron` in `sqlassist.toml`). Back up
`config/master.key` separately: without it, the saved database passwords and LLM API keys can't be decrypted.

<a id="docker-troubleshooting"></a>
### Troubleshooting

| Message or symptom | What to do |
|---|---|
| `cannot reach the Docker daemon` | Start Docker Desktop (or `sudo systemctl start docker`) and try again. |
| Build fails pulling `python:3.12-slim-bookworm` | Docker Hub is blocked: set `BASE_IMAGE` to the same image in your internal registry. |
| Build fails at `pip install` | Set `PIP_INDEX_URL` to your internal PyPI. If your proxy runs on 127.0.0.1, also set `BUILD_NETWORK=host`. Your shell's `HTTP(S)_PROXY` are passed to the build automatically. |
| `WARNING: DuckDB fts/vss extensions could not be downloaded` | Not fatal: search uses slower fallbacks. To add them later, run `python3 scripts/bundle_extensions.py --platform linux_amd64 --out data/extensions` on a machine with internet access (copy `data/extensions` across if that's another machine), then restart. |
| `docker load` fails with `lsetxattr com.apple.provenance … operation not supported` | The image file was built from macOS files that carry Apple extended attributes. Rebuild with the current `docker-build.sh` (it clears them and copies files without them), then use the new image file. Delete the old `docker/images/*.tar.gz` that failed. |
| `Refusing plain HTTP` in the startup log | Set `allow_insecure_http = true` in `config/sqlassist.toml`, or configure `ssl_certfile` / `ssl_keyfile`. Then start again. |
| `the data folder mounted at /data is not writable by uid=…` | The folder belongs to another user. Fix the folder's permissions, or set `RUN_AS` to its owner (`ls -ln data` shows the uid:gid). |
| Cannot connect from another computer (telnet fails) | Set `HOST_BIND=0.0.0.0` (or the server's IP from `hostname -I`), not a host name or `127.0.0.1`; run `docker ps` and check `PORTS` shows `0.0.0.0:<port>->8080`; open the port in the server firewall (`sudo firewall-cmd --add-port=<port>/tcp --permanent && sudo firewall-cmd --reload`, or `ufw allow <port>/tcp`). |
| `port is already allocated` | Something else uses the port (e.g. the non-Docker app via `scripts/start.sh`). Stop it, or set `HOST_PORT`. |
| `Could not set lock on file …/sqlassist.duckdb` | The normal install (`scripts/start.sh`) and the container can't use the same data folder at the same time. Stop one of them. |
| Very slow build or app on a Mac | Turn on Rosetta emulation in Docker Desktop (see step 1). |
| The container stopped during startup | The start script prints the last log lines; `docker logs sqlassist` shows everything. |

### How it works

- **Data and config are host folders.** `DATA_DIR` is mounted at `/data` and `CONFIG_DIR` at `/app/config`, so the
  container itself holds no state. Inside it, the data folder, log folder, key file, listen address (`0.0.0.0`) and
  port (`8080`) are fixed with environment variables.
- **Permissions.** Every file in the image is readable and writable by any user ID, so the container can run as your
  own user. Files it creates in the data folder are owned by you, and private files (`master.key`, the admin password
  file) stay owner-only. At startup it checks that `/data` is writable and says which user lacks access if not.
- **Versioned image files.** Each build's stamp is in the file name and stored inside the image, which is how the start
  script knows whether a file is newer than what Docker has, on any Docker installation.
- **Graceful stop.** `docker stop` sends SIGTERM; the app finishes running work and checkpoints DuckDB before exiting.
- **Offline extras.** The build includes the DuckDB `fts`/`vss` extensions when it can download them, and copies them
  into `data/extensions` on first start.

## Where this deviates from the spec

- **Windows scripts are plain batch.** TS Appendix F specifies PowerShell (`start.ps1`/`stop.ps1`). Here `start.cmd`, `stop.cmd` and `status.cmd` are plain batch files that call `python -m app.cli start|stop|status`, which does the same job: PID check, detached start, health polling, graceful shutdown via `/internal/shutdown`, then a forced-stop fallback.

These were deliberate calls. Each can be revisited.

- **BlockNote and Lottie.** The chat composer is a plain textarea, and messages are stored in the BlockNote JSON block shape (`paragraph`, `sqlCard`, `mermaid`, `insight`, `recommendationChips`, …), so the BlockNote editor can be dropped in later without a data migration. The mascot is original inline SVG + CSS instead of Lottie files. It's tiny (no assets), works offline and covers all spec moods plus the extended set in TS §17.1.
- **SQL in chat cards** uses a lightweight highlighter. Monaco is used in the SQL editor panel and diff view.
- **Embeddings unavailable.** If no embedding provider works, schema cards and documents are stored without vectors and retrieval falls back to keyword matching, so a pilot without an embedding endpoint still works. Locking reports `embedded: false`.
- **Reference file types.** References accept PDF, DOCX, Markdown (`.md`) and TXT. CSV, which the spec lists, is not accepted for new uploads; CSV references uploaded earlier still open, re-index and delete normally.
- **DuckDB 1.4.1** is pinned. `fts`/`vss` are not auto-installed (DD-14). Without them, keyword search falls back to ILIKE scoring and vector search is exact.

## Not yet verified against real systems

Verification so far used a DuckDB demo database and the fake OpenAI-compatible gateway in `server/tests/fake_llm.py`. These paths are written to the spec but haven't run against the real systems:

- the Spirare gateway: confirm the auth header and `response_format` support (OQ-01);
- Google Gemini;
- LDAP: the defaults are still placeholders (OQ-12);
- the PostgreSQL, MariaDB and Oracle adapters (the integration containers listed in TS §25);
- the Windows batch scripts on a real Windows machine (the Python start/stop logic they call was tested on Linux).

Each provider and connection has a **Test** button in Administration to check these.
