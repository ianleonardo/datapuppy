"""Purge engine (TS §19.2). The ONLY code path allowed to DELETE from audit_log (CI lint rule)."""
from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ..core.config import get_config
from ..core.errors import ApiError
from ..store.duck import AppStore
from . import audit
from .settings import settings

BATCH = 10_000


def earliest_audit(store: AppStore) -> str | None:
    r = store.fetchall_sync("SELECT min(ts)::DATE AS d FROM audit_log")
    return str(r[0]["d"]) if r and r[0]["d"] else None


def preview(store: AppStore, scope: str, date_from: str | None, date_to: str) -> dict[str, Any]:
    out: dict[str, Any] = {"scope": scope, "date_to": date_to}
    if scope in ("AUDIT", "BOTH"):
        out["audit_from"] = earliest_audit(store)  # oldest-first only: `from` pinned to earliest record
        out["audit_rows"] = store.fetchall_sync(
            "SELECT count(*) AS n FROM audit_log WHERE ts < (?::DATE + INTERVAL 1 DAY) AND action <> 'PURGE_EXECUTED'",
            [date_to])[0]["n"]
    if scope in ("CHAT", "BOTH"):
        params = [date_from or "1970-01-01", date_to]
        r = store.fetchall_sync(
            "SELECT count(*) AS s, coalesce(sum((SELECT count(*) FROM chat_messages m WHERE m.session_id = cs.id)), 0) AS m "
            "FROM chat_sessions cs WHERE updated_at >= ?::DATE AND updated_at < (?::DATE + INTERVAL 1 DAY)", params)
        out.update({"chat_from": date_from, "chat_sessions": r[0]["s"], "chat_messages": int(r[0]["m"])})
    return out


def execute(store: AppStore, scope: str, date_from: str | None, date_to: str, user_ref: str | None, user_id: str | None,
            progress=None) -> dict[str, Any]:
    if scope not in ("AUDIT", "CHAT", "BOTH"):
        raise ApiError("E-VAL-001", detail="scope must be AUDIT, CHAT or BOTH")
    date.fromisoformat(date_to)
    cfg = get_config()
    size_before = store.file_size()
    summary: dict[str, Any] = {"scope": scope, "date_to": date_to, "removed": {}, "remaining": {}}
    if scope in ("AUDIT", "BOTH"):
        removed = 0
        anchor = None
        while True:
            def unit(c):
                ids = [r[0] for r in c.execute(
                    "SELECT id FROM audit_log WHERE ts < (?::DATE + INTERVAL 1 DAY) AND action <> 'PURGE_EXECUTED' "
                    "ORDER BY id LIMIT ?", [date_to, BATCH]).fetchall()]
                if ids:
                    c.execute("DELETE FROM audit_log WHERE list_contains(?, id)", [ids])
                return len(ids)
            n = store.write_sync(unit)
            removed += n
            if progress:
                progress({"phase": "audit", "removed": removed})
            if n < BATCH:
                break
        first = store.fetchall_sync("SELECT id, prev_hash FROM audit_log ORDER BY id LIMIT 1")
        anchor = first[0]["prev_hash"] if first else None
        summary["removed"]["audit"] = removed
        summary["anchor_hash"] = anchor
        summary["mirror_files"] = _purge_mirror(cfg.log_dir / "audit", date_to)
    if scope in ("CHAT", "BOTH"):
        frm = date_from or "1970-01-01"
        sessions = [str(r["id"]) for r in store.fetchall_sync(
            "SELECT id FROM chat_sessions WHERE updated_at >= ?::DATE AND updated_at < (?::DATE + INTERVAL 1 DAY)",
            [frm, date_to])]
        msgs = 0
        for i in range(0, len(sessions), 500):
            ids = sessions[i:i + 500]

            def unit(c, ids=ids):
                n = c.execute("SELECT count(*) FROM chat_messages WHERE list_contains(?, CAST(session_id AS VARCHAR))",
                              [ids]).fetchone()[0]
                c.execute("UPDATE query_runs SET session_id = NULL, message_id = NULL WHERE "
                          "list_contains(?, CAST(session_id AS VARCHAR))", [ids])
                c.execute("DELETE FROM chat_messages WHERE list_contains(?, CAST(session_id AS VARCHAR))", [ids])
                c.execute("DELETE FROM chat_sessions WHERE list_contains(?, CAST(id AS VARCHAR))", [ids])
                return n
            msgs += store.write_sync(unit)
            if progress:
                progress({"phase": "chat", "sessions": min(i + 500, len(sessions)), "total": len(sessions)})
        summary["removed"].update({"chat_sessions": len(sessions), "chat_messages": msgs})
    store.checkpoint()
    summary["remaining"] = {
        "audit": store.fetchall_sync("SELECT count(*) AS n FROM audit_log")[0]["n"],
        "chat_sessions": store.fetchall_sync("SELECT count(*) AS n FROM chat_sessions")[0]["n"],
        "chat_messages": store.fetchall_sync("SELECT count(*) AS n FROM chat_messages")[0]["n"]}
    summary["db_size_before"] = size_before
    summary["db_size_after"] = store.file_size()
    summary["disk_free_bytes"] = shutil.disk_usage(cfg.data_dir).free
    summary["note"] = "DuckDB doesn't shrink files on delete — use 'Compact database' to reclaim space."
    audit.record_sync(store, action="PURGE_EXECUTED", user_ref=user_ref, user_id=user_id,
                      context={k: v for k, v in summary.items() if k != "note"})
    return summary


def _purge_mirror(d: Path, date_to: str) -> dict[str, int]:
    mode = settings.get("audit.purge_mirror_files", "delete")
    cutoff = date_to.replace("-", "")
    moved = 0
    for f in d.glob("audit-*.jsonl"):
        day = f.stem.split("-")[-1]
        if day <= cutoff:
            if mode == "move":
                (d / "purged").mkdir(exist_ok=True)
                f.replace(d / "purged" / f.name)
            else:
                f.unlink(missing_ok=True)
            moved += 1
    return {mode: moved}


def compact_pending_marker() -> Path:
    return get_config().data_dir / "run" / "compact.pending"


def request_compaction() -> None:
    compact_pending_marker().write_text(datetime.now(timezone.utc).isoformat())


def compact_if_requested(db_path: Path) -> bool:
    """Offline COPY FROM DATABASE to a new file and swap — runs during the next start, before the store opens."""
    marker = compact_pending_marker()
    if not marker.exists():
        return False
    import duckdb

    tmp = db_path.with_suffix(".compact.duckdb")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(":memory:")
    try:
        con.execute(f"ATTACH '{db_path}' AS src")
        con.execute(f"ATTACH '{tmp}' AS dst")
        con.execute("COPY FROM DATABASE src TO dst")
        con.execute("DETACH src")
        con.execute("DETACH dst")
    finally:
        con.close()
    backup = db_path.with_suffix(".precompact.duckdb")
    db_path.replace(backup)
    tmp.replace(db_path)
    backup.unlink(missing_ok=True)
    marker.unlink(missing_ok=True)
    return True
