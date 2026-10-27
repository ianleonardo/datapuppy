"""Audit writer: append-only by code path, hash-chained, mirrored to JSON lines (TS §19.1, DD-07).

The audit repository exposes ONLY `record` (append) and `query`. The CI lint rule
(tests/test_lint_rules.py) rejects UPDATE/DELETE on audit_log outside services/purge.py.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..store.duck import AppStore, rows

log = logging.getLogger("sqlassist.audit")
GENESIS = "0" * 64

ACTIONS = {
    "LOGIN", "LOGOUT", "PASSWORD_CHANGE", "USER_ADMIN", "DOCUMENT_UPLOAD", "DOCUMENT_DELETE",
    "DB_CONNECTION_CREATE", "DB_CONNECTION_EDIT", "DB_CONNECTION_DELETE", "DB_CONNECTION_TEST",
    "SCHEMA_CRAWL", "SCHEMA_EDIT", "SCHEMA_LOCK", "QUERY_EXECUTION", "QUERY_EXPORT",
    "LLM_CONFIG_CHANGE", "AUTH_CONFIG_CHANGE", "SETTINGS_CHANGE", "BACKUP_EXECUTED", "PURGE_EXECUTED",
}

_mirror_lock = threading.Lock()
_mirror_dir: Path | None = None


def init_mirror(log_dir: Path) -> None:
    global _mirror_dir
    _mirror_dir = log_dir / "audit"
    _mirror_dir.mkdir(parents=True, exist_ok=True)


def canonical(ts: str, user_ref: str | None, action: str, status: str, context: dict[str, Any]) -> str:
    return json.dumps({"ts": ts, "user_ref": user_ref, "action": action, "status": status, "context": context},
                      sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def chain_hash(prev_hash: str, canon: str) -> str:
    return hashlib.sha256((prev_hash + canon).encode()).hexdigest()


def append_in_txn(c, *, user_ref: str | None, user_id: str | None, action: str, status: str,
                  context: dict[str, Any]) -> dict[str, Any]:
    """Append one audit row using the writer-thread cursor `c` (caller holds the transaction)."""
    assert action in ACTIONS, action
    assert status in ("SUCCESS", "FAILED", "INTERCEPTED"), status
    last = c.execute("SELECT row_hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    prev = last[0] if last else GENESIS
    ts = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    ctx = json.loads(json.dumps(context, default=str))
    row_hash = chain_hash(prev, canonical(ts, user_ref, action, status, ctx))
    new_id = c.execute(
        "INSERT INTO audit_log(ts, user_ref, user_id, action, status, context, prev_hash, row_hash) "
        "VALUES (?::TIMESTAMPTZ, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
        [ts, user_ref, user_id, action, status, json.dumps(ctx, default=str), prev, row_hash]).fetchone()[0]
    rec = {"id": new_id, "ts": ts, "user_ref": user_ref, "user_id": user_id, "action": action,
           "status": status, "context": ctx, "prev_hash": prev, "row_hash": row_hash}
    return rec


def mirror(rec: dict[str, Any]) -> None:
    if _mirror_dir is None:
        return
    day = rec["ts"][:10].replace("-", "")
    with _mirror_lock, (_mirror_dir / f"audit-{day}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, default=str, ensure_ascii=False) + "\n")


async def record(store: AppStore, *, action: str, status: str = "SUCCESS", context: dict[str, Any] | None = None,
                 user_ref: str | None = None, user_id: str | None = None) -> dict[str, Any]:
    rec = await store.write(lambda c: append_in_txn(c, user_ref=user_ref, user_id=user_id, action=action,
                                                    status=status, context=context or {}))
    mirror(rec)
    return rec


def record_sync(store: AppStore, **kw) -> dict[str, Any]:
    rec = store.write_sync(lambda c: append_in_txn(c, user_ref=kw.get("user_ref"), user_id=kw.get("user_id"),
                                                   action=kw["action"], status=kw.get("status", "SUCCESS"),
                                                   context=kw.get("context") or {}))
    mirror(rec)
    return rec


def query(store: AppStore, *, action: str | None = None, status: str | None = None, user_id: str | None = None,
          date_from: str | None = None, date_to: str | None = None, limit: int = 200,
          before_id: int | None = None) -> list[dict[str, Any]]:
    where, params = [], []
    for col, val in (("action", action), ("status", status), ("user_id", user_id)):
        if val:
            where.append(f"{col} = ?")
            params.append(val)
    if date_from:
        where.append("ts >= ?::TIMESTAMPTZ")
        params.append(date_from)
    if date_to:
        where.append("ts < (?::DATE + INTERVAL 1 DAY)")
        params.append(date_to)
    if before_id:
        where.append("id < ?")
        params.append(before_id)
    sql = "SELECT id, ts, user_id, action, status, context, row_hash FROM audit_log"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    out = store.fetchall_sync(sql, params)
    for r in out:
        if isinstance(r["context"], str):
            r["context"] = json.loads(r["context"])
    return out


def verify_chain(store: AppStore, log_dir: Path | None = None) -> dict[str, Any]:
    """Re-compute the chain. The first surviving row may follow a purge anchor (TS §19.2)."""
    def fetch(c):
        return rows(c.execute("SELECT id, ts, user_ref, action, status, context, prev_hash, row_hash "
                              "FROM audit_log ORDER BY id"))
    data = store.read_sync(fetch)
    problems: list[str] = []
    prev = None
    for r in data:
        ts = r["ts"].astimezone(timezone.utc).isoformat(timespec="microseconds") if isinstance(r["ts"], datetime) else str(r["ts"])
        ctx = json.loads(r["context"]) if isinstance(r["context"], str) else r["context"]
        if prev is not None and r["prev_hash"] != prev:
            problems.append(f"broken link at id={r['id']}")
        expected = chain_hash(r["prev_hash"], canonical(ts, str(r["user_ref"]) if r["user_ref"] else None,
                                                         r["action"], r["status"], ctx))
        if expected != r["row_hash"]:
            problems.append(f"hash mismatch at id={r['id']}")
        prev = r["row_hash"]
    mirror_checked = 0
    if log_dir is not None and data:
        hashes = {r["row_hash"] for r in data}
        seen = set()
        for f in sorted((log_dir / "audit").glob("audit-*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    seen.add(json.loads(line)["row_hash"])
                except (ValueError, KeyError):
                    problems.append(f"unreadable mirror line in {f.name}")
        missing = [h for h in hashes if h not in seen]
        mirror_checked = len(hashes)
        if missing:
            problems.append(f"{len(missing)} DB rows missing from file mirror")
    return {"ok": not problems, "rows": len(data), "mirror_checked": mirror_checked, "problems": problems[:50]}
