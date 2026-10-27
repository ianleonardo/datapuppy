"""Query execution & safeguards pipeline, result handles, profiling and Excel export (TS §12, §14, §11.8)."""
from __future__ import annotations

import asyncio
import datetime as dt
import decimal
import json
import logging
import math
import secrets
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio

from ..core.config import get_config
from ..core.errors import ApiError
from ..permissions import has_connection_access
from ..store.duck import AppStore
from ..store.repos import RequestContext, RunRepo
from . import audit
from .connectors import BaseAdapter, registry
from .safeguards import Validated, count_sql, page_sql, probe_sql, referenced_tables, sql_hash, validate
from .settings import settings

log = logging.getLogger("sqlassist.exec")
EXCEL_MAX = 1_048_575

_connector_limiter: anyio.CapacityLimiter | None = None


def connector_limiter() -> anyio.CapacityLimiter:
    global _connector_limiter
    if _connector_limiter is None:
        _connector_limiter = anyio.CapacityLimiter(int(get_config().get("connector.max_threads", 32)))
    return _connector_limiter


async def in_pool(fn, *args):
    return await anyio.to_thread.run_sync(lambda: fn(*args), limiter=connector_limiter())


# ----------------------------------------------------------------------------- handles (in-memory LRU, TS §12.4)
@dataclass
class Handle:
    run_id: str
    owner: str
    connection_id: str
    engine: str
    validated: Validated
    columns: list[dict[str, str]]
    total: int | None
    method: str
    last_used: float = field(default_factory=time.time)


class HandleCache:
    def __init__(self, max_items: int = 2000):
        self._d: OrderedDict[str, Handle] = OrderedDict()
        self._lock = threading.Lock()
        self.max_items = max_items

    def put(self, h: Handle) -> None:
        with self._lock:
            self._d[h.run_id] = h
            self._d.move_to_end(h.run_id)
            while len(self._d) > self.max_items:
                self._d.popitem(last=False)

    def get(self, run_id: str, owner: str) -> Handle:
        ttl = int(settings.get("results.handle_ttl_min", 60)) * 60
        with self._lock:
            h = self._d.get(run_id)
            if h is None or h.owner != owner:
                raise ApiError("E-EXE-040")
            if time.time() - h.last_used > ttl:
                self._d.pop(run_id, None)
                raise ApiError("E-EXE-040")
            h.last_used = time.time()
            self._d.move_to_end(run_id)
            return h


handles = HandleCache()


# ----------------------------------------------------------------------------- helpers
async def load_connection(store: AppStore, ctx: RequestContext, connection_id: str) -> dict[str, Any]:
    row = await store.fetchone("SELECT * FROM connections WHERE id = ?", [connection_id])
    if not row or not has_connection_access(ctx, row):
        raise ApiError("E-AUTH-003")
    row["params"] = json.loads(row["params"]) if isinstance(row["params"], str) else row["params"]
    return row


def limits_for(conn: dict[str, Any]) -> tuple[int, int]:
    ov = conn.get("limits_override")
    ov = json.loads(ov) if isinstance(ov, str) else (ov or {})
    return (int(ov.get("max_rows") or settings.get("safeguard.max_rows", 1000)),
            int(ov.get("max_cols") or settings.get("safeguard.max_cols", 50)))


def jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else str(v)
    if isinstance(v, decimal.Decimal):
        return float(v) if abs(v) < 2**53 else str(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(v))} bytes>"
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    return str(v)


async def size_intercept(adapter: BaseAdapter, v: Validated) -> tuple[int | None, str, list[dict[str, str]]]:
    """Columns from a zero-row probe; rows via COUNT(*) → EXPLAIN → UNKNOWN (DD-09)."""
    cols = await in_pool(adapter.describe, probe_sql(v), float(settings.get("safeguard.statement_timeout_s", 120)))
    columns = [{"name": c.name, "type": c.type} for c in cols]
    try:
        n = await in_pool(adapter.count, count_sql(v), float(settings.get("safeguard.count_timeout_s", 5)))
        return n, "COUNT", columns
    except ApiError as e:
        if e.code != "E-EXE-030":
            raise
    est = await in_pool(adapter.explain_estimate, v.sql)
    if est is not None:
        return est, "EXPLAIN", columns
    return None, "UNKNOWN", columns


