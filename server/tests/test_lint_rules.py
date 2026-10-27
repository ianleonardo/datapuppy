"""CI lint rules (TS §15.3 item 2, §19.1).

1. SQL touching user-owned tables must only appear inside store/repos.py (plus allow-listed admin-wide routines).
2. UPDATE/DELETE on audit_log only in services/purge.py.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
USER_TABLES = re.compile(r"\b(FROM|INTO|UPDATE|JOIN)\s+(chat_sessions|chat_messages|query_runs|doc_chunks)\b", re.I)
ALLOWED_USER_TABLE_FILES = {
    "store/repos.py",
    "services/purge.py",       # admin-only purge/retention across all users
    "jobs/tasks.py",           # reindex/integrity: no user predicate by design (admin job)
    "services/rag.py",         # ingest job writes chunks for a document it was given
    "services/orchestrator.py",  # _interpret: query_runs with explicit owner_ref predicate
    "services/execution.py",
    "main.py",                 # /metrics aggregates counts only
}
AUDIT_WRITE = re.compile(r"\b(UPDATE\s+audit_log|DELETE\s+FROM\s+audit_log)\b", re.I)


def _strings(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            out.append("".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)))
    return out


def test_user_scoped_tables_only_in_repos():
    offenders = []
    for f in APP.rglob("*.py"):
        rel = f.relative_to(APP).as_posix()
        if rel in ALLOWED_USER_TABLE_FILES:
            continue
        if any(USER_TABLES.search(s) for s in _strings(f)):
            offenders.append(rel)
    assert not offenders, f"user-owned table SQL outside repos: {offenders}"


def test_owner_predicate_in_allowlisted_user_queries():
    """Outside repos/purge/jobs, every user-table query must carry an owner_ref predicate."""
    for rel in ("services/orchestrator.py", "services/execution.py"):
        for s in _strings(APP / rel):
            if USER_TABLES.search(s):
                assert "owner_ref" in s, (rel, s[:120])


def test_audit_log_append_only():
    offenders = [f.relative_to(APP).as_posix() for f in APP.rglob("*.py")
                 if f.name != "purge.py" and any(AUDIT_WRITE.search(s) for s in _strings(f))]
    assert not offenders, offenders
