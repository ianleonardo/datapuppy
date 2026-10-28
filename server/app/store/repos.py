"""Repositories. User-owned tables are only reachable through UserScopedRepo (DD-13, TS §15.3).

CI lint (tests/test_lint_rules.py) fails if SQL touching chat_sessions, chat_messages, query_runs or
doc_chunks appears outside this module (plus the explicitly allow-listed admin purge/retention code).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..core.errors import ApiError, not_found
from .duck import AppStore, rows


@dataclass
class RequestContext:
    user_ref: str
    user_id: str
    role: str
    display_name: str = ""
    grants_connections: set[str] = field(default_factory=set)
    grants_countries: set[str] = field(default_factory=set)
    request_id: str = ""
    theme: str = "morning"
    csrf_token: str = ""
    auth_source: str = "LOCAL"

    @property
    def is_admin(self) -> bool:
        return self.role == "SYSTEM_ADMIN"


def _j(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


class UserScopedRepo:
    """Every statement injects owner_ref = ctx.user_ref. There is no method without a context."""

    def __init__(self, store: AppStore, ctx: RequestContext):
        if ctx is None or not ctx.user_ref:
            raise ValueError("UserScopedRepo requires a RequestContext")
        self.s = store
        self.ctx = ctx

    @property
    def owner(self) -> str:
        return self.ctx.user_ref


class ChatRepo(UserScopedRepo):
    async def list_sessions(self, q: str | None = None, limit: int = 50, before: str | None = None) -> list[dict]:
        sql = ("SELECT s.id, s.title, s.connection_id, s.created_at, s.updated_at FROM chat_sessions s "
               "WHERE s.owner_ref = ?")
        params: list[Any] = [self.owner]
        if q:
            sql += (" AND (s.title ILIKE ? OR EXISTS (SELECT 1 FROM chat_messages m WHERE m.session_id = s.id "
                    "AND m.owner_ref = ? AND m.plain_text ILIKE ?))")
            params += [f"%{q}%", self.owner, f"%{q}%"]
        if before:
            sql += " AND s.updated_at < ?::TIMESTAMPTZ"
            params.append(before)
        sql += " ORDER BY s.updated_at DESC LIMIT ?"
        params.append(limit)
        return await self.s.fetchall(sql, params)

    async def create_session(self, connection_id: str | None, title: str = "New chat") -> dict:
        sid = str(uuid.uuid4())
        await self.s.execute("INSERT INTO chat_sessions(id, owner_ref, title, connection_id) VALUES (?, ?, ?, ?)",
                             [sid, self.owner, title, connection_id])
        return await self.get_session(sid)

    async def get_session(self, sid: str) -> dict:
        row = await self.s.fetchone("SELECT id, title, connection_id, created_at, updated_at FROM chat_sessions "
                                    "WHERE id = ? AND owner_ref = ?", [sid, self.owner])
        if not row:
            raise not_found()  # 404, not 403 (TS §15.3)
        return row

    async def update_session(self, sid: str, *, title: str | None = None, connection_id: str | None = None) -> None:
        await self.get_session(sid)
        if title is not None:
            await self.s.execute("UPDATE chat_sessions SET title = ?, updated_at = now() WHERE id = ? AND owner_ref = ?",
                                 [title[:200], sid, self.owner])
        if connection_id is not None:
            await self.s.execute("UPDATE chat_sessions SET connection_id = ?, updated_at = now() "
                                 "WHERE id = ? AND owner_ref = ?", [connection_id, sid, self.owner])

    async def delete_session(self, sid: str) -> None:
        await self.get_session(sid)
        owner = self.owner

        def unit(c):  # application-level cascade in one transaction (TR-STO-02)
            c.execute("UPDATE query_runs SET session_id = NULL, message_id = NULL WHERE session_id = ? AND owner_ref = ?",
                      [sid, owner])
            c.execute("DELETE FROM chat_messages WHERE session_id = ? AND owner_ref = ?", [sid, owner])
            c.execute("DELETE FROM chat_sessions WHERE id = ? AND owner_ref = ?", [sid, owner])
        await self.s.write(unit)

    async def messages(self, sid: str, limit: int = 200, after_seq: int = 0) -> list[dict]:
        await self.get_session(sid)
        out = await self.s.fetchall(
            "SELECT id, seq, role, content, plain_text, artifacts, llm_meta, feedback, created_at FROM chat_messages "
            "WHERE session_id = ? AND owner_ref = ? AND seq > ? ORDER BY seq LIMIT ?",
            [sid, self.owner, after_seq, limit])
        for m in out:
            for k in ("content", "artifacts", "llm_meta"):
                m[k] = _j(m[k])
        return out

    async def history(self, sid: str, turns: int) -> list[dict]:
        out = await self.s.fetchall(
            "SELECT role, plain_text, artifacts FROM chat_messages WHERE session_id = ? AND owner_ref = ? "
            "AND role <> 'SYSTEM_EVENT' ORDER BY seq DESC LIMIT ?", [sid, self.owner, turns * 2])
        return list(reversed(out))

    async def add_message(self, sid: str, role: str, content: Any, plain_text: str,
                          artifacts: dict | None = None, llm_meta: dict | None = None,
                          msg_id: str | None = None) -> dict:
        owner = self.owner
        mid = msg_id or str(uuid.uuid4())

        def unit(c):
            seq = c.execute("SELECT coalesce(max(seq), 0) + 1 FROM chat_messages WHERE session_id = ?", [sid]).fetchone()[0]
            c.execute("INSERT INTO chat_messages(id, session_id, owner_ref, seq, role, content, plain_text, artifacts, "
                      "llm_meta) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      [mid, sid, owner, seq, role, json.dumps(content, default=str), plain_text,
                       json.dumps(artifacts or {}, default=str), json.dumps(llm_meta, default=str) if llm_meta else None])
            c.execute("UPDATE chat_sessions SET updated_at = now() WHERE id = ? AND owner_ref = ?", [sid, owner])
            return seq
        seq = await self.s.write(unit)
        return {"id": mid, "seq": seq}

    async def update_artifacts(self, mid: str, patch: dict) -> None:
        row = await self.s.fetchone("SELECT artifacts FROM chat_messages WHERE id = ? AND owner_ref = ?", [mid, self.owner])
        if not row:
            raise not_found()
        art = _j(row["artifacts"]) or {}
        art.update(patch)
        await self.s.execute("UPDATE chat_messages SET artifacts = ? WHERE id = ? AND owner_ref = ?",
                             [json.dumps(art, default=str), mid, self.owner])

    async def set_feedback(self, mid: str, value: int) -> None:
        row = await self.s.fetchone("SELECT id FROM chat_messages WHERE id = ? AND owner_ref = ?", [mid, self.owner])
        if not row:
            raise not_found()
        await self.s.execute("UPDATE chat_messages SET feedback = ? WHERE id = ? AND owner_ref = ?",
                             [value, mid, self.owner])

    async def message_count(self, sid: str) -> int:
        r = await self.s.fetchone("SELECT count(*) AS n FROM chat_messages WHERE session_id = ? AND owner_ref = ?",
                                  [sid, self.owner])
        return int(r["n"]) if r else 0


class RunRepo(UserScopedRepo):
    async def create(self, **fields: Any) -> str:
        rid = fields.pop("id", None) or str(uuid.uuid4())
        fields["owner_ref"] = self.owner
        cols = ["id"] + list(fields)
        vals = [rid] + [json.dumps(v) if isinstance(v, (dict, list)) else v for v in fields.values()]
        await self.s.execute(f"INSERT INTO query_runs({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", vals)
        return rid

    async def update(self, rid: str, **fields: Any) -> None:
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = [json.dumps(v) if isinstance(v, (dict, list)) else v for v in fields.values()]
        await self.s.execute(f"UPDATE query_runs SET {sets} WHERE id = ? AND owner_ref = ?", vals + [rid, self.owner])

    async def get(self, rid: str) -> dict:
        row = await self.s.fetchone("SELECT * FROM query_runs WHERE id = ? AND owner_ref = ?", [rid, self.owner])
        if not row:
            raise not_found()
        row["columns"] = _j(row["columns"])
        return row

    async def history(self, connection_id: str | None = None, q: str | None = None, origin: str | None = None,
                      limit: int = 50) -> list[dict]:
        """The user's executed queries, newest first, one entry per distinct SQL text (per database)."""
        where = ["r.owner_ref = ?", "r.status <> 'PENDING'"]
        params: list[Any] = [self.owner]
        if connection_id:
            where.append("r.connection_id = ?")
            params.append(connection_id)
        if q:
            where.append("r.sql_text ILIKE ?")
            params.append(f"%{q}%")
        if origin == "MANUAL":
            where.append("r.origin = 'MANUAL'")
        elif origin == "AI":
            where.append("r.origin IN ('AI', 'REFINED')")
        sql = (
            "SELECT r.id, r.connection_id, c.name AS connection_name, c.engine, r.origin, r.sql_text, r.status, "
            "r.row_count, r.duration_ms, r.error_code, coalesce(r.finished_at, r.started_at) AS ran_at, "
            "count(*) OVER (PARTITION BY r.connection_id, r.sql_hash) AS times_run "
            "FROM query_runs r LEFT JOIN connections c ON c.id = r.connection_id "
            f"WHERE {' AND '.join(where)} "
            "QUALIFY row_number() OVER (PARTITION BY r.connection_id, r.sql_hash "
            "ORDER BY coalesce(r.finished_at, r.started_at) DESC NULLS LAST) = 1 "
            "ORDER BY ran_at DESC NULLS LAST LIMIT ?")
        params.append(max(1, min(int(limit), 200)))
        return await self.s.fetchall(sql, params)


