"""Background job bodies (TS §6.4): ingest, crawl, export, purge, backup, retention, reindex, verify, cleanup."""
from __future__ import annotations

import asyncio
import json
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..core.config import get_config
from ..core.errors import ApiError
from ..services import audit, purge, rag, schema
from ..services.connectors import registry
from ..services.execution import build_xlsx, cleanup_exports, export_path, handles, issue_download, rerun_handle
from ..services.llm import router
from ..services.settings import settings
from ..store.duck import AppStore
from ..store.repos import RequestContext
from .runner import task


@task("ingest_document")
async def ingest_document(store: AppStore, payload: dict, progress) -> dict:
    return await rag.ingest_document(store, get_config().data_dir, payload["document_id"], progress)


@task("crawl_schema")
async def crawl_schema(store: AppStore, payload: dict, progress) -> dict:
    conn = await store.fetchone("SELECT * FROM connections WHERE id = ?", [payload["connection_id"]])
    if not conn:
        raise ApiError("E-NF-001")
    adapter = registry.get(conn)
    try:
        result = await asyncio.to_thread(schema.crawl, store, conn, adapter, payload["_job_id"],
                                         payload.get("include_schemas"), progress)
        await audit.record(store, action="SCHEMA_CRAWL", user_ref=payload.get("user_ref"), user_id=payload.get("user_id"),
                           context={"connection_id": payload["connection_id"], **result})
        return result
    except Exception as e:
        await audit.record(store, action="SCHEMA_CRAWL", status="FAILED", user_ref=payload.get("user_ref"),
                           user_id=payload.get("user_id"), context={"connection_id": payload["connection_id"],
                                                                     "error": str(e)[:300]})
        raise


@task("build_export")
async def build_export(store: AppStore, payload: dict, progress) -> dict:
    ctx = RequestContext(user_ref=payload["user_ref"], user_id=payload["user_id"], role=payload["role"],
                         grants_connections=set(payload.get("grants_connections", [])),
                         grants_countries=set(payload.get("grants_countries", [])))
    try:
        h = handles.get(payload["run_id"], ctx.user_ref)
    except ApiError:
        h = await rerun_handle(store, ctx, payload["run_id"])
    conn = await store.fetchone("SELECT * FROM connections WHERE id = ?", [h.connection_id])
    adapter = registry.get(conn)
    out = export_path(h.run_id)
    n = await asyncio.to_thread(build_xlsx, adapter, h, out, payload["meta"], progress)
    token = issue_download(out, payload["meta"]["filename"], ctx.user_ref)
    await audit.record(store, action="QUERY_EXPORT", user_ref=ctx.user_ref, user_id=ctx.user_id,
                       context={"run_id": h.run_id, "rows": n, "mode": "async", "connection_id": h.connection_id})
    return {"rows": n, "download_url": f"/api/v1/downloads/{token}"}


@task("purge")
async def purge_task(store: AppStore, payload: dict, progress) -> dict:
    return await asyncio.to_thread(purge.execute, store, payload["scope"], payload.get("date_from"), payload["date_to"],
                                   payload.get("user_ref"), payload.get("user_id"), progress)


@task("backup")
async def backup(store: AppStore, payload: dict, progress) -> dict:
    return await asyncio.to_thread(do_backup, store, payload.get("to"), payload.get("user_ref"), payload.get("user_id"))


def do_backup(store: AppStore, to: str | None = None, user_ref: str | None = None, user_id: str | None = None) -> dict:
    """CHECKPOINT; ATTACH backup; COPY FROM DATABASE; DETACH — consistent copy while running (TS §24.5)."""
    cfg = get_config()
    bdir = cfg.data_dir / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    target = Path(to) if to else bdir / f"sqlassist-{stamp}.duckdb"
    target.unlink(missing_ok=True)

    def unit(c):
        c.execute("CHECKPOINT")
        c.execute(f"ATTACH '{str(target).replace(chr(39), chr(39) * 2)}' AS bk")
        try:
            c.execute("COPY FROM DATABASE " + _db_name(c) + " TO bk")
        finally:
            c.execute("DETACH bk")
    store.write_sync(unit, transactional=False)
    # incremental zip of uploads (new files since last zip)
    zpath = bdir / "uploads.zip"
    added = 0
    with zipfile.ZipFile(zpath, "a", zipfile.ZIP_DEFLATED) as z:
        have = set(z.namelist())
        up = cfg.data_dir / "uploads"
        for f in up.rglob("*"):
            if f.is_file():
                rel = str(f.relative_to(up))
                if rel not in have:
                    z.write(f, rel)
                    added += 1
    keep = int(settings.get("duckdb.backup_keep", 7))
    olds = sorted(bdir.glob("sqlassist-*.duckdb"))
    for f in olds[:-keep] if keep > 0 else []:
        f.unlink(missing_ok=True)
    result = {"file": str(target), "size": target.stat().st_size, "uploads_added": added}
    audit.record_sync(store, action="BACKUP_EXECUTED", user_ref=user_ref, user_id=user_id, context=result)
    return result


def _db_name(c) -> str:
    return c.execute("SELECT current_database()").fetchone()[0]


