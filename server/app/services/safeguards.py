"""Safeguards engine: static validation, static pre-flight and SQL wrappers (TS §12.2, §13.2, §14.1).

Read-only is enforced in three layers (DD-01): this parser allow-list, a read-only transaction in
every connector, and least-privilege DB accounts.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from ..core.errors import ApiError

DIALECTS = {"ORACLE": "oracle", "MARIADB": "mysql", "POSTGRES": "postgres", "DUCKDB": "duckdb"}

DENY_FUNCS: dict[str, list[str]] = {
    "oracle": [r"^DBMS_", r"^UTL_", r"^SYS\.DBMS_", r"^SYS\.UTL_"],
    "postgres": [r"^PG_SLEEP", r"^PG_READ_FILE", r"^PG_READ_BINARY_FILE", r"^PG_LS_DIR", r"^DBLINK", r"^LO_",
                 r"^PG_TERMINATE_BACKEND", r"^PG_CANCEL_BACKEND", r"^SET_CONFIG$", r"^PG_WRITE"],
    "mysql": [r"^SLEEP$", r"^BENCHMARK$", r"^LOAD_FILE$", r"^GET_LOCK$", r"^RELEASE_LOCK$"],
    "duckdb": [r"^READ_", r"^GLOB$", r"^COPY$", r"^ATTACH$", r"^INSTALL$", r"^LOAD$", r"^HTTP", r"^SNIFF_CSV$",
               r"^PARQUET_", r"^QUERY$", r"^QUERY_TABLE$", r"^SQLITE_", r"^POSTGRES_", r"^MYSQL_", r"^ICEBERG_",
               r"^DELTA_SCAN$", r"^SETSEED$", r"^GETENV$", r"^CURRENT_SETTING$"],
}

_FORBIDDEN_NAMES = ("Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "Command", "Transaction",
                    "Commit", "Rollback", "Into", "Lock", "Set", "Use", "Pragma", "TruncateTable", "Copy", "Attach",
                    "Detach", "Install", "LoadData", "Grant", "Revoke", "Kill", "Analyze")
FORBIDDEN = tuple(getattr(exp, n) for n in _FORBIDDEN_NAMES if hasattr(exp, n))
WRITE_KEYWORDS = re.compile(r"^\s*(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|CALL|EXEC|EXECUTE|"
                            r"BEGIN|COMMIT|ROLLBACK|COPY|ATTACH|DETACH|INSTALL|LOAD|PRAGMA|SET|USE|VACUUM|CHECKPOINT)\b", re.I)


_FILE_LIKE = re.compile(r"[/\\:*?]|\.(csv|tsv|parquet|json|ndjson|jsonl|txt|xlsx?|db|duckdb|sqlite3?|gz|zst|arrow|avro)$",
                        re.I)


@dataclass
class Issue:
    line: int
    col: int
    severity: str  # error | warning | info
    message: str
    end_col: int | None = None
    code: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"line": self.line, "col": self.col, "end_col": self.end_col, "severity": self.severity,
                "message": self.message, "code": self.code}


@dataclass
class Validated:
    ast: exp.Expression
    dialect: str
    sql: str
    warnings: list[Issue] = field(default_factory=list)
    destructive: bool = False


def sql_hash(sql: str) -> str:
    return hashlib.sha256(" ".join(sql.split()).encode()).hexdigest()


def _strip_trailing(sql: str) -> str:
    return sql.strip().rstrip(";").strip()


def _func_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).upper()
    try:
        return node.sql_name().upper()
    except Exception:  # noqa: BLE001
        return type(node).__name__.upper()


def _parse_error_issue(e: Exception) -> Issue:
    if isinstance(e, ParseError) and e.errors:
        err = e.errors[0]
        return Issue(int(err.get("line") or 1), int(err.get("col") or 1), "error",
                     str(err.get("description") or "syntax error"), code="E-EXE-002")
    msg = str(e).splitlines()[0][:200]
    m = re.search(r"[Ll]ine (\d+), [Cc]ol(?:umn)?:? (\d+)", str(e))
    return Issue(int(m.group(1)) if m else 1, int(m.group(2)) if m else 1, "error", msg, code="E-EXE-002")


def parse_one(sql: str, dialect: str) -> exp.Expression:
    text = _strip_trailing(sql)
    if not text:
        raise ApiError("E-EXE-001")
    try:
        statements = [s for s in sqlglot.parse(text, read=dialect) if s is not None]
    except (ParseError, TokenError) as e:
        iss = _parse_error_issue(e)
        raise ApiError("E-EXE-002", l=iss.line, c=iss.col, msg=iss.message, issues=[iss.as_dict()]) from e
    if len(statements) != 1:
        raise ApiError("E-EXE-001")
    return statements[0]


def validate(sql: str, engine: str) -> Validated:
    """TR-EXE-01: exactly one SELECT / set-op / WITH…SELECT; no DML/DDL/commands; function deny-list."""
    dialect = DIALECTS[engine]
    if WRITE_KEYWORDS.match(sql or ""):
        raise ApiError("E-EXE-001", destructive=True)
    ast = parse_one(sql, dialect)
    if dialect == "oracle":  # sqlglot normalises some package calls (DBMS_RANDOM.VALUE → RAND); the raw text executes
        m = re.search(r"\b((?:SYS\s*\.\s*)?(?:DBMS_|UTL_)\w*)", re.sub(r"'(?:[^']|'')*'", "''", sql), re.I)
        if m:
            raise ApiError("E-EXE-003", fn=m.group(1).upper())
    root = ast
    if not isinstance(root, (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)):
        raise ApiError("E-EXE-001", destructive=isinstance(root, FORBIDDEN))
    for node in root.walk():
        node = node[0] if isinstance(node, tuple) else node
        if isinstance(node, FORBIDDEN):
            raise ApiError("E-EXE-001", destructive=True)
        if isinstance(node, exp.Func):
            name = _func_name(node)
            for pat in DENY_FUNCS.get(dialect, []):
                if re.match(pat, name):
                    raise ApiError("E-EXE-003", fn=name)
        if dialect == "oracle" and isinstance(node, exp.Identifier) and re.match(r"^(DBMS_|UTL_)", str(node.name).upper()):
            raise ApiError("E-EXE-003", fn=str(node.name).upper())  # package calls parse as dotted names
        if dialect == "duckdb" and isinstance(node, exp.Table) and (
                isinstance(node.this, exp.Literal) or _FILE_LIKE.search(str(node.name or ""))):
            raise ApiError("E-EXE-003", fn="file scan")  # FROM 'file.csv' / '/path' replacement scans
    return Validated(ast=ast, dialect=dialect, sql=_strip_trailing(sql))


# ----------------------------------------------------------------------------- pre-flight (stage 1, static)
def static_preflight(sql: str, engine: str, catalog: dict[str, set[str]] | None = None,
                     excluded: set[str] | None = None) -> dict[str, Any]:
    """Returns {ok, issues[], corrected_sql?}. Never touches the target DB (TS §13.2).

    catalog maps lower-case table name (and schema.table) → set of lower-case column names.
    """
    dialect = DIALECTS[engine]
    issues: list[Issue] = []
    corrected = None
    lines = (sql or "").splitlines() or [""]

    def locate(token: str) -> tuple[int, int]:
        pat = re.compile(rf"\b{re.escape(token)}\b", re.I)
        for i, ln in enumerate(lines, 1):
            m = pat.search(ln)
            if m:
                return i, m.start() + 1
        return 1, 1

    if dialect == "oracle" and re.search(r"\bLIMIT\s+\d+", sql or "", re.I):
        ln, col = locate("LIMIT")
        issues.append(Issue(ln, col, "warning", "Oracle doesn't support LIMIT — use FETCH FIRST n ROWS ONLY.", code="DIALECT"))
        try:
            corrected = sqlglot.transpile(_strip_trailing(sql), read="postgres", write="oracle", pretty=True)[0]
        except Exception:  # noqa: BLE001
            pass
    try:
        v = validate(sql, engine)
    except ApiError as e:
        if e.code == "E-EXE-002":
            issues.extend(Issue(**{k: i[k] for k in ("line", "col", "severity", "message")}) for i in e.extra.get("issues", []))
        else:
            ln, col = 1, 1
            m = WRITE_KEYWORDS.match(sql or "")
            if m:
                ln, col = locate(m.group(1))
            issues.append(Issue(ln, col, "error", e.message, code=e.code))
        return {"ok": False, "issues": [i.as_dict() for i in issues], "corrected_sql": corrected,
                "destructive": bool(e.extra.get("destructive"))}

    ast = v.ast
    cte_names = {c.alias_or_name.lower() for c in ast.find_all(exp.CTE)}
    tables = list(ast.find_all(exp.Table))
    alias_map: dict[str, str] = {}
    for t in tables:
        name = t.name.lower()
        full = f"{t.db.lower()}.{name}" if t.db else name
        alias_map[(t.alias_or_name or name).lower()] = full if full in (catalog or {}) else name
        if catalog is not None and name not in cte_names and full not in catalog and name not in catalog:
            ln, col = locate(t.name)
            issues.append(Issue(ln, col, "error", f"I can't find a table called {t.name} in the approved schema.",
                                end_col=col + len(t.name), code="UNKNOWN_TABLE"))
        if excluded and (name in excluded or full in excluded):
            ln, col = locate(t.name)
            issues.append(Issue(ln, col, "warning", f"{t.name} is excluded from the governed schema.", code="EXCLUDED_TABLE"))
    # derived tables without alias
    for sub in ast.find_all(exp.Subquery):
        if isinstance(sub.parent, (exp.From, exp.Join)) and not sub.alias:
            issues.append(Issue(1, 1, "warning" if dialect != "postgres" else "error",
                                "This derived table needs an alias.", code="MISSING_ALIAS"))
    # columns
    if catalog is not None:
        real_tables = [alias_map.get((t.alias_or_name or t.name).lower(), t.name.lower()) for t in tables
                       if t.name.lower() not in cte_names]
        select_aliases = {a.alias.lower() for a in ast.find_all(exp.Alias)}
        for col in ast.find_all(exp.Column):
            cname = col.name.lower()
            if not cname or cname == "*" or cname in select_aliases:
                continue
            if col.table:
                tname = alias_map.get(col.table.lower())
                if tname and tname in catalog and cname not in catalog[tname]:
                    ln, c0 = locate(col.name)
                    issues.append(Issue(ln, c0, "error", f"{col.table}.{col.name} doesn't exist.", code="UNKNOWN_COLUMN"))
            elif real_tables and not cte_names:
                owners = [t for t in set(real_tables) if cname in catalog.get(t, set())]
                if len(owners) > 1:
                    ln, c0 = locate(col.name)
                    issues.append(Issue(ln, c0, "warning", f"Column {col.name} is ambiguous ({', '.join(sorted(owners))}).",
                                        code="AMBIGUOUS_COLUMN"))
                elif not owners and all(t in catalog for t in real_tables):
                    ln, c0 = locate(col.name)
                    issues.append(Issue(ln, c0, "error", f"Column {col.name} isn't in any referenced table.",
                                        code="UNKNOWN_COLUMN"))
    # structural hints (cheap)
    for j in ast.find_all(exp.Join):
        if not j.args.get("on") and not j.args.get("using") and (j.kind or "").upper() != "CROSS":
            ln, col = locate("JOIN")
            issues.append(Issue(ln, col, "warning", "This JOIN has no ON condition — that's a Cartesian product.",
                                code="CARTESIAN"))
    if ast.find(exp.Star) and isinstance(ast, exp.Select) and not ast.args.get("limit"):
        issues.append(Issue(1, 1, "info", "SELECT * without a limit can return lots of columns.", code="SELECT_STAR"))
    ok = not any(i.severity == "error" for i in issues)
    return {"ok": ok, "issues": [i.as_dict() for i in issues], "corrected_sql": corrected, "destructive": False}


# ----------------------------------------------------------------------------- wrappers
def _strip_order(ast: exp.Expression) -> exp.Expression:
    a = ast.copy()
    if isinstance(a, exp.Select) and not a.args.get("limit") and not a.args.get("offset") and not a.args.get("fetch"):
        a.set("order", None)
    return a


def count_sql(v: Validated) -> str:
    inner = _strip_order(v.ast)
    q = exp.select(exp.Count(this=exp.Star())).from_(exp.Subquery(this=inner, alias=exp.TableAlias(this=exp.to_identifier("q"))))
    return q.sql(dialect=v.dialect)


def probe_sql(v: Validated) -> str:
    q = exp.select("*").from_(exp.Subquery(this=v.ast.copy(), alias=exp.TableAlias(this=exp.to_identifier("q")))).where("1 = 0")
    return q.sql(dialect=v.dialect)


def page_sql(v: Validated, sort: list[tuple[str, str]], limit: int, offset: int, tiebreak: list[str]) -> str:
    q = exp.select("*").from_(exp.Subquery(this=v.ast.copy(), alias=exp.TableAlias(this=exp.to_identifier("q"))))
    order = [exp.Ordered(this=exp.column(c, table="q", quoted=True), desc=(d == "desc")) for c, d in sort]
    used = {c for c, _ in sort}
    order += [exp.Ordered(this=exp.column(c, table="q", quoted=True)) for c in tiebreak if c not in used]
    if order:
        q = q.order_by(*order)
    q = q.limit(int(limit)).offset(int(offset))
    return q.sql(dialect=v.dialect)


def pretty(sql: str, engine: str) -> str:
    d = DIALECTS[engine]
    return sqlglot.transpile(_strip_trailing(sql), read=d, write=d, pretty=True)[0]


def referenced_tables(v: Validated) -> list[str]:
    ctes = {c.alias_or_name.lower() for c in v.ast.find_all(exp.CTE)}
    return sorted({t.name for t in v.ast.find_all(exp.Table) if t.name.lower() not in ctes})
