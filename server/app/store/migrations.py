"""Versioned SQL migration runner (TS §4: Alembic has no DuckDB support).

Files: server/migrations/NNNN_name.sql. `{embed_dim}` is substituted with the deployment's
embedding dimension (TS §7.1). Statements are split on lines that end with ';'.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from .duck import AppStore

log = logging.getLogger("sqlassist.store")
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
_NAME = re.compile(r"^(\d{4})_(.+)\.sql$")


def split_statements(sql: str) -> list[str]:
    out, buf = [], []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") and not buf:
            continue
        buf.append(line)
        if stripped.endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt.strip(";").strip():
                out.append(stmt)
            buf = []
    tail = "\n".join(buf).strip()
    if tail and not all(l.strip().startswith("--") or not l.strip() for l in buf):
        out.append(tail)
    return out


def pending(store: AppStore) -> list[tuple[int, str, Path]]:
    store.write_sync(lambda c: c.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name VARCHAR, "
        "applied_at TIMESTAMPTZ DEFAULT now())"), transactional=False)
    applied = {r["version"] for r in store.fetchall_sync("SELECT version FROM schema_migrations")}
    todo = []
    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
        m = _NAME.match(f.name)
        if m and int(m.group(1)) not in applied:
            todo.append((int(m.group(1)), m.group(2), f))
    return todo


def apply_migrations(store: AppStore, embed_dim: int = 1024) -> list[int]:
    todo = pending(store)
    done = []
    for version, name, path in todo:
        sql = path.read_text(encoding="utf-8").replace("{embed_dim}", str(int(embed_dim)))

        def unit(c, sql=sql, version=version, name=name):
            for stmt in split_statements(sql):
                c.execute(stmt)
            c.execute("INSERT INTO schema_migrations(version, name) VALUES (?, ?)", [version, name])

        store.write_sync(unit)
        log.info("applied migration %04d_%s", version, name)
        done.append(version)
    return done