class DocumentRepo(UserScopedRepo):
    """Visibility rules (DD-03): PRIVATE only for owner; shared scopes visible to all (retrieval filters further)."""

    async def list(self, scope: str | None = None, kind: str = "REFERENCE") -> list[dict]:
        sql = ("SELECT id, owner_ref, scope, country_code, connection_id, filename, mime_type, size_bytes, status, "
               "status_detail, chunk_count, created_at, coalesce(kind, 'REFERENCE') AS kind FROM documents "
               "WHERE (scope <> 'PRIVATE' OR owner_ref = ?) AND coalesce(kind, 'REFERENCE') = ?")
        params: list[Any] = [self.owner, kind]
        if scope:
            sql += " AND scope = ?"
            params.append(scope)
        sql += " ORDER BY created_at DESC"
        out = await self.s.fetchall(sql, params)
        for d in out:
            d["is_mine"] = str(d.pop("owner_ref")) == self.owner
        return out

    async def get(self, doc_id: str) -> dict:
        row = await self.s.fetchone("SELECT * FROM documents WHERE id = ? AND (scope <> 'PRIVATE' OR owner_ref = ?)",
                                    [doc_id, self.owner])
        if not row:
            raise not_found()
        return row

    async def exists_sha(self, sha: str, scope: str, country: str | None, conn: str | None,
                         kind: str = "REFERENCE") -> str | None:
        r = await self.s.fetchone(
            "SELECT id FROM documents WHERE sha256 = ? AND scope = ? AND coalesce(country_code,'') = coalesce(?, '') "
            "AND coalesce(CAST(connection_id AS VARCHAR),'') = coalesce(?, '') AND (scope <> 'PRIVATE' OR owner_ref = ?) "
            "AND coalesce(kind, 'REFERENCE') = ?",
            [sha, scope, country, conn, self.owner, kind])
        return str(r["id"]) if r else None

    async def delete(self, doc_id: str) -> dict:
        doc = await self.get(doc_id)
        if doc["scope"] == "PRIVATE" and str(doc["owner_ref"]) != self.owner:
            raise not_found()
        if doc["scope"] != "PRIVATE" and not self.ctx.is_admin:
            raise ApiError("E-AUTH-002")

        def unit(c):
            c.execute("DELETE FROM doc_chunks WHERE document_id = ?", [doc_id])
            c.execute("DELETE FROM documents WHERE id = ?", [doc_id])
        await self.s.write(unit)
        return doc


