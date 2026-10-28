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
    token: str | None = None            # the identifier the issue is about (as written)
    suggestions: list[str] = field(default_factory=list)  # similar names from the approved schema, best first

    def as_dict(self) -> dict[str, Any]:
        d = {"line": self.line, "col": self.col, "end_col": self.end_col, "severity": self.severity,
             "message": self.message, "code": self.code}
        if self.suggestions:
            d["token"] = self.token
            d["suggestions"] = self.suggestions
        return d


def _singular(w: str) -> str:
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith(("ses", "xes", "zes", "ches", "shes")):
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]
    return w


def similar_names(name: str, candidates: Any, n: int = 3) -> list[tuple[str, float]]:
    """Names from `candidates` that look like `name`: typos, case, underscores, singular/plural, prefixes.

    Returns [(candidate, score)] best first, score in 0..1, only plausible matches (score ≥ 0.6).
    """
    import difflib

    raw = name.strip('"`[]').lower()
    flat = raw.replace("_", "")
    out: dict[str, float] = {}
    for cand in candidates:
        c = cand.lower()
        if c == raw:
            continue
        cflat = c.replace("_", "")
        score = difflib.SequenceMatcher(None, raw, c).ratio()
        if cflat == flat or _singular(cflat) == _singular(flat):
            score = max(score, 0.95)                       # order_id vs orderid, order vs orders
        elif len(flat) >= 3 and (flat in cflat or cflat in flat):
            score = max(score, 0.7 + 0.2 * min(len(flat), len(cflat)) / max(len(flat), len(cflat)))
        if "_" in c and "_" not in raw and len(raw) >= 2:
            # prefixed / suffixed names: id → customer_id, name or nme → customer_name, date → signup_date
            for part in c.split("_"):
                if part == raw or (len(raw) >= 3 and _singular(part) == _singular(raw)):
                    score = max(score, 0.85)
                elif len(raw) >= 3 and len(part) >= 3:
                    pr = difflib.SequenceMatcher(None, raw, part).ratio()
                    if pr >= 0.75:
                        score = max(score, 0.75 * pr)   # below whole-name typo matches
        if score >= 0.6:
            out[cand] = max(score, out.get(cand, 0))
    return sorted(out.items(), key=lambda kv: (-kv[1], len(kv[0]), kv[0]))[:n]


def _confident(matches: list[tuple[str, float]]) -> str | None:
    """A single suggestion safe enough to put into the corrected SQL."""
    if not matches or matches[0][1] < 0.8:
        return None
    if len(matches) > 1 and matches[0][1] - matches[1][1] < 0.08:
        return None
    return matches[0][0]


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


# ----------------------------------------------------------------------------- keyword typos
SQL_KEYWORDS = (
    "SELECT FROM WHERE GROUP ORDER BY HAVING LIMIT OFFSET JOIN LEFT RIGHT INNER OUTER FULL CROSS ON AND OR NOT AS "
    "DISTINCT UNION ALL CASE WHEN THEN ELSE END BETWEEN LIKE ILIKE IN IS NULL EXISTS WITH FETCH FIRST NEXT ROWS ONLY "
    "DESC ASC OVER PARTITION USING INTERSECT EXCEPT QUALIFY WINDOW NATURAL LATERAL"
).split()
_KW_SET = {k.lower() for k in SQL_KEYWORDS}
_WORD = re.compile(r"'(?:[^']|'')*'|\"[^\"]*\"|`[^`]*`|\[[^\]]*\]|--[^\n]*|/\*[\s\S]*?\*/|([A-Za-z_][A-Za-z0-9_$]*)")


