"""Schema discovery, classification and governance (TS §10)."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import date
from typing import Any

from ..core.errors import ApiError
from ..store.duck import AppStore, rows
from .settings import settings

log = logging.getLogger("sqlassist.schema")

# ----------------------------------------------------------------------------- Appendix C
SMELL_PATTERNS: list[dict[str, str]] = [
    {"id": "SM-01", "pattern": r"(_|^)(BCK|BAK|BKP|BACKUP)(_|$)", "cls": "BACKUP", "sev": "HIGH"},
    {"id": "SM-02", "pattern": r"(_|^)(TMP|TEMP)(_|$)", "cls": "TEMP", "sev": "HIGH"},
    {"id": "SM-03", "pattern": r"^OLD_|_OLD$", "cls": "ARCHIVE", "sev": "HIGH"},
    {"id": "SM-04", "pattern": r"_(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])$", "cls": "BACKUP", "sev": "MEDIUM"},
    {"id": "SM-05", "pattern": r"_(19|20)\d{2}(0[1-9]|1[0-2])$", "cls": "ARCHIVE", "sev": "MEDIUM"},
    {"id": "SM-06", "pattern": r"(_|^)(COPY|CPY|DUP)\d*(_|$)", "cls": "BACKUP", "sev": "MEDIUM"},
    {"id": "SM-07", "pattern": r"(_|^)(TEST|TST|DEBUG|SCRATCH)(_|$)", "cls": "TEMP", "sev": "MEDIUM"},
    {"id": "SM-08", "pattern": r"(_|^)(DEPRECATED|DEPR|UNUSED|DO_NOT_USE|DNU)(_|$)", "cls": "ARCHIVE", "sev": "HIGH"},
    {"id": "SM-09", "pattern": r"(_|^)(ARCH|ARCHIVE)(_|$)", "cls": "ARCHIVE", "sev": "MEDIUM"},
    {"id": "SM-10", "pattern": r"^BIN\$", "cls": "TEMP", "sev": "HIGH"},
]

SENSITIVITY_PATTERNS = {
    "RESTRICTED": ["NRIC", "FIN", "SSN", "PASSPORT", "CARD_NO", "CARDNO", "ACCOUNT_NO", "ACCT_NO", "DOB",
                   "DATE_OF_BIRTH", "SALARY", "PASSWORD", "PIN"],
    "SENSITIVE": ["EMAIL", "PHONE", "MOBILE", "ADDRESS", "NAME", "POSTAL", "ZIP"],
}


def _valid_date_suffix(name: str) -> bool:
    m = re.search(r"_((?:19|20)\d{2})(\d{2})(\d{2})$", name)
    if not m:
        return False
    try:
        date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return True
    except ValueError:
        return False


def detect_smells(name: str, patterns: list[dict] | None = None) -> tuple[list[str], str | None, str | None]:
    """Returns (flags, classification, highest severity)."""
    flags, cls, sev = [], None, None
    for p in patterns or settings.get("schema.smell_patterns") or SMELL_PATTERNS:
        if re.search(p["pattern"], name, re.I):
            if p["id"] == "SM-04" and not _valid_date_suffix(name):
                continue
            flags.append(p["id"])
            if sev != "HIGH":
                cls, sev = p["cls"], p["sev"]
    return flags, cls, sev


def base_table_hint(name: str, all_names: set[str]) -> str | None:
    """Sibling detection: X_BCK → X ("Looks like a backup of X")."""
    stripped = re.sub(r"(_(BCK|BAK|BKP|BACKUP|TMP|TEMP|OLD|COPY\d*|CPY\d*|DUP\d*|ARCH|ARCHIVE|TEST|TST|"
                      r"(19|20)\d{6}|(19|20)\d{4}))+$", "", name, flags=re.I)
    stripped = re.sub(r"^(OLD|TMP|TEMP|DNU|TEST)_", "", stripped, flags=re.I)
    if stripped and stripped.lower() != name.lower():
        for n in all_names:
            if n.lower() == stripped.lower():
                return n
    return None


def classify(table: dict[str, Any], fk_from: int, fk_to: int) -> tuple[str, float]:
    """TS §10.3 deterministic heuristic; highest score ≥ 0.5 wins."""
    name = table["name"].upper()
    cols = table["columns"]
    measures = sum(1 for c in cols if re.search(r"(AMOUNT|AMT|QTY|QUANTITY|PRICE|TOTAL|REVENUE|COST|BALANCE|COUNT)",
                                               c["name"].upper()) and re.search(r"(INT|NUM|DEC|DOUBLE|FLOAT|REAL|MONEY)",
                                                                                 str(c["type"]).upper()))
    dates = sum(1 for c in cols if re.search(r"(DATE|TIME)", str(c["type"]).upper()))
    scores = {"FACT": 0.0, "DIMENSION": 0.0, "STAGING": 0.0}
    if re.match(r"^(FACT|FCT|F)_", name) or re.search(r"_(FACT|FCT)$", name):
        scores["FACT"] += 0.7
    if re.match(r"^(DIM|D)_", name) or re.search(r"_DIM$", name):
        scores["DIMENSION"] += 0.7
    if re.match(r"^(STG|STAGE|STAGING|LND|RAW)_", name):
        scores["STAGING"] += 0.8
    if re.search(r"(ORDER|TRANSACTION|TXN|PAYMENT|SALE|EVENT|INVOICE|LEDGER|LOG)S?$", name):
        scores["FACT"] += 0.3
    if re.search(r"(CUSTOMER|PRODUCT|COUNTRY|REGION|BRANCH|CURRENCY|CATEGORY|USER|ACCOUNT|EMPLOYEE)S?$", name):
        scores["DIMENSION"] += 0.3
    scores["FACT"] += min(0.3, 0.1 * measures) + (0.1 if dates else 0) + min(0.2, 0.1 * fk_from)
    scores["DIMENSION"] += min(0.4, 0.2 * fk_to) + (0.1 if fk_from == 0 and measures == 0 else 0)
    best = max(scores, key=scores.get)
    val = round(min(scores[best], 0.99), 2)
    return (best, val) if val >= 0.5 else ("UNKNOWN", val)


def column_sensitivity(name: str) -> str:
    up = name.upper()
    tokens = set(re.split(r"[^A-Z0-9]+", up))
    for level in ("RESTRICTED", "SENSITIVE"):
        for p in settings.get("schema.sensitivity_patterns", SENSITIVITY_PATTERNS).get(level, []):
            if p in tokens or up == p or up.endswith("_" + p) or up.startswith(p + "_"):
                return level
    return "INTERNAL"


# ----------------------------------------------------------------------------- crawl (job task body)
def crawl(store: AppStore, connection: dict[str, Any], adapter, job_id: str, include_schemas: list[str] | None,
          progress) -> dict[str, Any]:
    conn_id = str(connection["id"])
    version = store.fetchall_sync("SELECT coalesce(max(version), 0) + 1 AS v FROM schema_snapshots WHERE connection_id = ?",
                                  [conn_id])[0]["v"]
    snap_id = str(uuid.uuid4())
    store.write_sync(lambda c: c.execute("INSERT INTO schema_snapshots(id, connection_id, version, state, job_id) "
                                         "VALUES (?, ?, ?, 'CRAWLING', ?)", [snap_id, conn_id, version, job_id]))
    try:
        progress({"phase": "introspecting"})
        dump = adapter.introspect(include_schemas)
        max_tables = int(settings.get("schema.max_tables", 5000))
        truncated = len(dump.tables) > max_tables
        tables = dump.tables[:max_tables]
        prev = _previous_decisions(store, conn_id)
        names = {t["name"] for t in tables}
        fk_from: dict[str, int] = {}
        fk_to: dict[str, int] = {}
        for fk in dump.fks:
            fk_from[fk["from_table"]] = fk_from.get(fk["from_table"], 0) + 1
            fk_to[fk["to_table"]] = fk_to.get(fk["to_table"], 0) + 1
        id_by_name: dict[tuple, str] = {}
        stats = {"tables": 0, "views": 0, "pending": 0, "excluded": 0, "carried_over": 0, "truncated": truncated}
        batch = 500
        for i in range(0, len(tables), batch):
            chunk = tables[i:i + batch]

            def unit(c, chunk=chunk):
                for t in chunk:
                    tid = str(uuid.uuid4())
                    id_by_name[(t["schema"], t["name"])] = tid
                    flags, smell_cls, sev = detect_smells(t["name"])
                    cls, conf = classify(t, fk_from.get(t["name"], 0), fk_to.get(t["name"], 0))
                    if smell_cls:
                        cls, conf = smell_cls, 0.9
                    inclusion = "EXCLUDED" if sev == "HIGH" else "PENDING_REVIEW" if sev == "MEDIUM" else "INCLUDED"
                    old = prev.get((t["schema"], t["name"]))
                    alias = desc = None
                    decided_by = None
                    if old:  # carry-over by name
                        inclusion = old["inclusion"] if old["inclusion"] != "PENDING_REVIEW" else inclusion
                        alias, desc, decided_by = old["alias"], old["description"], old["decided_by"]
                        if old["classification_overridden"]:
                            cls = old["classification"]
                        stats["carried_over"] += 1
                    stats["tables" if t["type"] == "TABLE" else "views"] += 1
                    stats["pending"] += inclusion == "PENDING_REVIEW"
                    stats["excluded"] += inclusion == "EXCLUDED"
                    c.execute("INSERT INTO schema_tables(id, snapshot_id, schema_name, table_name, object_type, alias, "
                              "description, classification, class_confidence, smell_flags, base_table_hint, inclusion, "
                              "decided_by, row_estimate) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              [tid, snap_id, t["schema"], t["name"], t["type"], alias, desc or t.get("comment"), cls,
                               conf, flags, base_table_hint(t["name"], names) if flags else None, inclusion, decided_by,
                               t.get("row_estimate")])
                    oldcols = (old or {}).get("columns", {})
                    for col in t["columns"]:
                        oc = oldcols.get(col["name"])
                        c.execute("INSERT INTO schema_columns(table_id, ordinal, column_name, data_type, is_nullable, is_pk, "
                                  "alias, description, sensitivity, is_masked) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                  [tid, col["ordinal"], col["name"], str(col["type"]), col["nullable"], col["pk"],
                                   oc["alias"] if oc else None, (oc["description"] if oc else None) or col.get("comment"),
                                   oc["sensitivity"] if oc else column_sensitivity(col["name"]),
                                   oc["is_masked"] if oc else False])
            store.write_sync(unit)
            progress({"phase": "classifying", "done": min(i + batch, len(tables)), "total": len(tables)})

        def rels(c):
            for fk in dump.fks:
                a = id_by_name.get((fk["from_schema"], fk["from_table"]))
                b = id_by_name.get((fk["to_schema"], fk["to_table"])) or next(
                    (v for (s, n), v in id_by_name.items() if n == fk["to_table"]), None)
                if a and b:
                    c.execute("INSERT INTO schema_relationships(snapshot_id, from_table_id, from_columns, to_table_id, "
                              "to_columns, source, name) VALUES (?,?,?,?,?, 'FK_CONSTRAINT', ?)",
                              [snap_id, a, fk["from_cols"], b, fk["to_cols"], fk.get("name")])
            for r in _previous_admin_relationships(c, conn_id):
                a, b = id_by_name.get((r["fs"], r["ft"])), id_by_name.get((r["ts"], r["tt"]))
                if a and b:
                    c.execute("INSERT INTO schema_relationships(snapshot_id, from_table_id, from_columns, to_table_id, "
                              "to_columns, source, name) VALUES (?,?,?,?,?, 'ADMIN_DEFINED', ?)",
                              [snap_id, a, r["fc"], b, r["tc"], r["name"]])
            stats["relationships"] = len(dump.fks)
            c.execute("UPDATE schema_snapshots SET state = 'DRAFT', stats = ? WHERE id = ?", [json.dumps(stats), snap_id])
        store.write_sync(rels)
        return {"snapshot_id": snap_id, "version": version, **stats}
    except Exception as e:
        store.write_sync(lambda c: c.execute("UPDATE schema_snapshots SET state = 'FAILED', stats = ? WHERE id = ?",
                                             [json.dumps({"error": str(e)[:300]}), snap_id]))
        raise


def _previous_decisions(store: AppStore, conn_id: str) -> dict[tuple, dict]:
    snap = store.fetchall_sync("SELECT id FROM schema_snapshots WHERE connection_id = ? AND state IN ('LOCKED','DRAFT') "
                               "ORDER BY (state = 'LOCKED') DESC, version DESC LIMIT 1", [conn_id])
    if not snap:
        return {}
    sid = str(snap[0]["id"])
    out: dict[tuple, dict] = {}
    for t in store.fetchall_sync("SELECT id, schema_name, table_name, alias, description, inclusion, decided_by, "
                                 "classification, decided_at IS NOT NULL AS classification_overridden FROM schema_tables "
                                 "WHERE snapshot_id = ?", [sid]):
        t["columns"] = {}
        out[(t["schema_name"], t["table_name"])] = t
    by_id = {str(v["id"]): v for v in out.values()}
    for c in store.fetchall_sync("SELECT c.table_id, c.column_name, c.alias, c.description, c.sensitivity, c.is_masked "
                                 "FROM schema_columns c JOIN schema_tables t ON t.id = c.table_id WHERE t.snapshot_id = ?", [sid]):
        by_id[str(c["table_id"])]["columns"][c["column_name"]] = c
    return out


def _previous_admin_relationships(c, conn_id: str) -> list[dict]:
    return rows(c.execute(
        "SELECT ft.schema_name AS fs, ft.table_name AS ft, r.from_columns AS fc, tt.schema_name AS ts, tt.table_name AS tt, "
        "r.to_columns AS tc, r.name FROM schema_relationships r JOIN schema_tables ft ON ft.id = r.from_table_id "
        "JOIN schema_tables tt ON tt.id = r.to_table_id JOIN schema_snapshots s ON s.id = r.snapshot_id "
        "WHERE s.connection_id = ? AND r.source = 'ADMIN_DEFINED' AND s.state = 'LOCKED'", [conn_id]))


# ----------------------------------------------------------------------------- reads
def locked_snapshot(store: AppStore, conn_id: str) -> dict | None:
    r = store.fetchall_sync("SELECT * FROM schema_snapshots WHERE connection_id = ? AND state = 'LOCKED'", [conn_id])
    return r[0] if r else None


def snapshot_tables(store: AppStore, snap_id: str, included_only: bool = False) -> list[dict]:
    sql = ("SELECT id, schema_name, table_name, object_type, alias, description, classification, class_confidence, "
           "smell_flags, base_table_hint, inclusion, row_estimate FROM schema_tables WHERE snapshot_id = ?")
    if included_only:
        sql += " AND inclusion = 'INCLUDED'"
    return store.fetchall_sync(sql + " ORDER BY schema_name, table_name", [snap_id])


def table_columns(store: AppStore, table_ids: list[str]) -> dict[str, list[dict]]:
    if not table_ids:
        return {}
    out: dict[str, list[dict]] = {}
    for c in store.fetchall_sync("SELECT id, table_id, ordinal, column_name, data_type, is_nullable, is_pk, alias, "
                                 "description, sensitivity, is_masked FROM schema_columns WHERE list_contains(?, "
                                 "CAST(table_id AS VARCHAR)) ORDER BY ordinal", [table_ids]):
        out.setdefault(str(c["table_id"]), []).append(c)
    return out


def relationships(store: AppStore, snap_id: str) -> list[dict]:
    return store.fetchall_sync(
        "SELECT r.id, r.from_table_id, ft.table_name AS from_table, r.from_columns, r.to_table_id, tt.table_name AS to_table, "
        "r.to_columns, r.source, r.name FROM schema_relationships r JOIN schema_tables ft ON ft.id = r.from_table_id "
        "JOIN schema_tables tt ON tt.id = r.to_table_id WHERE r.snapshot_id = ?", [snap_id])


def catalog_for_preflight(store: AppStore, conn_id: str) -> tuple[dict[str, set[str]] | None, set[str]]:
    snap = locked_snapshot(store, conn_id)
    if not snap:
        return None, set()
    cat: dict[str, set[str]] = {}
    excluded: set[str] = set()
    for r in store.fetchall_sync(
            "SELECT t.schema_name, t.table_name, t.inclusion, list(lower(c.column_name)) AS cols FROM schema_tables t "
            "LEFT JOIN schema_columns c ON c.table_id = t.id WHERE t.snapshot_id = ? GROUP BY ALL", [str(snap["id"])]):
        cols = {c for c in (r["cols"] or []) if c}
        cat[r["table_name"].lower()] = cols
        cat[f"{r['schema_name'].lower()}.{r['table_name'].lower()}"] = cols
        if r["inclusion"] != "INCLUDED":
            excluded.add(r["table_name"].lower())
    return cat, excluded


# ----------------------------------------------------------------------------- cards & lock (TS §10.5)
def render_card(t: dict, cols: list[dict], rels: list[dict]) -> str:
    lines = [f"TABLE {t['schema_name']}.{t['table_name']}" + (f"  (alias: {t['alias']})" if t.get("alias") else ""),
             f"kind: {t['classification'].lower()}; rows≈{t['row_estimate'] or 'unknown'}"]
    if t.get("description"):
        lines.append(f"about: {t['description']}")
    lines.append("columns:")
    for c in cols:
        if c["is_masked"]:
            continue
        extra = []
        if c["is_pk"]:
            extra.append("PK")
        if c.get("alias"):
            extra.append(f"alias {c['alias']}")
        if c["sensitivity"] in ("SENSITIVE", "RESTRICTED"):
            extra.append(c["sensitivity"].lower())
        desc = f" — {c['description']}" if c.get("description") else ""
        lines.append(f"  - {c['column_name']} {c['data_type']}{(' [' + ', '.join(extra) + ']') if extra else ''}{desc}")
    joins = [f"  - {r['from_table']}({', '.join(r['from_columns'])}) → {r['to_table']}({', '.join(r['to_columns'])})"
             for r in rels if str(r["from_table_id"]) == str(t["id"]) or str(r["to_table_id"]) == str(t["id"])]
    if joins:
        lines.append("joins:")
        lines += joins
    return "\n".join(lines)


async def lock_snapshot(store: AppStore, snap_id: str, user_ref: str, embedder) -> dict[str, Any]:
    snap = await store.fetchone("SELECT * FROM schema_snapshots WHERE id = ?", [snap_id])
    if not snap:
        raise ApiError("E-NF-001")
    if snap["state"] != "DRAFT":
        raise ApiError("E-VAL-001", detail="Only a DRAFT snapshot can be locked.")
    pending = await store.fetchone("SELECT count(*) AS n FROM schema_tables WHERE snapshot_id = ? AND inclusion = "
                                   "'PENDING_REVIEW'", [snap_id])
    if pending["n"]:
        raise ApiError("E-SCH-001", pending=pending["n"])  # TR-SCH-03
    tables = snapshot_tables(store, snap_id, included_only=True)
    cols = table_columns(store, [str(t["id"]) for t in tables])
    rels = relationships(store, snap_id)
    cards = [(str(t["id"]), render_card(t, cols.get(str(t["id"]), []), rels)) for t in tables]
    digest = hashlib.sha256("\n\n".join(c for _, c in cards).encode()).hexdigest()
    vectors: list[list[float] | None] = [None] * len(cards)
    embedded = False
    dim = None
    if embedder is not None and cards:
        try:
            vectors = []
            for i in range(0, len(cards), 32):
                vectors += await embedder.embed([c for _, c in cards[i:i + 32]])
            embedded = True
            dim = len(vectors[0]) if vectors else None
        except Exception as e:  # noqa: BLE001 — degrade: keyword retrieval over cards
            log.warning("schema card embedding failed; locking without vectors (keyword search still works; check the primary embedding provider in Admin → LLM providers, then re-index): %s", e)
            vectors = [None] * len(cards)
    conn_id = str(snap["connection_id"])
    store_dim = _embed_dim(store)
    if embedded and dim != store_dim:
        log.warning("embedding dim %s != store dim %s; storing cards without vectors (re-index needed)", dim, store_dim)
        vectors = [None] * len(cards)
        embedded = False

    def unit(c):
        c.execute("UPDATE schema_snapshots SET state = 'SUPERSEDED' WHERE connection_id = ? AND state = 'LOCKED'", [conn_id])
        c.execute("DELETE FROM schema_cards WHERE connection_id = ?", [conn_id])
        for (tid, text), vec in zip(cards, vectors):
            c.execute(f"INSERT INTO schema_cards(table_id, connection_id, snapshot_id, card_text, embedding) "
                      f"VALUES (?, ?, ?, ?, ?::FLOAT[{store_dim}])", [tid, conn_id, snap_id, text, vec])
        stats = json.loads(snap["stats"]) if isinstance(snap["stats"], str) else (snap["stats"] or {})
        stats.update({"cards": len(cards), "embedded": embedded})
        c.execute("UPDATE schema_snapshots SET state = 'LOCKED', locked_by = ?, locked_at = now(), prompt_digest = ?, "
                  "stats = ? WHERE id = ?", [user_ref, digest, json.dumps(stats), snap_id])
    await store.write(unit)
    return {"snapshot_id": snap_id, "prompt_digest": digest, "cards": len(cards), "embedded": embedded}


def _embed_dim(store: AppStore) -> int:
    r = store.fetchall_sync("SELECT data_type FROM duckdb_columns() WHERE table_name = 'schema_cards' AND "
                            "column_name = 'embedding'")
    m = re.search(r"\[(\d+)\]", r[0]["data_type"]) if r else None
    return int(m.group(1)) if m else 1024


async def unlock_to_draft(store: AppStore, snap_id: str) -> str:
    """'Unlock to new draft' — clone v+1 as DRAFT (TS §10.1)."""
    snap = await store.fetchone("SELECT * FROM schema_snapshots WHERE id = ? AND state = 'LOCKED'", [snap_id])
    if not snap:
        raise ApiError("E-VAL-001", detail="Only the LOCKED snapshot can be unlocked.")
    new_id = str(uuid.uuid4())
    conn_id = str(snap["connection_id"])

    def unit(c):
        v = c.execute("SELECT max(version) + 1 FROM schema_snapshots WHERE connection_id = ?", [conn_id]).fetchone()[0]
        c.execute("DELETE FROM schema_snapshots WHERE connection_id = ? AND state = 'DRAFT'", [conn_id])
        c.execute("INSERT INTO schema_snapshots(id, connection_id, version, state, stats) VALUES (?, ?, ?, 'DRAFT', ?)",
                  [new_id, conn_id, v, snap["stats"]])
        mapping = {}
        for (tid,) in c.execute("SELECT id FROM schema_tables WHERE snapshot_id = ?", [snap_id]).fetchall():
            mapping[str(tid)] = str(uuid.uuid4())
        for old, new in mapping.items():
            c.execute("INSERT INTO schema_tables SELECT ?::UUID, ?::UUID, schema_name, table_name, object_type, alias, "
                      "description, classification, class_confidence, smell_flags, base_table_hint, inclusion, decided_by, "
                      "decided_at, row_estimate FROM schema_tables WHERE id = ?", [new, new_id, old])
            c.execute("INSERT INTO schema_columns SELECT uuid(), ?::UUID, ordinal, column_name, data_type, is_nullable, is_pk, "
                      "alias, description, sensitivity, is_masked FROM schema_columns WHERE table_id = ?", [new, old])
        for r in rows(c.execute("SELECT * FROM schema_relationships WHERE snapshot_id = ?", [snap_id])):
            a, b = mapping.get(str(r["from_table_id"])), mapping.get(str(r["to_table_id"]))
            if a and b:
                c.execute("INSERT INTO schema_relationships(snapshot_id, from_table_id, from_columns, to_table_id, to_columns, "
                          "source, name) VALUES (?,?,?,?,?,?,?)",
                          [new_id, a, r["from_columns"], b, r["to_columns"], r["source"], r["name"]])
    await store.write(unit)
    return new_id


def diff(store: AppStore, snap_id: str) -> dict[str, Any]:
    snap = store.fetchall_sync("SELECT connection_id, version FROM schema_snapshots WHERE id = ?", [snap_id])
    if not snap:
        raise ApiError("E-NF-001")
    prev = store.fetchall_sync("SELECT id FROM schema_snapshots WHERE connection_id = ? AND version < ? AND state IN "
                               "('LOCKED','SUPERSEDED') ORDER BY version DESC LIMIT 1",
                               [str(snap[0]["connection_id"]), snap[0]["version"]])
    if not prev:
        return {"added": [], "removed": [], "changed": [], "baseline": None}

    def sig(sid):
        out = {}
        for r in store.fetchall_sync("SELECT t.schema_name || '.' || t.table_name AS t, string_agg(c.column_name || ':' || "
                                     "c.data_type, ',' ORDER BY c.ordinal) AS s FROM schema_tables t LEFT JOIN schema_columns "
                                     "c ON c.table_id = t.id WHERE t.snapshot_id = ? GROUP BY 1", [sid]):
            out[r["t"]] = r["s"]
        return out
    a, b = sig(str(prev[0]["id"])), sig(snap_id)
    return {"baseline": str(prev[0]["id"]), "added": sorted(set(b) - set(a)), "removed": sorted(set(a) - set(b)),
            "changed": sorted(k for k in set(a) & set(b) if a[k] != b[k])}


# ----------------------------------------------------------------------------- Mermaid ERD (deterministic)
def _mm(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", s)


def mermaid_erd(store: AppStore, conn_id: str, only: list[str] | None = None, max_tables: int = 25,
                include_all: bool = False) -> tuple[str, list[str]]:
    snap = locked_snapshot(store, conn_id)
    if not snap:
        snap_rows = store.fetchall_sync("SELECT * FROM schema_snapshots WHERE connection_id = ? AND state = 'DRAFT' "
                                        "ORDER BY version DESC LIMIT 1", [conn_id]) if include_all else []
        if not snap_rows:
            raise ApiError("E-SCH-003")
        snap = snap_rows[0]
    sid = str(snap["id"])
    tables = snapshot_tables(store, sid, included_only=not include_all)
    if only:
        wanted = {o.lower() for o in only}
        tables = [t for t in tables if t["table_name"].lower() in wanted]
    tables = tables[:max_tables]
    ids = {str(t["id"]) for t in tables}
    cols = table_columns(store, list(ids))
    lines = ["erDiagram", "    direction LR"]
    for t in tables:
        lines.append(f"    {_mm(t['table_name'])} {{")
        for c in cols.get(str(t["id"]), [])[:14]:
            if c["is_masked"]:
                continue
            typ = _mm(re.sub(r"\(.*\)", "", str(c["data_type"])).split()[0] or "col")
            lines.append(f"        {typ} {_mm(c['column_name'])}{' PK' if c['is_pk'] else ''}")
        lines.append("    }")
    for r in relationships(store, sid):
        if str(r["from_table_id"]) in ids and str(r["to_table_id"]) in ids:
            label = _mm(",".join(r["from_columns"]))[:30] or "fk"
            lines.append(f"    {_mm(r['to_table'])} ||--o{{ {_mm(r['from_table'])} : {label}")
    return "\n".join(lines), [t["table_name"] for t in tables]
