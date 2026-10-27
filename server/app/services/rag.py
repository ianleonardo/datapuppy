"""RAG ingestion & retrieval (TS §8)."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import re
from pathlib import Path
from typing import Any

from ..core.errors import ApiError
from ..store.duck import AppStore
from ..store.repos import RequestContext, knowledge_keyword_search, knowledge_vector_search
from .llm import heuristic_tokens, router
from .settings import settings

log = logging.getLogger("sqlassist.rag")

ALLOWED = {".pdf": "application/pdf",
           ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
           ".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown"}
# CSV is no longer accepted for new uploads; CSV references uploaded earlier still re-index and display.
MAX_BYTES = 52_428_800


def check_magic(ext: str, head: bytes) -> bool:
    if ext == ".pdf":
        return head.startswith(b"%PDF-")
    if ext == ".docx":
        return head.startswith(b"PK\x03\x04")
    # text formats: reject binary content (NUL bytes) and undecodable heads
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as e:
        if e.start < len(head) - 4:  # tolerate a multibyte char cut at the boundary
            try:
                head.decode("latin-1")
            except UnicodeDecodeError:
                return False
    return True


# ----------------------------------------------------------------------------- extraction
def extract(path: Path, ext: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Returns sections [{text, meta}] and warnings."""
    warnings: list[str] = []
    if ext == ".pdf":
        import pdfplumber

        sections = []
        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages, 1):
                text = page.extract_text() or ""
                if len(text.strip()) < 20:
                    warnings.append(f"page {i} possibly scanned (no text layer; OCR is out of scope)")
                    continue
                sections.append({"text": text, "meta": {"page": i}})
        return sections, warnings
    if ext == ".docx":
        import docx

        d = docx.Document(str(path))
        sections, heading, buf = [], None, []
        for p in d.paragraphs:
            if p.style is not None and p.style.name.lower().startswith("heading"):
                if buf:
                    sections.append({"text": "\n".join(buf), "meta": {"heading": heading}})
                heading, buf = p.text.strip(), [f"# {p.text.strip()}"]
            elif p.text.strip():
                buf.append(p.text)
        if buf:
            sections.append({"text": "\n".join(buf), "meta": {"heading": heading}})
        for ti, table in enumerate(d.tables):
            rows_ = ["| " + " | ".join(c.text.strip() for c in r.cells) + " |" for r in table.rows]
            sections.append({"text": "\n".join(rows_), "meta": {"table": ti + 1}})
        return sections, warnings
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    if ext == ".csv":
        n = int(settings.get("rag.csv_rows_per_chunk", 50))
        reader = list(csv.reader(io.StringIO(text)))
        if not reader:
            return [], warnings
        header, body = reader[0], reader[1:]
        sections = []
        for i in range(0, len(body), n):
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(header)
            w.writerows(body[i:i + n])
            sections.append({"text": buf.getvalue(), "meta": {"rows": [i + 1, min(i + n, len(body))], "csv": True}})
        return sections, warnings
    if ext in (".md", ".markdown"):
        return markdown_sections(text), warnings
    return [{"text": text, "meta": {}}], warnings


_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def markdown_sections(text: str) -> list[dict[str, Any]]:
    """Split Markdown at headings (outside fenced code) so each chunk carries its heading path as metadata."""
    sections: list[dict[str, Any]] = []
    path: list[str] = []
    buf: list[str] = []
    fence = False

    def flush():
        body = "\n".join(buf).strip()
        if body:
            sections.append({"text": body, "meta": {"heading": " > ".join(path) or None}})

    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        m = None if fence else _MD_HEADING.match(line)
        if m:
            flush()
            level = len(m.group(1))
            path = path[: level - 1] + [m.group(2).strip()]
            buf = [line]
        else:
            buf.append(line)
    flush()
    return sections


def preview_text(path: Path, ext: str, limit: int = 2_000_000) -> tuple[str, bool]:
    """Readable text of a stored document for the in-app viewer. Returns (text, truncated)."""
    if ext in (".pdf", ".docx"):
        sections, _ = extract(path, ext)
        parts = []
        for sec in sections:
            m = sec["meta"]
            if "page" in m:
                parts.append(f"--- Page {m['page']} ---\n{sec['text']}")
            else:
                parts.append(sec["text"])
        text = "\n\n".join(parts)
    else:
        raw = path.read_bytes()[: limit + 1]
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
    return (text[:limit], len(text) > limit)