def _tiebreak(columns: list[dict[str, str]]) -> list[str]:
    return [c["name"] for c in columns[:5]]


def parse_sort(sort: str | None, columns: list[dict[str, str]]) -> list[tuple[str, str]]:
    names = {c["name"] for c in columns}
    out = []
    for part in (sort or "").split(","):
        if not part.strip():
            continue
        col, _, d = part.rpartition(":") if ":" in part else (part, ":", "asc")
        col = col.strip()
        if col not in names:  # validated against the result's column list
            raise ApiError("E-VAL-001", detail=f"Unknown sort column {col}")
        out.append((col, "desc" if d.strip().lower() == "desc" else "asc"))
    return out


async def fetch_page(adapter: BaseAdapter, h: Handle, page: int, page_size: int, sort: str | None) -> dict[str, Any]:
    allowed = settings.get("grid.page_size_options", [25, 50, 100, 200])
    if page_size not in allowed:
        page_size = int(settings.get("grid.page_size_default", 50))
    page = max(1, page)
    sql = page_sql(h.validated, parse_sort(sort, h.columns), page_size, (page - 1) * page_size, _tiebreak(h.columns))
    p = await in_pool(adapter.fetch, sql, float(settings.get("safeguard.statement_timeout_s", 120)), h.run_id)
    total_pages = math.ceil(h.total / page_size) if h.total is not None else None
    return {"run_id": h.run_id, "columns": h.columns, "rows": [[jsonable(v) for v in r] for r in p.rows],
            "page": page, "page_size": page_size, "total_rows": h.total, "row_count_method": h.method,
            "total_pages": total_pages, "has_more": len(p.rows) == page_size, "live_data": True}


# ----------------------------------------------------------------------------- execute (TS §12.1)
async def execute(store: AppStore, ctx: RequestContext, *, connection_id: str, sql: str, origin: str,
                  message_id: str | None, session_id: str | None, confirmed_by_user: bool,
                  confirmed_large: bool, run_id: str | None = None) -> dict[str, Any]:
    conn = await load_connection(store, ctx, connection_id)
    runs = RunRepo(store, ctx)
    h = sql_hash(sql)
    base_ctx = {"connection_id": connection_id, "connection": conn["name"], "sql": sql, "sql_hash": h, "origin": origin}

    async def audit_rec(status: str, **extra):
        await audit.record(store, action="QUERY_EXECUTION", status=status, user_ref=ctx.user_ref, user_id=ctx.user_id,
                           context={**base_ctx, **extra})

    try:
        v = validate(sql, conn["engine"])
    except ApiError as e:
        rid = await runs.create(session_id=session_id, message_id=message_id, connection_id=connection_id, origin=origin,
                                sql_text=sql, sql_hash=h, status="REJECTED", error_code=e.code, error_detail=e.message,
                                started_at=dt.datetime.now(dt.timezone.utc))
        await audit_rec("FAILED", error=e.code, run_id=rid)
        raise
    if origin in ("AI", "REFINED") and not confirmed_by_user:
        raise ApiError("E-EXE-010")  # DD-08: AI SQL needs an explicit Execute click

    adapter = registry.get(conn)
    rid = run_id or str(uuid.uuid4())
    started = time.monotonic()
    now = dt.datetime.now(dt.timezone.utc)
    max_rows, max_cols = limits_for(conn)
    try:
        total, method, columns = await size_intercept(adapter, v)
    except ApiError as e:
        await runs.create(id=rid, session_id=session_id, message_id=message_id, connection_id=connection_id, origin=origin,
                          sql_text=sql, sql_hash=h, status="FAILED", error_code=e.code, error_detail=e.message, started_at=now)
        await audit_rec("FAILED", error=e.code, detail=e.message, run_id=rid)
        raise
    big = total is None or total > max_rows or len(columns) > max_cols
    if big and not confirmed_large:
        await runs.create(id=rid, session_id=session_id, message_id=message_id, connection_id=connection_id, origin=origin,
                          sql_text=sql, sql_hash=h, status="INTERCEPTED", row_count=total, row_count_method=method,
                          column_count=len(columns), columns=columns, started_at=now)
        await audit_rec("INTERCEPTED", rows=total, cols=len(columns), row_count_method=method, run_id=rid)
        raise ApiError("E-EXE-020", rows=total, cols=len(columns), row_count_method=method,
                       thresholds={"max_rows": max_rows, "max_cols": max_cols}, run_id=rid)

    handle = Handle(rid, ctx.user_ref, connection_id, conn["engine"], v, columns, total, method)
    handles.put(handle)
    try:
        page1 = await fetch_page(adapter, handle, 1, int(settings.get("grid.page_size_default", 50)), None)
    except ApiError as e:
        await _upsert_run(runs, rid, session_id, message_id, connection_id, origin, sql, h, "FAILED", now,
                          error_code=e.code, error_detail=e.message)
        await audit_rec("FAILED", error=e.code, detail=e.message, run_id=rid)
        raise
    dur = int((time.monotonic() - started) * 1000)
    await _upsert_run(runs, rid, session_id, message_id, connection_id, origin, sql, h, "SUCCEEDED", now,
                      row_count=total, row_count_method=method, column_count=len(columns), columns=columns,
                      confirmed_large=confirmed_large, finished_at=dt.datetime.now(dt.timezone.utc), duration_ms=dur)
    await audit_rec("SUCCESS", rows=total, row_count_method=method, duration_ms=dur, run_id=rid,
                    confirmed_large=confirmed_large)
    page1.update({"tables": referenced_tables(v), "duration_ms": dur, "engine": conn["engine"]})
    return page1


