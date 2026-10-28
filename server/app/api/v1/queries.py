"""Queries, results, downloads and jobs (TS §20.3)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from ...auth.deps import current_ctx
from ...auth.ratelimit import limit
from ...core.errors import ApiError
from ...jobs.runner import runner
from ...services import audit, schema
from ...services.connectors import registry
from ...services.execution import (EXCEL_MAX, build_xlsx, execute, export_path, fetch_page, handles, in_pool,
                                   issue_download, load_connection, redeem_download, rerun_handle, sensitive_columns)
from ...services.orchestrator import DIALECT_NAMES, SCHEMAS, _llm_json, persona
from ...services.safeguards import pretty, static_preflight, validate
from ...services.settings import settings
from ...store.duck import store
from ...store.repos import RequestContext, RunRepo

router = APIRouter(tags=["queries"])


class PreflightIn(BaseModel):
    connection_id: str
    sql: str = Field(min_length=1, max_length=200_000)
    stage: str = "static"  # static | ai


class ExecuteIn(BaseModel):
    connection_id: str
    sql: str = Field(min_length=1, max_length=200_000)
    origin: str = Field(default="MANUAL", pattern="^(AI|MANUAL|REFINED)$")
    message_id: str | None = None
    session_id: str | None = None
    confirmed_by_user: bool = False
    confirmed_large: bool = False
    run_id: str | None = None


class FormatIn(BaseModel):
    connection_id: str
    sql: str


@router.post("/queries/preflight")
async def preflight(body: PreflightIn, ctx: RequestContext = Depends(current_ctx)):
    s = store()
    conn = await load_connection(s, ctx, body.connection_id)
    cat, excluded = schema.catalog_for_preflight(s, body.connection_id)
    res = static_preflight(body.sql, conn["engine"], cat, excluded)
    res["stage"] = "static"
    if body.stage == "ai":
        limit("preflight", ctx.user_ref, per_min=settings.get("ratelimit.preflight_per_min", 30))
        numbered = "\n".join(f"{i + 1:>3}| {ln}" for i, ln in enumerate(body.sql.splitlines()))
        cards = await s.fetchall("SELECT card_text FROM schema_cards WHERE connection_id = ? LIMIT 20", [body.connection_id])
        msgs = [{"role": "system", "content": persona(ctx) + f"\nReview this {DIALECT_NAMES[conn['engine']]} SQL for structural "
                 "problems: Cartesian joins, non-sargable predicates, GROUP BY mismatches, NOT IN with nullable columns, "
                 "date-boundary bugs. Known static findings: <static_issues>" + json.dumps(res["issues"]) +
                 "</static_issues>\n<schema>" + "\n\n".join(c["card_text"] for c in cards) + "</schema>\nReturn ONLY JSON "
                 "matching: " + json.dumps(SCHEMAS["REVIEW_SQL"])}, {"role": "user", "content": f"<sql>\n{numbered}\n</sql>"}]
        try:
            data, _ = await _llm_json(s, msgs, "REVIEW_SQL")
            for i in data.get("issues") or []:
                if isinstance(i, dict) and i.get("message"):
                    res["issues"].append({"line": int(i.get("line") or 1), "col": int(i.get("col") or 1), "end_col": None,
                                          "severity": i.get("severity", "warning"), "message": i["message"], "code": "AI"})
            cand = (data.get("corrected_sql") or "").strip()
            if cand:
                validate(cand, conn["engine"])
                res["corrected_sql"] = cand  # shown as a diff; never silently applied (TR-PRE-03)
            res["ai_reply"] = data.get("reply")
            res["stage"] = "ai"
        except ApiError as e:
            res["ai_error"] = e.message
    return res


@router.get("/queries/history")
async def query_history(connection_id: str | None = None, q: str | None = None, origin: str | None = None,
                        limit: int = 50, ctx: RequestContext = Depends(current_ctx)):
    """SQL editor history: the caller's own executed queries (manual and AI), newest first, de-duplicated."""
    origin = (origin or "").upper() or None
    if origin not in (None, "MANUAL", "AI"):
        raise ApiError("E-VAL-001", detail="origin must be MANUAL or AI")
    return {"items": await RunRepo(store(), ctx).history(connection_id, (q or "").strip() or None, origin, limit)}


@router.post("/queries/format")
async def format_sql(body: FormatIn, ctx: RequestContext = Depends(current_ctx)):
    conn = await load_connection(store(), ctx, body.connection_id)
    try:
        return {"sql": pretty(body.sql, conn["engine"])}
    except Exception:  # noqa: BLE001
        return {"sql": body.sql}


@router.post("/queries/execute")
async def execute_query(body: ExecuteIn, ctx: RequestContext = Depends(current_ctx)):
    limit("execute", ctx.user_ref, per_min=settings.get("ratelimit.execute_per_min", 60))
    return await execute(store(), ctx, connection_id=body.connection_id, sql=body.sql, origin=body.origin,
                         message_id=body.message_id, session_id=body.session_id, confirmed_by_user=body.confirmed_by_user,
                         confirmed_large=body.confirmed_large, run_id=body.run_id)