def chunk(sections: list[dict[str, Any]], target: int = 800, overlap: int = 120) -> list[dict[str, Any]]:
    """Headings → paragraphs → sentences, ~target tokens with overlap. CSV sections are kept intact."""
    out: list[dict[str, Any]] = []
    for sec in sections:
        if sec["meta"].get("csv"):
            out.append({"content": sec["text"], "meta": sec["meta"], "tokens": heuristic_tokens(sec["text"])})
            continue
        units: list[str] = []
        for para in re.split(r"\n\s*\n|(?=\n#)", sec["text"]):
            para = para.strip()
            if not para:
                continue
            if heuristic_tokens(para) <= target:
                units.append(para)
            else:
                units += [s for s in re.split(r"(?<=[.!?。])\s+", para) if s.strip()]
        buf: list[str] = []
        size = 0
        for u in units:
            t = heuristic_tokens(u)
            if size + t > target and buf:
                text = "\n\n".join(buf)
                out.append({"content": text, "meta": sec["meta"], "tokens": heuristic_tokens(text)})
                tail: list[str] = []
                tsize = 0
                for prev in reversed(buf):
                    pt = heuristic_tokens(prev)
                    if tsize + pt > overlap:
                        break
                    tail.insert(0, prev)
                    tsize += pt
                buf, size = tail, tsize
            if t > target:  # hard split very long sentences
                for i in range(0, len(u), target * 3):
                    piece = u[i:i + target * 3]
                    out.append({"content": piece, "meta": sec["meta"], "tokens": heuristic_tokens(piece)})
                continue
            buf.append(u)
            size += t
        if buf:
            text = "\n\n".join(buf)
            out.append({"content": text, "meta": sec["meta"], "tokens": heuristic_tokens(text)})
    return [c for c in out if c["content"].strip()]


# ----------------------------------------------------------------------------- ingest job
async def ingest_document(store: AppStore, data_dir: Path, doc_id: str, progress) -> dict[str, Any]:
    doc = await store.fetchone("SELECT * FROM documents WHERE id = ?", [doc_id])
    if not doc:
        return {"skipped": "deleted"}
    ext = Path(doc["filename"]).suffix.lower()

    async def status(s: str, detail: str | None = None, **extra) -> None:
        sets = "status = ?, status_detail = ?"
        vals: list[Any] = [s, detail]
        for k, v in extra.items():
            sets += f", {k} = ?"
            vals.append(v)
        await store.execute(f"UPDATE documents SET {sets} WHERE id = ?", vals + [doc_id])

    await status("EXTRACTING")
    try:
        sections, warnings = await asyncio.to_thread(extract, data_dir / "uploads" / doc["file_path"], ext)
    except Exception as e:  # noqa: BLE001
        await status("FAILED", f"E-RAG-005: {str(e)[:150]}")
        raise ApiError("E-RAG-005") from e
    chunks = chunk(sections, int(settings.get("rag.chunk_tokens", 800)), int(settings.get("rag.chunk_overlap", 120)))
    if not chunks:
        await status("FAILED", "E-RAG-005: no extractable text" + (f" ({'; '.join(warnings[:3])})" if warnings else ""))
        return {"chunks": 0}
    progress({"phase": "embedding", "done": 0, "total": len(chunks)})
    await status("EMBEDDING", "; ".join(warnings[:5]) or None)
    embedder = router.embedder(store)
    vectors: list[list[float] | None] = [None] * len(chunks)
    model = None
    if embedder is not None:
        batch = int(embedder.params.get("embed_batch", 32))
        vectors = []
        for i in range(0, len(chunks), batch):
            texts = [c["content"] for c in chunks[i:i + batch]]
            for attempt in range(5):  # exponential backoff
                try:
                    vectors += await embedder.embed(texts)
                    break
                except ApiError:
                    if attempt == 4:
                        await status("FAILED", "E-LLM-001: embedding provider unavailable")
                        raise
                    await asyncio.sleep(2 ** attempt)
            progress({"phase": "embedding", "done": min(i + batch, len(chunks)), "total": len(chunks)})
        model = embedder.row.get("embed_model")
    dim = await store.read(lambda c: c.execute(
        "SELECT data_type FROM duckdb_columns() WHERE table_name='doc_chunks' AND column_name='embedding'").fetchone()[0])
    store_dim = int(re.search(r"\[(\d+)\]", dim).group(1))
    if vectors and vectors[0] is not None and len(vectors[0]) != store_dim:
        await status("FAILED", f"embedding dimension {len(vectors[0])} != store dimension {store_dim}; re-index required")
        raise ApiError("E-LLM-002")

    def unit(c):
        c.execute("DELETE FROM doc_chunks WHERE document_id = ?", [doc_id])
        for i, (ch, vec) in enumerate(zip(chunks, vectors)):
            c.execute(f"INSERT INTO doc_chunks(document_id, scope, owner_ref, country_code, connection_id, chunk_index, "
                      f"content, token_count, meta, embedding) VALUES (?,?,?,?,?,?,?,?,?, ?::FLOAT[{store_dim}])",
                      [doc_id, doc["scope"], str(doc["owner_ref"]), doc["country_code"],
                       str(doc["connection_id"]) if doc["connection_id"] else None, i, ch["content"], ch["tokens"],
                       json.dumps(ch["meta"]), vec])
        c.execute("UPDATE documents SET status = 'READY', chunk_count = ?, embedding_model = ?, status_detail = ? WHERE id = ?",
                  [len(chunks), model, ("; ".join(warnings[:5]) or None) if model else
                   "stored without vectors (no embedding provider) — keyword search only", doc_id])
    await store.write(unit)
    rebuild_fts(store)
    return {"chunks": len(chunks), "embedded": model is not None}