async def _upsert_run(runs: RunRepo, rid, session_id, message_id, connection_id, origin, sql, h, status, started, **f):
    try:
        await runs.get(rid)
        await runs.update(rid, status=status, **f)
    except ApiError:
        await runs.create(id=rid, session_id=session_id, message_id=message_id, connection_id=connection_id, origin=origin,
                          sql_text=sql, sql_hash=h, status=status, started_at=started, **f)


async def rerun_handle(store: AppStore, ctx: RequestContext, run_id: str) -> Handle:
    """Re-create a handle after expiry/restart from the stored run (UI 'Re-run')."""
    run = await RunRepo(store, ctx).get(run_id)
    conn = await load_connection(store, ctx, str(run["connection_id"]))
    v = validate(run["sql_text"], conn["engine"])
    h = Handle(run_id, ctx.user_ref, str(conn["id"]), conn["engine"], v, run["columns"] or [], run["row_count"],
               run["row_count_method"] or "UNKNOWN")
    handles.put(h)
    return h


# ----------------------------------------------------------------------------- profiling (TS §11.8)
def profile_rows(columns: list[dict[str, str]], data: list[list[Any]]) -> dict[str, Any]:
    """Compute an exact profile in-process with DuckDB."""
    import duckdb

    con = duckdb.connect(":memory:")
    try:
        prof: dict[str, Any] = {"rows_profiled": len(data), "columns": []}
        if not data:
            return prof
        safe = [f"c{i}" for i in range(len(columns))]
        cols_data = {s: [jsonable(r[i]) for r in data] for i, s in enumerate(safe)}
        import pyarrow as pa  # noqa: F401  (optional fast path)
        tbl = pa.table(cols_data)
        con.register("t", tbl)
        for i, c in enumerate(columns):
            s = safe[i]
            typ = con.execute(f"SELECT typeof({s}) FROM t WHERE {s} IS NOT NULL LIMIT 1").fetchone()
            base = con.execute(f"SELECT count(*), count({s}), count(DISTINCT {s}) FROM t").fetchone()
            entry: dict[str, Any] = {"name": c["name"], "type": c["type"], "null_pct": round(100 * (1 - base[1] / base[0]), 1),
                                     "distinct": base[2]}
            t = (typ[0] if typ else "").upper()
            if any(k in t for k in ("INT", "DOUBLE", "DECIMAL", "FLOAT", "BIGINT")):
                st = con.execute(f"SELECT min({s}), max({s}), avg({s}), median({s}), stddev_samp({s}), sum({s}) FROM t").fetchone()
                entry.update({"min": st[0], "max": st[1], "mean": _r(st[2]), "median": _r(st[3]), "stddev": _r(st[4]),
                              "sum": _r(st[5])})
            else:
                top = con.execute(f"SELECT {s}::VARCHAR AS v, count(*) AS n FROM t WHERE {s} IS NOT NULL GROUP BY 1 "
                                  f"ORDER BY 2 DESC LIMIT 5").fetchall()
                entry["top_values"] = [{"value": str(v)[:60], "count": n} for v, n in top]
                if "DATE" in c["type"].upper() or "TIME" in c["type"].upper():
                    mm = con.execute(f"SELECT min({s}), max({s}) FROM t").fetchone()
                    entry.update({"min": mm[0], "max": mm[1]})
            prof["columns"].append(entry)
        return prof
    except Exception:  # noqa: BLE001 — no pyarrow / mixed types → pure-Python profile
        return _profile_python(columns, data)
    finally:
        con.close()


