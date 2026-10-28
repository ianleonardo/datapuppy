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

Follow TS §24. In short: `scripts/build-release.sh` on a build machine with `vp` installed (web build, wheels, DuckDB
`fts`/`vss` extensions), extract on the host, `pip install --no-index --find-links wheels -r server/requirements.lock`
(+ `requirements-drivers.txt` as needed), copy extensions to `{data_dir}/extensions`, configure
`config/sqlassist.toml`, `scripts/start.sh` / `scripts\start.cmd`. Use `scripts/sqlassist.service`
(systemd) on Linux. On Windows, register `scripts\start.cmd --foreground` with Task Scheduler ("At startup") or NSSM, and use `scripts\stop.cmd` to stop. CLI: `python -m app.cli start|stop|status|paths|create-admin|rotate-master-key|backup|verify-audit|doctor|seed-demo`.

The master key is generated at `config/master.key` (outside `data_dir`). Back it up separately from data backups.

## Docker (linux/amd64)

Scripts: `scripts/docker-build|docker-start|docker-stop` as `.sh` (Linux/macOS) and `.cmd` (Windows, plain batch).
Settings live in `docker/docker.conf`; copy `docker/docker.example.conf` to start. Environment variables with the
same names override the file, and command-line options override both.

```bash
scripts/docker-build.sh                 # builds sqlassist:<stamp> + :latest for linux/amd64, saves docker/images/sqlassist-<stamp>-linux-amd64.tar.gz
scripts/docker-build.sh --rebuild-web   # run `vp build` first (otherwise the UI already in server/app/static is used)
scripts/docker-start.sh                 # loads a newer image file if there is one, (re)creates the container, waits until healthy
scripts/docker-start.sh --data-dir /srv/sqlassist/data --port 8443 --bind 0.0.0.0
scripts/docker-stop.sh                  # graceful stop (DuckDB checkpoint); --remove also deletes the container
```

- **Image files and updates.** Each build saves an image file named with its build stamp (UTC). The stamp is
  also stored in the image as a label. To update another machine, copy the new file into its `docker/images` folder
  and run the start script. It loads the file only when the file's stamp is newer than the image Docker already has.
  It then replaces the container, because the container was made from the older image. Data and config stay on the host.
- **Data and config.** `DATA_DIR` (default `./data`) is mounted read-write at `/data`, so the DuckDB store, uploads,
  exports, logs and backups are host files. `CONFIG_DIR` (default `./config`) is mounted at `/app/config` and holds
  `sqlassist.toml`, `master.key` and TLS files. In `sqlassist.toml`, refer to cert/key files as `./config/<file>`.
  Inside the container, the data folder, log folder, key path, host (0.0.0.0) and port (8080) are fixed by
  environment variables. Use `HOST_BIND` / `HOST_PORT` to choose where it is published.
- **Permissions.** Every file baked into the image is read/write for all users (`chmod -R a+rwX`), so the image runs
  under any user ID. On Linux/macOS the start script runs the container as your own `uid:gid` (`RUN_AS=auto`). Files it
  writes into `DATA_DIR` therefore stay owned by you and remain readable and writable from the host. Set
  `RUN_AS=uid:gid` to use another owner. On startup the entrypoint checks that `/data` is writable and says which
  user lacks access if it isn't.
- **Offline extras.** The build bundles DuckDB `fts`/`vss` (when extensions.duckdb.org is reachable). They are copied
  into `{data}/extensions` on first start. Optional drivers: `DRIVERS=oracledb mariadb`.
- **Proxies / private mirrors.** `HTTP(S)_PROXY` from your shell are passed to the build. Use `PIP_INDEX_URL`
  for an internal PyPI, and `BASE_IMAGE` if Docker Hub is not reachable (any Debian/Ubuntu image with Python 3.12
  and `python3 -m venv`).
- Don't run the Docker container and a host install (`scripts/start.sh`) on the same data folder at the same time.
  DuckDB allows only one writer.

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