@task("retention")
async def retention(store: AppStore, payload: dict, progress) -> dict:
    out: dict[str, Any] = {}
    a, ch = int(settings.get("retention.audit_days", 0)), int(settings.get("retention.chat_days", 0))
    if a:
        cutoff = (date.today() - timedelta(days=a)).isoformat()
        out["audit"] = await asyncio.to_thread(purge.execute, store, "AUDIT", None, cutoff, None, "system:retention")
    if ch:
        cutoff = (date.today() - timedelta(days=ch)).isoformat()
        out["chat"] = await asyncio.to_thread(purge.execute, store, "CHAT", None, cutoff, None, "system:retention")
    return out


@task("verify_audit")
async def verify_audit(store: AppStore, payload: dict, progress) -> dict:
    res = await asyncio.to_thread(audit.verify_chain, store, get_config().log_dir)
    if not res["ok"]:
        import logging
        logging.getLogger("sqlassist.audit").error("audit chain verification failed: %s", res["problems"][:5])
        await settings.set(store, "system.audit_alert", {"at": datetime.now(timezone.utc).isoformat(), **res}, None)
    return res


@task("cleanup")
async def cleanup(store: AppStore, payload: dict, progress) -> dict:
    n = await asyncio.to_thread(cleanup_exports)
    from ..services.connectors import cleanup_wallet_temp
    return {"exports_deleted": n, "orphans": await asyncio.to_thread(integrity_report, store)}


def integrity_report(store: AppStore) -> dict:
    """Nightly orphan report (TS §7.1)."""
    q = {
        "chat_messages_without_session": "SELECT count(*) AS n FROM chat_messages m WHERE NOT EXISTS (SELECT 1 FROM chat_sessions s WHERE s.id = m.session_id)",
        "doc_chunks_without_document": "SELECT count(*) AS n FROM doc_chunks c WHERE NOT EXISTS (SELECT 1 FROM documents d WHERE d.id = c.document_id)",
        "schema_columns_without_table": "SELECT count(*) AS n FROM schema_columns c WHERE NOT EXISTS (SELECT 1 FROM schema_tables t WHERE t.id = c.table_id)",
        "grants_without_user": "SELECT count(*) AS n FROM user_grants g WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = g.user_ref)",
    }
    return {k: store.fetchall_sync(sql)[0]["n"] for k, sql in q.items()}


@task("reindex")
async def reindex(store: AppStore, payload: dict, progress) -> dict:
    """Re-embed all chunks and schema cards with the current primary embedder; build *_new and swap (TS §8.3)."""
    emb = router.embedder(store)
    if emb is None:
        raise ApiError("E-LLM-001")
    probe = await emb.embed(["dimension probe"])
    dim = len(probe[0])
    chunks = await store.fetchall("SELECT id, content FROM doc_chunks ORDER BY id")
    cards = await store.fetchall("SELECT table_id, card_text FROM schema_cards")
    batch = int(emb.params.get("embed_batch", 32))
    cvec: list = []
    for i in range(0, len(chunks), batch):
        cvec += await emb.embed([c["content"] for c in chunks[i:i + batch]])
        progress({"phase": "chunks", "done": min(i + batch, len(chunks)), "total": len(chunks)})
    svec: list = []
    for i in range(0, len(cards), batch):
        svec += await emb.embed([c["card_text"] for c in cards[i:i + batch]])

    def unit(c):
        c.execute("CREATE TABLE doc_chunks_new (id BIGINT PRIMARY KEY DEFAULT nextval('doc_chunk_seq'), document_id UUID NOT NULL, "
                  "scope rag_scope NOT NULL, owner_ref UUID NOT NULL, country_code VARCHAR(2), connection_id UUID, "
                  "chunk_index INTEGER NOT NULL, content VARCHAR NOT NULL, token_count INTEGER NOT NULL, "
                  f"meta JSON NOT NULL DEFAULT '{{}}', embedding FLOAT[{dim}])")
        c.execute("CREATE TABLE schema_cards_new (table_id UUID PRIMARY KEY, connection_id UUID NOT NULL, snapshot_id UUID NOT NULL, "
                  f"card_text VARCHAR NOT NULL, embedding FLOAT[{dim}])")
        vec_by_id = {ch["id"]: v for ch, v in zip(chunks, cvec)}
        for r in c.execute("SELECT id, document_id, scope, owner_ref, country_code, connection_id, chunk_index, content, "
                           "token_count, meta FROM doc_chunks").fetchall():
            c.execute(f"INSERT INTO doc_chunks_new VALUES (?,?,?,?,?,?,?,?,?,?, ?::FLOAT[{dim}])", list(r) + [vec_by_id.get(r[0])])
        vec_by_card = {str(cd["table_id"]): v for cd, v in zip(cards, svec)}
        for r in c.execute("SELECT table_id, connection_id, snapshot_id, card_text FROM schema_cards").fetchall():
            c.execute(f"INSERT INTO schema_cards_new VALUES (?,?,?,?, ?::FLOAT[{dim}])", list(r) + [vec_by_card.get(str(r[0]))])
        c.execute("DROP TABLE doc_chunks")
        c.execute("ALTER TABLE doc_chunks_new RENAME TO doc_chunks")
        c.execute("DROP TABLE schema_cards")
        c.execute("ALTER TABLE schema_cards_new RENAME TO schema_cards")
        c.execute("UPDATE documents SET embedding_model = ? WHERE status = 'READY'", [emb.row.get("embed_model")])
        c.execute("UPDATE llm_providers SET embed_dim = ? WHERE id = ?", [dim, str(emb.row["id"])])
    await store.write(unit)
    rag.rebuild_fts(store)
    return {"chunks": len(chunks), "cards": len(cards), "dim": dim}
