"""App factory & lifespan (TS §6.5)."""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from .core.config import Config, get_config, set_config
from .core.crypto import init_keyring
from .core.errors import ApiError, api_error_handler
from .core.logging import configure_logging, request_id_var, user_id_var
from .core.paths import ensure_tree, write_private

log = logging.getLogger("sqlassist")
access = logging.getLogger("sqlassist.access")
STARTED_AT = datetime.now(timezone.utc)
STATIC_DIR = Path(__file__).resolve().parent / "static"
SENSITIVE_ROUTES = ("/api/v1/auth", "/api/v1/downloads", "/internal")

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "connect-src 'self'; worker-src 'self' blob:; frame-src 'self'; font-src 'self' data:; frame-ancestors 'none'")


class State:
    ready = False
    shutting_down = False
    shutdown_event: asyncio.Event | None = None


def startup(cfg: Config):
    """Steps 1–9 of TS §6.5 (synchronous part). Returns (store, runner)."""
    from .auth.service import ensure_bootstrap_admin
    from .jobs import tasks  # noqa: F401  (register tasks)
    from .jobs.runner import JobRunner, set_runner
    from .services import audit
    from .services.llm import seed_providers
    from .services.purge import compact_if_requested
    from .services.settings import settings
    from .store.duck import AppStore, set_store
    from .store.migrations import apply_migrations, pending

    ensure_tree(cfg)
    configure_logging(cfg)
    init_keyring(cfg.master_key_file)
    audit.init_mirror(cfg.log_dir)
    if cfg.db_path.exists() and compact_if_requested(cfg.db_path):
        log.warning("database compacted at startup")
    store = AppStore(cfg.db_path, temp_dir=cfg.data_dir / "tmp", extension_dir=cfg.data_dir / "extensions",
                     memory_limit=str(cfg.get("duckdb.memory_limit")), threads=int(cfg.get("duckdb.threads")),
                     read_cursors=int(cfg.get("store.read_cursors", 8)))
    store.load_extensions()
    if cfg.db_path.exists() and pending(store) and store.fetchall_sync(
            "SELECT count(*) AS n FROM schema_migrations")[0]["n"]:
        from .jobs.tasks import do_backup  # automatic pre-migration backup (TS §24.6)
        try:
            set_store(store)
            do_backup(store)
        except Exception:  # noqa: BLE001
            log.exception("pre-migration backup failed")
    apply_migrations(store, int(cfg.get("rag.embed_dim", 1024)))
    set_store(store)
    settings.load(store)
    seed_providers(store, cfg)
    ensure_bootstrap_admin(store, cfg)
    runner = JobRunner(store, int(cfg.get("jobs.max_workers", 4)), float(cfg.get("jobs.poll_interval_s", 1.0)))
    runner.recover()
    set_runner(runner)
    return store, runner


