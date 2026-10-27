"""DuckDB application store (TS §6.3, DD-11).

* One dedicated writer thread owns the primary connection. Callers submit write units —
  functions run inside BEGIN … COMMIT — and await futures. This serialises writes and makes
  the audit hash chain trivially serial (TR-STO-01).
* Readers use cursors (`con.cursor()`) from a bounded pool, run in worker threads; they see
  committed data (MVCC).
"""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
from concurrent.futures import Future
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

import anyio
import duckdb

log = logging.getLogger("sqlassist.store")
T = TypeVar("T")

_STOP = object()


class AppStore:
    def __init__(self, db_path: Path, *, temp_dir: Path, extension_dir: Path,
                 memory_limit: str = "4GB", threads: int = 8, read_cursors: int = 8):
        self.db_path = db_path
        self.extension_dir = extension_dir
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(db_path), config={
            "autoinstall_known_extensions": False,
            "autoload_known_extensions": False,
        })
        for stmt in (
            f"SET temp_directory = '{_q(temp_dir)}'",
            f"SET extension_directory = '{_q(extension_dir)}'",
            f"SET memory_limit = '{_q(memory_limit)}'",
            f"SET threads = {int(threads)}",
        ):
            self.con.execute(stmt)
        self.extensions: dict[str, bool] = {}
        self._queue: queue.Queue = queue.Queue()
        self._writer = threading.Thread(target=self._writer_loop, name="duckdb-writer", daemon=True)
        self._read_sem = threading.BoundedSemaphore(read_cursors)
        self._writer.start()

    # ------------------------------------------------------------------ extensions
    def load_extensions(self, names=("fts", "vss")) -> dict[str, bool]:
        for name in names:
            try:
                self.con.execute(f"LOAD {name}")
                self.extensions[name] = True
            except duckdb.Error as exc:  # DD-14: degrade gracefully
                self.extensions[name] = False
                log.warning("DuckDB extension %s unavailable (%s); degrading", name, str(exc).splitlines()[0])
        return self.extensions

    # ------------------------------------------------------------------ writer
    def _writer_loop(self) -> None:
        cur = self.con.cursor()
        for name, ok in list(self.extensions.items()):
            if ok:
                try:
                    cur.execute(f"LOAD {name}")
                except duckdb.Error:
                    pass
        while True:
            item = self._queue.get()
            if item is _STOP:
                break
            fn, fut, transactional = item
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                if transactional:
                    cur.execute("BEGIN TRANSACTION")
                    try:
                        result = fn(cur)
                        cur.execute("COMMIT")
                    except BaseException:
                        try:
                            cur.execute("ROLLBACK")
                        except duckdb.Error:
                            pass
                        raise
                else:
                    result = fn(cur)
                fut.set_result(result)
            except BaseException as exc:  # noqa: BLE001
                fut.set_exception(exc)
        cur.close()

    def submit(self, fn: Callable[[duckdb.DuckDBPyConnection], T], transactional: bool = True) -> "Future[T]":
        fut: Future = Future()
        self._queue.put((fn, fut, transactional))
        return fut

    async def write(self, fn: Callable[[duckdb.DuckDBPyConnection], T], transactional: bool = True) -> T:
        return await asyncio.wrap_future(self.submit(fn, transactional))

    def write_sync(self, fn: Callable[[duckdb.DuckDBPyConnection], T], transactional: bool = True) -> T:
        return self.submit(fn, transactional).result()

    # ------------------------------------------------------------------ readers
    @contextmanager
    def cursor(self) -> Iterator[duckdb.DuckDBPyConnection]:
        with self._read_sem:
            cur = self.con.cursor()
            try:
                for name, ok in self.extensions.items():
                    if ok:
                        try:
                            cur.execute(f"LOAD {name}")
                        except duckdb.Error:
                            pass
                yield cur
            finally:
                cur.close()

    def read_sync(self, fn: Callable[[duckdb.DuckDBPyConnection], T]) -> T:
        with self.cursor() as cur:
            return fn(cur)

    async def read(self, fn: Callable[[duckdb.DuckDBPyConnection], T]) -> T:
        return await anyio.to_thread.run_sync(self.read_sync, fn)

    # helpers ---------------------------------------------------------------
    async def fetchall(self, sql: str, params: list | tuple = ()) -> list[dict[str, Any]]:
        return await self.read(lambda c: rows(c.execute(sql, params)))

    async def fetchone(self, sql: str, params: list | tuple = ()) -> dict[str, Any] | None:
        res = await self.fetchall(sql, params)
        return res[0] if res else None

    def fetchall_sync(self, sql: str, params: list | tuple = ()) -> list[dict[str, Any]]:
        return self.read_sync(lambda c: rows(c.execute(sql, params)))

    async def execute(self, sql: str, params: list | tuple = ()) -> None:
        await self.write(lambda c: c.execute(sql, params))

    # ------------------------------------------------------------------ lifecycle
    def checkpoint(self) -> None:
        self.write_sync(lambda c: c.execute("CHECKPOINT"), transactional=False)

    def close(self) -> None:
        try:
            self.checkpoint()
        except Exception:  # noqa: BLE001
            log.exception("checkpoint on close failed")
        self._queue.put(_STOP)
        self._writer.join(timeout=30)
        self.con.close()

    def file_size(self) -> int:
        total = 0
        for p in (self.db_path, self.db_path.with_suffix(self.db_path.suffix + ".wal")):
            if p.exists():
                total += p.stat().st_size
        return total


def rows(cur: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    if cur.description is None:
        return []
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _q(value: Any) -> str:
    return str(value).replace("'", "''")


_store: AppStore | None = None


def set_store(store: AppStore | None) -> None:
    global _store
    _store = store


def store() -> AppStore:
    if _store is None:
        from ..core.errors import ApiError

        raise ApiError("E-SYS-001")
    return _store