def _dl_distance(a: str, b: str) -> int:
    """Damerau-Levenshtein (optimal string alignment): typos, missing/extra letters, swapped neighbours."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[len(a)][len(b)]


def _keyword_candidates(word: str) -> list[str]:
    w = word.lower()
    if w in _KW_SET or len(w) < 3:
        return []
    out = []
    for kw in SQL_KEYWORDS:
        k = kw.lower()
        if abs(len(k) - len(w)) > 2 or (len(k) <= 2 and len(w) > 3):
            continue
        dist = _dl_distance(w, k)
        if dist == 1 or (dist == 2 and min(len(w), len(k)) >= 6):
            out.append((dist, abs(len(k) - len(w)), kw))
    return [kw for _, _, kw in sorted(out)]


def _parse_progress(sql: str, dialect: str) -> int:
    """How far the parser gets: len(sql)+1 when the statement parses, else the error offset."""
    try:
        parse_one(sql, dialect)
        return len(sql) + 1
    except ApiError as e:
        if e.code != "E-EXE-002":
            return len(sql) + 1
        lines = sql.splitlines()
        ln, col = int(e.extra.get("l") or 1), int(e.extra.get("c") or 1)
        return sum(len(x) + 1 for x in lines[: ln - 1]) + col


def _match_case(word: str, kw: str) -> str:
    return kw.lower() if word.islower() else kw.title() if word[:1].isupper() and word[1:].islower() else kw


def keyword_typos(sql: str, dialect: str, known: set[str]) -> tuple[str, list[tuple[int, int, str, str]]] | None:
    """For SQL that doesn't parse: words that look like misspelt keywords (were → WHERE, form → FROM).

    A fix is kept only if it makes the statement parse or moves the parse error further along, so ordinary
    identifiers are left alone. Returns (fixed_sql, [(offset_in_original, len, word, keyword)]) or None.
    """
    words = [(m.start(1), m.group(1)) for m in _WORD.finditer(sql) if m.group(1)]
    cands = [(pos, w, kws) for pos, w in words if w.lower() not in known and (kws := _keyword_candidates(w))]
    if not cands:
        return None
    fixed, shift, applied = sql, 0, []
    best = _parse_progress(fixed, dialect)
    for pos, w, kws in cands:
        at = pos + shift
        for kw in kws:
            rep = _match_case(w, kw)
            trial = fixed[:at] + rep + fixed[at + len(w):]
            prog = _parse_progress(trial, dialect)
            # progress is measured on the trial text; compare against the same spot in the current text
            if prog > len(trial) or prog > best + (len(rep) - len(w)):
                fixed, shift, best = trial, shift + len(rep) - len(w), prog
                applied.append((pos, len(w), w, kw))
                break
        if best > len(fixed):
            break
    return (fixed, applied) if applied else None


_AGGREGATES = {"count": "count(*)", "sum": None, "avg": None, "min": None, "max": None}


# ----------------------------------------------------------------------------- pre-flight (stage 1, static)
def static_preflight(sql: str, engine: str, catalog: dict[str, set[str]] | None = None,
                     excluded: set[str] | None = None, _nested: bool = False) -> dict[str, Any]:
    """Returns {ok, issues[], corrected_sql?}. Never touches the target DB (TS §13.2).

    catalog maps lower-case table name (and schema.table) → set of lower-case column names.
    """
    dialect = DIALECTS[engine]
    issues: list[Issue] = []
    corrected = None
    fixes: list[tuple[str, str]] = []   # (identifier as written, confident replacement)
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
        if e.code == "E-EXE-002" and not _nested:
            known = {k.split(".")[-1] for k in (catalog or {})} | {c for cols in (catalog or {}).values() for c in cols}
            kt = keyword_typos(sql, dialect, known)
            if kt:
                return _with_keyword_fixes(sql, engine, catalog, excluded, kt, issues, corrected)
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
    fixed_tables: dict[str, str] = {}   # unknown table → confident suggestion
    for t in tables:
        name = t.name.lower()
        full = f"{t.db.lower()}.{name}" if t.db else name
        alias_map[(t.alias_or_name or name).lower()] = full if full in (catalog or {}) else name
        if catalog is not None and name not in cte_names and full not in catalog and name not in catalog:
            ln, col = locate(t.name)
            pool = [k for k in catalog if "." not in k and k not in (excluded or set())]
            matches = similar_names(t.name, pool)
            msg = f"I can't find a table called {t.name} in the approved schema."
            if matches:
                msg += " Did you mean " + _or_list([m for m, _ in matches]) + "?"
            issues.append(Issue(ln, col, "error", msg, end_col=col + len(t.name), code="UNKNOWN_TABLE",
                                token=t.name, suggestions=[m for m, _ in matches]))
            fix = _confident(matches)
            if fix:
                fixes.append((t.name, fix))
                alias_map[(t.alias_or_name or name).lower()] = fix  # check its columns against the likely table
                fixed_tables[name] = fix
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
        real_tables = [fixed_tables.get(t.name.lower()) or alias_map.get((t.alias_or_name or t.name).lower(), t.name.lower())
                       for t in tables if t.name.lower() not in cte_names]
        select_aliases = {a.alias.lower() for a in ast.find_all(exp.Alias)}
        for col in ast.find_all(exp.Column):
            cname = col.name.lower()
            if not cname or cname == "*" or cname in select_aliases:
                continue
            if (not col.table and cname in _AGGREGATES and real_tables
                    and not any(cname in catalog.get(t, set()) for t in set(real_tables))):
                ln, c0 = locate(col.name)
                fix = _AGGREGATES[cname]
                msg = f"{col.name} is a function and needs brackets" + (
                    f" — did you mean {fix}?" if fix else f", e.g. {col.name}(amount).")
                issues.append(Issue(ln, c0, "error", msg, end_col=c0 + len(col.name), code="BARE_AGGREGATE",
                                    token=col.name, suggestions=[fix] if fix else []))
                if fix:
                    fixes.append((col.name, fix))
                continue
            if col.table:
                tname = alias_map.get(col.table.lower())
                if tname and tname in catalog and cname not in catalog[tname]:
                    ln, c0 = locate(col.name)
                    matches = similar_names(col.name, sorted(catalog[tname]))
                    msg = f"{col.table}.{col.name} doesn't exist."
                    if matches:
                        msg += " Did you mean " + _or_list([f"{col.table}.{m}" for m, _ in matches]) + "?"
                    issues.append(Issue(ln, c0, "error", msg, end_col=c0 + len(col.name), code="UNKNOWN_COLUMN",
                                        token=col.name, suggestions=[m for m, _ in matches]))
                    fix = _confident(matches)
                    if fix:
                        fixes.append((col.name, fix))
            elif real_tables and not cte_names:
                owners = [t for t in set(real_tables) if cname in catalog.get(t, set())]
                if len(owners) > 1:
                    ln, c0 = locate(col.name)
                    issues.append(Issue(ln, c0, "warning", f"Column {col.name} is ambiguous ({', '.join(sorted(owners))}).",
                                        code="AMBIGUOUS_COLUMN"))
                elif not owners and all(t in catalog for t in real_tables):
                    ln, c0 = locate(col.name)
                    pool = sorted({c for t in set(real_tables) for c in catalog.get(t, set())})
                    matches = similar_names(col.name, pool)
                    msg = f"Column {col.name} isn't in any referenced table."
                    if matches:
                        where = {m: sorted(t for t in set(real_tables) if m in catalog.get(t, set())) for m, _ in matches}
                        msg += " Did you mean " + _or_list([f"{m} ({', '.join(where[m])})" for m, _ in matches]) + "?"
                    issues.append(Issue(ln, c0, "error", msg, end_col=c0 + len(col.name), code="UNKNOWN_COLUMN",
                                        token=col.name, suggestions=[m for m, _ in matches]))
                    fix = _confident(matches)
                    if fix:
                        fixes.append((col.name, fix))
    # structural hints (cheap)
    for j in ast.find_all(exp.Join):
        if not j.args.get("on") and not j.args.get("using") and (j.kind or "").upper() != "CROSS":
            ln, col = locate("JOIN")
            issues.append(Issue(ln, col, "warning", "This JOIN has no ON condition — that's a Cartesian product.",
                                code="CARTESIAN"))
    if ast.find(exp.Star) and isinstance(ast, exp.Select) and not ast.args.get("limit"):
        issues.append(Issue(1, 1, "info", "SELECT * without a limit can return lots of columns.", code="SELECT_STAR"))
    seen: set[tuple] = set()  # one issue per (kind, name), e.g. a misspelt column used in SELECT and WHERE
    issues = [i for i in issues if not ((k := (i.code, (i.token or "").lower(), i.message)) in seen or seen.add(k))]
    if fixes:  # every confident name fix applied as whole-word replacement (outside quotes/comments)
        corrected = _replace_identifiers(corrected or sql, dict(fixes))
    ok = not any(i.severity == "error" for i in issues)
    return {"ok": ok, "issues": [i.as_dict() for i in issues], "corrected_sql": corrected, "destructive": False}


def _with_keyword_fixes(sql: str, engine: str, catalog, excluded, kt, pre_issues: list[Issue], corrected):
    fixed, applied = kt
    lines = sql.splitlines() or [""]
    starts = [0]
    for ln in lines[:-1]:
        starts.append(starts[-1] + len(ln) + 1)

    def pos(offset: int) -> tuple[int, int]:
        ln = max(i for i, st in enumerate(starts) if st <= offset)
        return ln + 1, offset - starts[ln] + 1

    out = [i.as_dict() for i in pre_issues]
    for off, ln_, word, kw in applied:
        line, col = pos(off)
        out.append({**Issue(line, col, "error", f"“{word}” looks like a typo for {kw}.", end_col=col + ln_,
                            code="KEYWORD_TYPO", token=word, suggestions=[_match_case(word, kw)]).as_dict()})
    inner = static_preflight(fixed, engine, catalog, excluded, _nested=True)
    # map positions in the repaired text back to the original (keyword fixes change lengths on their line)
    shifts: dict[int, list[tuple[int, int]]] = {}
    for off, ln_, word, kw in applied:
        line, col = pos(off)
        shifts.setdefault(line, []).append((col, len(kw) - ln_))
    for i in inner["issues"]:
        delta = 0
        for col, d in sorted(shifts.get(i["line"], [])):
            if i["col"] > col + delta:
                delta += d
        i = {**i, "col": i["col"] - delta}
        if i.get("end_col"):
            i["end_col"] = i["end_col"] - delta
        out.append(i)
    return {"ok": False, "issues": out, "corrected_sql": inner.get("corrected_sql") or fixed,
            "destructive": inner.get("destructive", False)}


def _or_list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " or " + items[-1]


_SKIP = re.compile(r"'(?:[^']|'')*'|--[^\n]*|/\*[\s\S]*?\*/")


def _replace_identifiers(sql: str, fixes: dict[str, str]) -> str:
    """Whole-word, case-insensitive replacement of identifiers, leaving string literals and comments alone."""
    words = re.compile(r"(?<![\w$])(" + "|".join(re.escape(k) for k in sorted(fixes, key=len, reverse=True)) + r")(?![\w$])",
                       re.I)
    lower = {k.lower(): v for k, v in fixes.items()}
    out, pos = [], 0
    for m in _SKIP.finditer(sql):
        out.append(words.sub(lambda w: lower[w.group(1).lower()], sql[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(words.sub(lambda w: lower[w.group(1).lower()], sql[pos:]))
    return "".join(out)


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