# ----------------------------------------------------------------------------- retrieval (scope predicate)
SCOPE_PREDICATE = ("(scope = 'GLOBAL' OR (scope = 'COUNTRY' AND country_code = ?) OR "
                   "(scope = 'CONNECTION' AND connection_id = ?) OR (scope = 'PRIVATE' AND owner_ref = ?))")
# documents of one kind (REFERENCE / GUIDELINE); doc_chunks has no kind column of its own
KIND_PREDICATE = "document_id IN (SELECT id FROM documents WHERE coalesce(kind, 'REFERENCE') = ?)"


def knowledge_vector_search(store: AppStore, ctx: RequestContext, qvec: list[float], dim: int, country: str | None,
                            conn: str | None, k: int, kind: str = "REFERENCE") -> list[dict]:
    sql = (f"SELECT id, document_id, content, meta, array_cosine_similarity(embedding, ?::FLOAT[{int(dim)}]) AS score "
           f"FROM doc_chunks WHERE embedding IS NOT NULL AND {SCOPE_PREDICATE} AND {KIND_PREDICATE} "
           f"ORDER BY score DESC LIMIT ?")
    return store.read_sync(lambda c: rows(c.execute(sql, [qvec, country, conn, ctx.user_ref, kind, k])))


def knowledge_keyword_search(store: AppStore, ctx: RequestContext, text: str, country: str | None,
                             conn: str | None, k: int, fts: bool, kind: str = "REFERENCE") -> list[dict]:
    if fts:
        sql = (f"SELECT id, document_id, content, meta, score FROM (SELECT *, fts_main_doc_chunks.match_bm25(id, ?) AS score "
               f"FROM doc_chunks) t WHERE score IS NOT NULL AND {SCOPE_PREDICATE} AND {KIND_PREDICATE} "
               f"ORDER BY score DESC LIMIT ?")
        try:
            return store.read_sync(lambda c: rows(c.execute(sql, [text, country, conn, ctx.user_ref, kind, k])))
        except Exception:  # noqa: BLE001 — index not built yet → fall back
            pass
    terms = [t for t in {w.lower() for w in text.split()} if len(t) > 3][:12]
    if not terms:
        return []
    score_expr = " + ".join("CASE WHEN content ILIKE ? THEN 1 ELSE 0 END" for _ in terms)
    sql = (f"SELECT id, document_id, content, meta, ({score_expr}) AS score FROM doc_chunks WHERE {SCOPE_PREDICATE} "
           f"AND {KIND_PREDICATE} ORDER BY score DESC LIMIT ?")
    params = [f"%{t}%" for t in terms] + [country, conn, ctx.user_ref, kind, k]
    return [r for r in store.read_sync(lambda c: rows(c.execute(sql, params))) if r["score"] > 0]