async def _scheduler(store, runner) -> None:
    """Lightweight cron: nightly backup, retention, audit verification, cleanup; session write-behind."""
    from .auth.service import flush_last_seen
    from .jobs.runner import cron_matches
    from .services.settings import settings

    last_minute = None
    while True:
        await asyncio.sleep(5)
        try:
            await flush_last_seen(store)
        except Exception:  # noqa: BLE001
            log.exception("session flush failed")
        now = datetime.now()
        minute = now.replace(second=0, microsecond=0)
        if minute == last_minute:
            continue
        last_minute = minute
        schedule = {
            "backup": settings.get("duckdb.backup_cron", "0 2 * * *"),
            "retention": "30 2 * * *",
            "verify_audit": "45 2 * * *",
            "cleanup": "15 * * * *",
        }
        for job, cron in schedule.items():
            if cron and cron_matches(cron, now):
                await runner.enqueue(job, {}, None, idempotency_key=f"sched:{job}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_config()
    store, runner = startup(cfg)
    run_dir = cfg.data_dir / "run"
    write_private(run_dir / "shutdown.token", secrets.token_urlsafe(32))
    State.shutdown_event = asyncio.Event()
    runner.start()
    sched = asyncio.create_task(_scheduler(store, runner))
    State.ready = True
    log.info("SQL Assistant started", extra={"extra": {"data_dir": str(cfg.data_dir), "log_dir": str(cfg.log_dir)}})
    try:
        yield
    finally:
        # graceful shutdown: stop accepting, cancel target queries, checkpoint jobs, drain writer, CHECKPOINT, close
        State.shutting_down = True
        State.ready = False
        sched.cancel()
        from .services.connectors import cleanup_wallet_temp, registry
        registry.close_all()
        await runner.stop(timeout=20)
        await asyncio.to_thread(store.close)
        from .store.duck import set_store
        set_store(None)
        cleanup_wallet_temp()
        (run_dir / "shutdown.token").unlink(missing_ok=True)
        pid_file = run_dir / "sqlassist.pid"  # uvicorn re-raises SIGTERM after shutdown, so clean up here
        try:
            if pid_file.exists() and pid_file.read_text().strip() == str(os.getpid()):
                pid_file.unlink()
        except OSError:
            pass
        log.info("SQL Assistant stopped cleanly")


def create_app(cfg: Config | None = None) -> FastAPI:
    if cfg is not None:
        set_config(cfg)
    app = FastAPI(title="AI SQL Assistant", version="0.2.0", lifespan=lifespan, docs_url="/api/docs",
                  openapi_url="/api/openapi.json", redoc_url=None)
    app.add_exception_handler(ApiError, api_error_handler)

    @app.exception_handler(RequestValidationError)
    async def _val(_: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(x) for x in first.get("loc", []) if x != "body")
        return JSONResponse({"code": "E-VAL-001", "title": f"{loc}: {first.get('msg', 'invalid input')}", "status": 422},
                            status_code=422, media_type="application/problem+json")

    @app.middleware("http")
    async def mw(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        request.state.request_id = rid
        request_id_var.set(rid)
        user_id_var.set(None)
        if State.shutting_down and not request.url.path.startswith("/healthz"):
            return JSONResponse({"code": "E-SYS-001", "title": "The server is starting up or shutting down.", "status": 503},
                                status_code=503)
        t0 = time.monotonic()
        try:
            resp: Response = await call_next(request)
        except Exception:  # noqa: BLE001
            log.exception("unhandled error")
            resp = JSONResponse({"code": "E-SYS-500", "title": "Unexpected server error.", "status": 500}, status_code=500)
        resp.headers["X-Request-ID"] = rid
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        if "X-Allow-Same-Origin-Frame" in resp.headers:  # document viewer (PDF) only
            del resp.headers["X-Allow-Same-Origin-Frame"]
            resp.headers["X-Frame-Options"] = "SAMEORIGIN"
            resp.headers["Content-Security-Policy"] = CSP.replace("frame-ancestors 'none'", "frame-ancestors 'self'")
        else:
            resp.headers["X-Frame-Options"] = "DENY"
        if request.url.scheme == "https":
            resp.headers["Strict-Transport-Security"] = "max-age=31536000"
        path = request.url.path
        access.info("request", extra={"extra": {
            "method": request.method, "path": path,
            "query": None if path.startswith(SENSITIVE_ROUTES) else (str(request.url.query) or None),
            "status": resp.status_code, "latency_ms": int((time.monotonic() - t0) * 1000),
            "user_id": getattr(getattr(request.state, "ctx", None), "user_id", None),
            "ip": request.client.host if request.client else None}})
        return resp

    from .api.v1 import admin, auth, chat, queries, rag_schema
    for r in (auth.router, chat.router, queries.router, rag_schema.router, admin.router):
        app.include_router(r, prefix="/api/v1")

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz():
        from .jobs.runner import runner
        try:
            ok = State.ready and runner().alive
        except ApiError:
            ok = False
        return JSONResponse({"ready": ok}, status_code=200 if ok else 503)

    @app.post("/internal/shutdown", include_in_schema=False)
    async def internal_shutdown(request: Request):
        host = request.client.host if request.client else ""
        if host not in ("127.0.0.1", "::1", "localhost"):
            return PlainTextResponse("forbidden", status_code=403)
        token_file = get_config().data_dir / "run" / "shutdown.token"
        sent = request.headers.get("X-Shutdown-Token", "")
        if not token_file.exists() or not secrets.compare_digest(sent, token_file.read_text().strip()):
            return PlainTextResponse("forbidden", status_code=403)
        log.warning("graceful shutdown requested via /internal/shutdown")
        if State.shutdown_event:
            State.shutdown_event.set()
        return {"ok": True}

    @app.get("/metrics", include_in_schema=False)
    async def metrics():
        from .services.settings import settings
        from .store.duck import store
        if not settings.get("metrics.enabled"):
            return PlainTextResponse("disabled", status_code=404)
        s = store()
        q = s.fetchall_sync("SELECT status, count(*) AS n FROM query_runs GROUP BY status")
        lines = [f'query_exec_total{{status="{r["status"]}"}} {r["n"]}' for r in q]
        lines.append(f"app_store_bytes {s.file_size()}")
        return PlainTextResponse("\n".join(lines) + "\n")

    # ---- SPA (TR-UI-06): static assets + fallback to index.html
    if (STATIC_DIR / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            if path.startswith(("api/", "internal/")):
                return JSONResponse({"code": "E-NF-001", "title": "Not found.", "status": 404}, status_code=404)
            f = (STATIC_DIR / path).resolve()
            if path and f.is_file() and str(f).startswith(str(STATIC_DIR.resolve())):
                return FileResponse(f)
            return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})
    else:
        @app.get("/", include_in_schema=False)
        async def no_ui():
            return PlainTextResponse("SQL Assistant API is running. The web UI is not built — run `vp build` in web/ and "
                                     "copy web/dist/* to server/app/static/ (see README).")
    return app


app = create_app() if os.environ.get("SQLASSIST_IMPORT_APP") else None
