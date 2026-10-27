"""RAG documents & analyst schema views (TS §20.4)."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from ...auth.deps import current_ctx
from ...auth.ratelimit import limit
from ...core.config import get_config
from ...core.errors import ApiError
from ...jobs.runner import runner
from ...permissions import has_connection_access
from ...services import audit, rag, schema
from ...services.settings import settings
from ...store.duck import store
from ...store.repos import DocumentRepo, RequestContext

router = APIRouter(tags=["rag", "schema"])


@router.post("/rag/documents")
async def upload(file: UploadFile = File(...), scope: str = Form("PRIVATE"), country_code: str | None = Form(None),
                 connection_id: str | None = Form(None), ctx: RequestContext = Depends(current_ctx)):
    limit("upload", ctx.user_ref, per_hour=settings.get("ratelimit.uploads_per_hour", 20))
    s = store()
    scope = scope.upper()
    if scope not in ("GLOBAL", "COUNTRY", "CONNECTION", "PRIVATE"):
        raise ApiError("E-VAL-001", detail="Invalid scope")
    if scope != "PRIVATE" and not ctx.is_admin:
        raise ApiError("E-AUTH-002")
    if scope == "COUNTRY" and not country_code:
        raise ApiError("E-VAL-001", detail="country_code required for COUNTRY scope")
    if scope == "CONNECTION" and not connection_id:
        raise ApiError("E-VAL-001", detail="connection_id required for CONNECTION scope")
    if scope != "COUNTRY":
        country_code = None
    if scope != "CONNECTION":
        connection_id = None
    filename = Path(file.filename or "upload").name
    ext = Path(filename).suffix.lower()
    ctx_audit = {"filename": filename, "scope": scope}

    async def reject(code: str, **extra):
        await audit.record(s, action="DOCUMENT_UPLOAD", status="FAILED", user_ref=ctx.user_ref, user_id=ctx.user_id,
                           context={**ctx_audit, "error": code, **extra})
        raise ApiError(code)

    if ext not in rag.ALLOWED:
        await reject("E-RAG-001")
    max_bytes = min(int(settings.get("rag.max_file_mb", 50)), 50) * 1024 * 1024
    now = datetime.now()
    rel = Path(f"{now:%Y}/{now:%m}/{uuid.uuid4()}{ext}")
    dest = get_config().data_dir / "uploads" / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256()
    size = 0
    head = b""
    with dest.open("wb") as fh:  # counted as it streams; aborted at the cap (TR-RAG-01)
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                fh.close()
                dest.unlink(missing_ok=True)
                await reject("E-RAG-002", size_bytes=size)
            if len(head) < 4096:
                head += chunk[: 4096 - len(head)]
            sha.update(chunk)
            fh.write(chunk)
    if not rag.check_magic(ext, head):
        dest.unlink(missing_ok=True)
        await reject("E-RAG-003")
    digest = sha.hexdigest()
    repo = DocumentRepo(s, ctx)
    dup = await repo.exists_sha(digest, scope, country_code, connection_id)
    if dup:
        dest.unlink(missing_ok=True)
        await audit.record(s, action="DOCUMENT_UPLOAD", status="FAILED", user_ref=ctx.user_ref, user_id=ctx.user_id,
                           context={**ctx_audit, "error": "E-RAG-004"})
        raise ApiError("E-RAG-004", existing_id=dup, reindex_offer=True)
    doc_id = str(uuid.uuid4())
    await s.execute("INSERT INTO documents(id, owner_ref, scope, country_code, connection_id, filename, mime_type, size_bytes, "
                    "sha256, file_path, status) VALUES (?,?,?,?,?,?,?,?,?,?, 'UPLOADED')",
                    [doc_id, ctx.user_ref, scope, country_code, connection_id, filename, rag.ALLOWED[ext], size, digest,
                     str(rel).replace("\\", "/")])
    await audit.record(s, action="DOCUMENT_UPLOAD", user_ref=ctx.user_ref, user_id=ctx.user_id,
                       context={**ctx_audit, "document_id": doc_id, "size_bytes": size, "sha256": digest})
    jid = await runner().enqueue("ingest_document", {"document_id": doc_id}, ctx.user_ref, idempotency_key=f"ingest:{doc_id}")
    return JSONResponse({"id": doc_id, "status": "UPLOADED", "job_id": jid}, status_code=202)


@router.get("/rag/documents")
async def list_docs(scope: str | None = None, ctx: RequestContext = Depends(current_ctx)):
    return {"items": await DocumentRepo(store(), ctx).list(scope)}


@router.get("/rag/documents/{doc_id}")
async def get_doc(doc_id: str, ctx: RequestContext = Depends(current_ctx)):
    d = await DocumentRepo(store(), ctx).get(doc_id)
    d["is_mine"] = str(d.pop("owner_ref")) == ctx.user_ref
    d.pop("file_path", None)
    return d


@router.get("/rag/documents/{doc_id}/text")
async def doc_text(doc_id: str, ctx: RequestContext = Depends(current_ctx)):
    """Readable text for the viewer (Markdown/TXT raw; PDF/DOCX extracted). Same visibility as get_doc."""
    d = await DocumentRepo(store(), ctx).get(doc_id)
    path = get_config().data_dir / "uploads" / d["file_path"]
    if not path.exists():
        raise ApiError("E-NF-001")
    import asyncio
    text, truncated = await asyncio.to_thread(rag.preview_text, path, Path(d["filename"]).suffix.lower())
    return {"id": doc_id, "filename": d["filename"], "mime_type": d["mime_type"], "text": text, "truncated": truncated}


@router.get("/rag/documents/{doc_id}/file")
async def doc_file(doc_id: str, download: bool = False, ctx: RequestContext = Depends(current_ctx)):
    """Original file. Served inline (PDF viewer) unless ?download=1; framing allowed from this origin only."""
    d = await DocumentRepo(store(), ctx).get(doc_id)
    path = get_config().data_dir / "uploads" / d["file_path"]
    if not path.exists():
        raise ApiError("E-NF-001")
    resp = FileResponse(path, media_type=d["mime_type"], filename=d["filename"],
                        content_disposition_type="attachment" if download else "inline")
    resp.headers["X-Allow-Same-Origin-Frame"] = "1"
    return resp


@router.delete("/rag/documents/{doc_id}")
async def delete_doc(doc_id: str, ctx: RequestContext = Depends(current_ctx)):
    s = store()
    doc = await DocumentRepo(s, ctx).delete(doc_id)
    (get_config().data_dir / "uploads" / doc["file_path"]).unlink(missing_ok=True)
    rag.rebuild_fts(s)
    await audit.record(s, action="DOCUMENT_DELETE", user_ref=ctx.user_ref, user_id=ctx.user_id,
                       context={"document_id": doc_id, "filename": doc["filename"], "scope": doc["scope"]})
    return {"ok": True}


@router.post("/rag/documents/{doc_id}/reindex")
async def reindex_doc(doc_id: str, ctx: RequestContext = Depends(current_ctx)):
    doc = await DocumentRepo(store(), ctx).get(doc_id)
    if doc["scope"] != "PRIVATE" and not ctx.is_admin:
        raise ApiError("E-AUTH-002")
    if doc["scope"] == "PRIVATE" and str(doc["owner_ref"]) != ctx.user_ref:
        raise ApiError("E-NF-001")
    jid = await runner().enqueue("ingest_document", {"document_id": doc_id}, ctx.user_ref, idempotency_key=f"ingest:{doc_id}")
    return {"job_id": jid}


# ----------------------------------------------------------------------------- schema (analyst read views)
@router.get("/schema/tree")
async def tree(ctx: RequestContext = Depends(current_ctx)):
    """Country → Database (connection) → Schema → Table/View. Analysts: granted + INCLUDED of the locked snapshot."""
    s = store()
    conns = await s.fetchall("SELECT c.*, k.name AS country_name FROM connections c LEFT JOIN countries k ON k.code = "
                             "c.country_code ORDER BY c.country_code, c.name")
    out: dict[str, dict] = {}
    for c in conns:
        if not has_connection_access(ctx, c):
            continue
        snap = schema.locked_snapshot(s, str(c["id"]))
        draft = None
        if ctx.is_admin and not snap:
            d = await s.fetchall("SELECT * FROM schema_snapshots WHERE connection_id = ? AND state = 'DRAFT' ORDER BY "
                                 "version DESC LIMIT 1", [str(c["id"])])
            draft = d[0] if d else None
        use = snap or draft
        schemas: dict[str, list] = {}
        if use:
            for t in schema.snapshot_tables(s, str(use["id"]), included_only=not ctx.is_admin):
                schemas.setdefault(t["schema_name"], []).append({
                    "id": str(t["id"]), "name": t["table_name"], "type": t["object_type"], "alias": t["alias"],
                    "classification": t["classification"], "inclusion": t["inclusion"], "smell_flags": t["smell_flags"],
                    "row_estimate": t["row_estimate"]})
        country = out.setdefault(c["country_code"], {"code": c["country_code"], "name": c["country_name"], "databases": []})
        country["databases"].append({"id": str(c["id"]), "name": c["name"], "engine": c["engine"],
                                     "snapshot": {"id": str(use["id"]), "state": use["state"], "version": use["version"]}
                                     if use else None,
                                     "schemas": [{"name": k, "tables": v} for k, v in schemas.items()]})
    return {"countries": list(out.values())}


@router.get("/schema/{conn}/tables/{table_id}")
async def table_detail(conn: str, table_id: str, ctx: RequestContext = Depends(current_ctx)):
    s = store()
    c = await s.fetchone("SELECT * FROM connections WHERE id = ?", [conn])
    if not c or not has_connection_access(ctx, c):
        raise ApiError("E-AUTH-003")
    t = await s.fetchone("SELECT t.* FROM schema_tables t JOIN schema_snapshots s ON s.id = t.snapshot_id WHERE t.id = ? "
                         "AND s.connection_id = ?", [table_id, conn])
    if not t or (not ctx.is_admin and t["inclusion"] != "INCLUDED"):
        raise ApiError("E-NF-001")
    cols = schema.table_columns(s, [table_id]).get(table_id, [])
    if not ctx.is_admin:
        cols = [x for x in cols if not x["is_masked"]]
    return {**t, "columns": cols}


@router.get("/schema/{conn}/erd")
async def erd(conn: str, tables: str | None = None, ctx: RequestContext = Depends(current_ctx)):
    s = store()
    c = await s.fetchone("SELECT * FROM connections WHERE id = ?", [conn])
    if not c or not has_connection_access(ctx, c):
        raise ApiError("E-AUTH-003")
    only = [t.strip() for t in tables.split(",")] if tables else None
    source, shown = schema.mermaid_erd(s, conn, only=only, include_all=ctx.is_admin)
    return {"mermaid": source, "tables": shown}


@router.get("/starter-prompts")
async def starter_prompts(ctx: RequestContext = Depends(current_ctx)):
    return {"items": await store().fetchall("SELECT id, label, prompt, intent FROM starter_prompts WHERE is_active "
                                            "ORDER BY sort_order")}