# ----------------------------------------------------------------------------- admin-only metadata
def chat_metadata(store: AppStore, limit: int = 500) -> list[dict]:
    """Admins see metadata only — never content (TS §15.3 item 5)."""
    return store.fetchall_sync(
        "SELECT s.id AS session_id, u.user_id AS owner, s.created_at, s.updated_at, "
        "(SELECT count(*) FROM chat_messages m WHERE m.session_id = s.id) AS message_count "
        "FROM chat_sessions s LEFT JOIN users u ON u.id = s.owner_ref ORDER BY s.updated_at DESC LIMIT ?", [limit])


def detach_connection_from_sessions(c, connection_id: str) -> None:
    """Admin cascade when a connection is deleted (runs inside the caller's writer transaction)."""
    c.execute("UPDATE chat_sessions SET connection_id = NULL WHERE connection_id = ?", [connection_id])


def generation_success_rate(store: AppStore) -> dict:
    r = store.fetchall_sync(
        "SELECT count(*) FILTER (WHERE status = 'SUCCEEDED') AS ok, count(*) AS total FROM query_runs "
        "WHERE origin = 'AI' AND started_at >= now() - INTERVAL 7 DAY")
    fb = store.fetchall_sync("SELECT count(*) FILTER (WHERE feedback > 0) AS up, count(*) FILTER (WHERE feedback < 0) "
                             "AS down FROM chat_messages WHERE created_at >= now() - INTERVAL 7 DAY")
    ok, total = r[0]["ok"], r[0]["total"]
    return {"ai_runs_7d": total, "ai_success_rate_7d": round(ok / total, 3) if total else None,
            "thumbs_up_7d": fb[0]["up"], "thumbs_down_7d": fb[0]["down"]}