def rebuild_fts(store: AppStore) -> None:
    """DuckDB FTS indexes don't update incrementally — rebuild after each document (TS §8.4)."""
    if not store.extensions.get("fts"):
        return
    try:
        store.write_sync(lambda c: c.execute(
            "PRAGMA create_fts_index('doc_chunks', 'id', 'content', overwrite=1)"), transactional=False)
    except Exception as e:  # noqa: BLE001
        log.warning("FTS rebuild failed: %s", e)


# ----------------------------------------------------------------------------- retrieval (TS §8.4)
def rrf(*legs: list[dict], k: int = 60) -> list[dict]:
    scores: dict[Any, float] = {}
    items: dict[Any, dict] = {}
    for leg in legs:
        for rank, item in enumerate(leg):
            scores[item["id"]] = scores.get(item["id"], 0.0) + 1.0 / (k + rank + 1)
            items[item["id"]] = item
    return [items[i] | {"rrf": s} for i, s in sorted(scores.items(), key=lambda x: -x[1])]


async def retrieve_knowledge(store: AppStore, ctx: RequestContext, prompt: str, qvec: list[float] | None,
                             country: str | None, conn: str | None) -> list[dict]:
    k = 8
    vec_leg: list[dict] = []
    if qvec is not None:
        vec_leg = await asyncio.to_thread(knowledge_vector_search, store, ctx, qvec, len(qvec), country, conn, k)
    kw_leg = await asyncio.to_thread(knowledge_keyword_search, store, ctx, prompt, country, conn, k,
                                     bool(store.extensions.get("fts")))
    fused = rrf(vec_leg, kw_leg)[: int(settings.get("rag.knowledge_top_k", 6))]
    if fused:
        names = {str(r["id"]): r["filename"] for r in await store.fetchall(
            "SELECT id, filename FROM documents WHERE list_contains(?, CAST(id AS VARCHAR))",
            [[str(f["document_id"]) for f in fused]])}
        for f in fused:
            f["filename"] = names.get(str(f["document_id"]), "document")
    return fused


async def retrieve_schema(store: AppStore, conn_id: str, prompt: str, qvec: list[float] | None) -> list[dict]:
    """Top-k schema cards + 1-hop FK neighbours, capped (TS §8.4 step 1)."""
    cards = await store.fetchall("SELECT c.table_id, c.card_text, t.table_name, c.embedding IS NOT NULL AS has_vec "
                                 "FROM schema_cards c JOIN schema_tables t ON t.id = c.table_id WHERE c.connection_id = ?",
                                 [conn_id])
    max_tables = int(settings.get("rag.schema_max_tables", 20))
    if len(cards) <= max_tables:
        return cards
    top_k = int(settings.get("rag.schema_top_k", 12))
    chosen: list[dict] = []
    if qvec is not None and any(c["has_vec"] for c in cards):
        dim = len(qvec)
        chosen = await store.fetchall(
            f"SELECT c.table_id, c.card_text, t.table_name, array_cosine_similarity(c.embedding, ?::FLOAT[{dim}]) AS score "
            "FROM schema_cards c JOIN schema_tables t ON t.id = c.table_id WHERE c.connection_id = ? AND c.embedding IS NOT "
            "NULL ORDER BY score DESC LIMIT ?", [qvec, conn_id, top_k])
    else:
        words = {w.lower() for w in re.findall(r"[A-Za-z_]{3,}", prompt)}
        scored = sorted(cards, key=lambda c: -sum(w in c["card_text"].lower() for w in words))
        chosen = scored[:top_k]
    ids = {str(c["table_id"]) for c in chosen}
    neigh = await store.fetchall(
        "SELECT DISTINCT CASE WHEN list_contains(?, CAST(from_table_id AS VARCHAR)) THEN to_table_id ELSE from_table_id END "
        "AS tid FROM schema_relationships r JOIN schema_snapshots s ON s.id = r.snapshot_id WHERE s.connection_id = ? AND "
        "s.state = 'LOCKED' AND (list_contains(?, CAST(from_table_id AS VARCHAR)) OR list_contains(?, CAST(to_table_id AS VARCHAR)))",
        [list(ids), conn_id, list(ids), list(ids)])
    by_id = {str(c["table_id"]): c for c in cards}
    for n in neigh:
        if len(chosen) >= max_tables:
            break
        tid = str(n["tid"])
        if tid not in ids and tid in by_id:
            chosen.append(by_id[tid])
            ids.add(tid)
    return chosen