@router.get("/results/{handle}/rows")
async def rows(handle: str, page: int = 1, page_size: int = 50, sort: str | None = None,
               ctx: RequestContext = Depends(current_ctx)):
    h = handles.get(handle, ctx.user_ref)
    conn = await load_connection(store(), ctx, h.connection_id)
    return await fetch_page(registry.get(conn), h, page, page_size, sort)


@router.post("/results/{handle}/rerun")
async def rerun(handle: str, ctx: RequestContext = Depends(current_ctx)):
    run = await RunRepo(store(), ctx).get(handle)
    return await execute(store(), ctx, connection_id=str(run["connection_id"]), sql=run["sql_text"], origin="MANUAL",
                         message_id=None, session_id=None, confirmed_by_user=True,
                         confirmed_large=bool(run["confirmed_large"]) or run["status"] == "INTERCEPTED")


@router.post("/results/{handle}/cancel")
async def cancel(handle: str, ctx: RequestContext = Depends(current_ctx)):
    h = handles.get(handle, ctx.user_ref)
    conn = await load_connection(store(), ctx, h.connection_id)
    registry.get(conn).cancel(handle)
    await RunRepo(store(), ctx).update(handle, status="CANCELLED")
    return {"ok": True}


@router.post("/results/{handle}/export")
async def export(handle: str, ctx: RequestContext = Depends(current_ctx)):
    s = store()
    try:
        h = handles.get(handle, ctx.user_ref)
    except ApiError:
        h = await rerun_handle(s, ctx, handle)
    conn = await load_connection(s, ctx, h.connection_id)
    if h.total is not None and h.total > EXCEL_MAX:
        raise ApiError("E-EXP-002")
    sens = sorted(await sensitive_columns(s, h.connection_id) & {c["name"].lower() for c in h.columns})
    meta = {"connection": f"{conn['name']} ({conn['engine']}, {conn['country_code']})", "user": ctx.user_id,
            "sensitive_cols": sens, "filename": f"query-{handle[:8]}.xlsx"}
    sync_limit = int(settings.get("export.sync_row_limit", 10000))
    if h.total is not None and h.total <= sync_limit:
        out = export_path(handle)
        n = await in_pool(build_xlsx, registry.get(conn), h, out, meta)
        token = issue_download(out, meta["filename"], ctx.user_ref)
        await audit.record(s, action="QUERY_EXPORT", user_ref=ctx.user_ref, user_id=ctx.user_id,
                           context={"run_id": handle, "rows": n, "mode": "sync", "connection_id": h.connection_id})
        return {"download_url": f"/api/v1/downloads/{token}", "rows": n}
    jid = await runner().enqueue("build_export", {"run_id": handle, "meta": meta, "user_ref": ctx.user_ref,
                                                  "user_id": ctx.user_id, "role": ctx.role,
                                                  "grants_connections": sorted(ctx.grants_connections),
                                                  "grants_countries": sorted(ctx.grants_countries)}, ctx.user_ref)
    from fastapi.responses import JSONResponse
    return JSONResponse({"job_id": jid}, status_code=202)


@router.get("/downloads/{token}")
async def download(token: str, ctx: RequestContext = Depends(current_ctx)):
    path, filename = redeem_download(token, ctx.user_ref)
    if not Path(path).exists():
        raise ApiError("E-NF-001")
    return FileResponse(path, filename=filename,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ----------------------------------------------------------------------------- jobs
async def _job_for(jid: str, ctx: RequestContext) -> dict:
    row = await store().fetchone("SELECT id, type, status, progress, attempts, error, result, created_by, created_at, "
                                 "started_at, finished_at FROM jobs WHERE id = ?", [jid])
    if not row or (str(row["created_by"]) != ctx.user_ref and not ctx.is_admin):
        raise ApiError("E-NF-001")
    for k in ("progress", "result"):
        if isinstance(row[k], str):
            row[k] = json.loads(row[k])
    return row


@router.get("/jobs/{jid}")
async def job(jid: str, ctx: RequestContext = Depends(current_ctx)):
    return await _job_for(jid, ctx)


@router.get("/jobs/{jid}/events")
async def job_events(jid: str, request: Request, ctx: RequestContext = Depends(current_ctx)):
    await _job_for(jid, ctx)
    q = runner().subscribe(jid)

    async def stream():
        try:
            row = await _job_for(jid, ctx)
            yield f"event: status\ndata: {json.dumps(row, default=str)}\n\n"
            while row["status"] in ("QUEUED", "RUNNING"):
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"event: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"
                    if ev.get("status") in ("SUCCEEDED", "FAILED", "CANCELLED"):
                        break
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    row = await _job_for(jid, ctx)
                if await request.is_disconnected():
                    break
        finally:
            runner().unsubscribe(jid, q)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