def _profile_python(columns, data):
    prof = {"rows_profiled": len(data), "columns": []}
    for i, c in enumerate(columns):
        vals = [r[i] for r in data]
        nn = [v for v in vals if v is not None]
        e: dict[str, Any] = {"name": c["name"], "type": c["type"], "null_pct": round(100 * (1 - len(nn) / max(1, len(vals))), 1),
                             "distinct": len({str(v) for v in nn})}
        nums = [float(v) for v in nn if isinstance(v, (int, float, decimal.Decimal)) and not isinstance(v, bool)]
        if nums and len(nums) == len(nn):
            s = sorted(nums)
            e.update({"min": s[0], "max": s[-1], "mean": _r(sum(s) / len(s)), "median": _r(s[len(s) // 2]), "sum": _r(sum(s))})
        else:
            counts: dict[str, int] = {}
            for v in nn:
                counts[str(v)[:60]] = counts.get(str(v)[:60], 0) + 1
            e["top_values"] = [{"value": k, "count": n} for k, n in sorted(counts.items(), key=lambda x: -x[1])[:5]]
        prof["columns"].append(e)
    return prof


def _r(x):
    return round(float(x), 4) if x is not None else None


async def sensitive_columns(store: AppStore, connection_id: str) -> set[str]:
    rows_ = await store.fetchall(
        "SELECT DISTINCT lower(c.column_name) AS n FROM schema_columns c JOIN schema_tables t ON t.id = c.table_id "
        "JOIN schema_snapshots s ON s.id = t.snapshot_id WHERE s.connection_id = ? AND s.state = 'LOCKED' AND "
        "(c.sensitivity IN ('SENSITIVE','RESTRICTED') OR c.is_masked)", [connection_id])
    return {r["n"] for r in rows_}


async def interpretation_inputs(store: AppStore, ctx: RequestContext, run_id: str) -> dict[str, Any]:
    """Profile ≤ N rows + redacted sample (DD-04)."""
    try:
        h = handles.get(run_id, ctx.user_ref)
    except ApiError:
        h = await rerun_handle(store, ctx, run_id)
    conn = await load_connection(store, ctx, h.connection_id)
    adapter = registry.get(conn)
    max_rows = int(settings.get("ai.interpret_max_rows", 1000))
    sql = page_sql(h.validated, [], max_rows, 0, [])
    page = await in_pool(adapter.fetch, sql, float(settings.get("safeguard.statement_timeout_s", 120)))
    columns = [{"name": c.name, "type": c.type} for c in page.columns] or h.columns
    profile = await asyncio.to_thread(profile_rows, columns, page.rows)
    sens = await sensitive_columns(store, h.connection_id)
    from .schema import column_sensitivity
    redact_idx = {i for i, c in enumerate(columns)
                  if c["name"].lower() in sens or column_sensitivity(c["name"]) in ("SENSITIVE", "RESTRICTED")}
    for i in redact_idx:  # profile values of sensitive columns are redacted too
        col = profile["columns"][i] if i < len(profile["columns"]) else None
        if col:
            for k in ("top_values", "min", "max"):
                col.pop(k, None)
            col["redacted"] = True
    sample: list[list[Any]] = []
    if conn.get("allow_result_values_to_llm"):
        n = int(settings.get("ai.interpret_sample_rows", 20))
        sample = [["<redacted>" if i in redact_idx else jsonable(v) for i, v in enumerate(r)] for r in page.rows[:n]]
    return {"sql": h.validated.sql, "profile": jsonable(profile), "sample": sample, "columns": [c["name"] for c in columns],
            "total": h.total, "method": h.method, "connection_id": h.connection_id}


# ----------------------------------------------------------------------------- Excel export (TS §14.3)
_download_tokens: dict[str, tuple[str, str, float, str]] = {}  # token → (path, filename, expiry, owner)
_dl_lock = threading.Lock()


def issue_download(path: Path, filename: str, owner: str) -> str:
    token = secrets.token_urlsafe(24)
    with _dl_lock:
        _download_tokens[token] = (str(path), filename, time.time() + 15 * 60, owner)
    return token


def redeem_download(token: str, owner: str) -> tuple[str, str]:
    with _dl_lock:
        item = _download_tokens.pop(token, None)  # one-time
    if not item or item[2] < time.time() or item[3] != owner:
        raise ApiError("E-NF-001")
    return item[0], item[1]


def build_xlsx(adapter: BaseAdapter, h: Handle, out: Path, meta: dict[str, Any], progress=None) -> int:
    import xlsxwriter

    sens = meta.get("sensitive_cols") or []
    wb = xlsxwriter.Workbook(str(out), {"constant_memory": True, "default_date_format": "yyyy-mm-dd hh:mm:ss",
                                        "remove_timezone": True, "strings_to_formulas": False, "strings_to_urls": False})
    ws = wb.add_worksheet("Data")
    head = wb.add_format({"bold": True, "bg_color": "#FFE7C2", "border": 1})
    ws.freeze_panes(1, 0)
    n = 0
    cols: list[str] = [c["name"] for c in h.columns]
    for i, c in enumerate(cols):
        ws.write(0, i, c, head)
        ws.set_column(i, i, min(40, max(10, len(c) + 2)))
    for _, batch in adapter.stream(page_sql(h.validated, [], EXCEL_MAX + 1, 0, []), 5000):
        for row in batch:
            n += 1
            if n > EXCEL_MAX:
                wb.close()
                out.unlink(missing_ok=True)
                raise ApiError("E-EXP-002")
            for j, v in enumerate(row):
                if isinstance(v, (dt.datetime, dt.date)):
                    ws.write_datetime(n, j, v.replace(tzinfo=None) if isinstance(v, dt.datetime) else
                                      dt.datetime(v.year, v.month, v.day))
                elif isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
                    ws.write_number(n, j, v)
                elif isinstance(v, decimal.Decimal):
                    ws.write_number(n, j, float(v))
                elif v is None:
                    continue
                else:
                    ws.write_string(n, j, str(jsonable(v))[:32000])
        if progress:
            progress({"rows": n})
    if cols:
        ws.autofilter(0, 0, max(n, 1), len(cols) - 1)
    q = wb.add_worksheet("Query")
    bold = wb.add_format({"bold": True})
    wrap = wb.add_format({"text_wrap": True, "valign": "top", "font_name": "Consolas"})
    q.set_column(0, 0, 18)
    q.set_column(1, 1, 110)
    for i, (k, v) in enumerate([("SQL", h.validated.sql), ("Connection", meta.get("connection")),
                                ("Exported at (UTC)", dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
                                ("User", meta.get("user")), ("Row count", n),
                                ("Sensitivity note", ("Contains SENSITIVE/RESTRICTED columns: " + ", ".join(sens) +
                                                      ". Handle per data policy.") if sens else
                                 "No columns tagged sensitive in the governed schema.")]):
        q.write(i, 0, k, bold)
        q.write(i, 1, str(v), wrap if k == "SQL" else None)
    wb.close()
    return n


def export_path(run_id: str) -> Path:
    d = get_config().data_dir / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{run_id}-{secrets.token_hex(4)}.xlsx"


def cleanup_exports() -> int:
    ttl = int(settings.get("export.file_ttl_h", 24)) * 3600
    n = 0
    for p in (get_config().data_dir / "exports").glob("*.xlsx"):
        if time.time() - p.stat().st_mtime > ttl:
            p.unlink(missing_ok=True)
            n += 1
    return n
