"""In-process job runner + scheduler (TS §6.4, DD-12).

Jobs are persisted in the `jobs` table so they survive restarts. A supervisor polls every second (and is
woken immediately on enqueue), claims jobs FIFO with per-type concurrency limits and runs them.
"""
from __future__ import annotations

import asyncio
import json
import logging
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from ..core.errors import ApiError
from ..store.duck import AppStore

log = logging.getLogger("sqlassist.jobs")

IDEMPOTENT = {"ingest_document", "crawl_schema", "build_export", "backup", "reindex", "retention", "verify_audit", "cleanup"}
LIMITS = {"crawl_schema": 1, "purge": 1, "ingest_document": 2, "build_export": 2, "backup": 1, "reindex": 1}

TaskFn = Callable[[AppStore, dict[str, Any], Callable[[dict], None]], Awaitable[dict[str, Any] | None]]
TASKS: dict[str, TaskFn] = {}


def task(name: str):
    def deco(fn: TaskFn) -> TaskFn:
        TASKS[name] = fn
        return fn
    return deco


class JobRunner:
    def __init__(self, store: AppStore, max_workers: int = 4, poll_s: float = 1.0):
        self.store = store
        self.max_workers = max_workers
        self.poll_s = poll_s
        self._wake = asyncio.Event()
        self._running: dict[str, asyncio.Task] = {}
        self._types: dict[str, str] = {}
        self._stop = False
        self._supervisor: asyncio.Task | None = None
        self._listeners: dict[str, list[asyncio.Queue]] = {}
        self.heartbeat = datetime.now(timezone.utc)

    # ------------------------------------------------------------------ public
    async def enqueue(self, type_: str, payload: dict[str, Any], created_by: str | None = None,
                      idempotency_key: str | None = None, max_attempts: int = 3) -> str:
        if idempotency_key:
            row = await self.store.fetchone("SELECT id FROM jobs WHERE idempotency_key = ? AND status IN ('QUEUED','RUNNING')",
                                            [idempotency_key])
            if row:
                return str(row["id"])
        jid = str(uuid.uuid4())
        await self.store.execute("INSERT INTO jobs(id, type, payload, created_by, idempotency_key, max_attempts) "
                                 "VALUES (?, ?, ?, ?, ?, ?)", [jid, type_, json.dumps(payload, default=str), created_by,
                                                               idempotency_key, max_attempts])
        self._wake.set()
        return jid

    def recover(self) -> None:
        """RUNNING jobs: re-queue idempotent ones, fail the rest with INTERRUPTED (TS §6.4)."""
        for r in self.store.fetchall_sync("SELECT id, type FROM jobs WHERE status = 'RUNNING'"):
            if r["type"] in IDEMPOTENT:
                self.store.write_sync(lambda c, i=str(r["id"]): c.execute(
                    "UPDATE jobs SET status = 'QUEUED', started_at = NULL WHERE id = ?", [i]))
            else:
                self.store.write_sync(lambda c, i=str(r["id"]): c.execute(
                    "UPDATE jobs SET status = 'FAILED', error = 'INTERRUPTED', finished_at = now() WHERE id = ?", [i]))

    def start(self) -> None:
        self._supervisor = asyncio.create_task(self._loop(), name="job-supervisor")

    async def stop(self, timeout: float = 20) -> None:
        self._stop = True
        self._wake.set()
        if self._running:
            done, pending = await asyncio.wait(list(self._running.values()), timeout=timeout)
            for t in pending:
                t.cancel()
        if self._supervisor:
            self._supervisor.cancel()

    def subscribe(self, job_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._listeners.setdefault(job_id, []).append(q)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue) -> None:
        if job_id in self._listeners and q in self._listeners[job_id]:
            self._listeners[job_id].remove(q)

    @property
    def alive(self) -> bool:
        return self._supervisor is not None and not self._supervisor.done()

    def queue_depth(self) -> int:
        return self.store.fetchall_sync("SELECT count(*) AS n FROM jobs WHERE status = 'QUEUED'")[0]["n"]

    # ------------------------------------------------------------------ internals
    def _publish(self, job_id: str, event: dict) -> None:
        for q in self._listeners.get(job_id, []):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def _loop(self) -> None:
        while not self._stop:
            self.heartbeat = datetime.now(timezone.utc)
            try:
                await self._dispatch()
            except Exception:  # noqa: BLE001
                log.exception("job dispatch failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_s)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def _dispatch(self) -> None:
        free = self.max_workers - len(self._running)
        if free <= 0:
            return
        queued = await self.store.fetchall("SELECT id, type, payload, attempts, max_attempts FROM jobs WHERE status = "
                                           "'QUEUED' ORDER BY created_at LIMIT 50")
        for job in queued:
            if free <= 0:
                break
            t = job["type"]
            active_same = sum(1 for v in self._types.values() if v == t)
            if active_same >= LIMITS.get(t, self.max_workers):
                continue
            payload = json.loads(job["payload"]) if isinstance(job["payload"], str) else job["payload"]
            if t == "crawl_schema" and any(self._types.get(k) == t for k in self._running):
                continue
            jid = str(job["id"])
            claimed = await self.store.write(lambda c, jid=jid: c.execute(
                "UPDATE jobs SET status = 'RUNNING', started_at = now(), heartbeat_at = now(), attempts = attempts + 1 "
                "WHERE id = ? AND status = 'QUEUED' RETURNING id", [jid]).fetchall())
            if not claimed:
                continue
            self._types[jid] = t
            self._running[jid] = asyncio.create_task(self._run(jid, t, payload, job["attempts"] + 1, job["max_attempts"]))
            free -= 1

    async def _run(self, jid: str, type_: str, payload: dict, attempt: int, max_attempts: int) -> None:
        fn = TASKS.get(type_)
        loop = asyncio.get_running_loop()

        def progress(p: dict) -> None:
            """Safe to call from the event loop or from worker threads."""
            def on_loop():
                self._publish(jid, {"type": "progress", "progress": p})
            loop.call_soon_threadsafe(on_loop)
            self.store.submit(lambda c: c.execute("UPDATE jobs SET progress = ?, heartbeat_at = now() WHERE id = ?",
                                                  [json.dumps(p, default=str), jid]))
        try:
            if fn is None:
                raise RuntimeError(f"unknown job type {type_}")
            self._publish(jid, {"type": "status", "status": "RUNNING"})
            result = await fn(self.store, payload | {"_job_id": jid}, progress)
            await self.store.execute("UPDATE jobs SET status = 'SUCCEEDED', finished_at = now(), result = ? WHERE id = ?",
                                     [json.dumps(result or {}, default=str), jid])
            self._publish(jid, {"type": "status", "status": "SUCCEEDED", "result": result})
        except asyncio.CancelledError:
            await self.store.execute("UPDATE jobs SET status = 'CANCELLED', finished_at = now() WHERE id = ?", [jid])
            raise
        except Exception as e:  # noqa: BLE001
            msg = e.message if isinstance(e, ApiError) else f"{type(e).__name__}: {e}"
            log.error("job %s (%s) failed: %s", jid, type_, msg, extra={"extra": {"tb": traceback.format_exc()[-2000:]}})
            retry = attempt < max_attempts and type_ in IDEMPOTENT and not isinstance(e, ApiError)
            await self.store.execute("UPDATE jobs SET status = ?, error = ?, finished_at = CASE WHEN ? THEN NULL ELSE now() "
                                     "END WHERE id = ?", ["QUEUED" if retry else "FAILED", msg[:1000], retry, jid])
            self._publish(jid, {"type": "status", "status": "QUEUED" if retry else "FAILED", "error": msg[:300]})
        finally:
            self._running.pop(jid, None)
            self._types.pop(jid, None)
            self._wake.set()


_runner: JobRunner | None = None


def set_runner(r: JobRunner | None) -> None:
    global _runner
    _runner = r


def runner() -> JobRunner:
    if _runner is None:
        raise ApiError("E-SYS-001")
    return _runner


# ----------------------------------------------------------------------------- cron (5-field, minimal)
def cron_matches(expr: str, now: datetime) -> bool:
    fields = expr.split()
    if len(fields) != 5:
        return False
    vals = [now.minute, now.hour, now.day, now.month, (now.isoweekday() % 7)]
    for f, v in zip(fields, vals):
        if not _field_match(f, v):
            return False
    return True


def _field_match(f: str, v: int) -> bool:
    for part in f.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/")
            step = int(s)
        if part == "*":
            if v % step == 0:
                return True
            continue
        if "-" in part:
            a, b = map(int, part.split("-"))
            if a <= v <= b and (v - a) % step == 0:
                return True
        elif int(part) == v:
            return True
    return False
