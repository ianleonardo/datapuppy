"""Connector framework: per-dialect adapters, pooling, read-only enforcement, timeouts, introspection (TS §9).

Adapters are synchronous and run in the connector thread pool (TS §6.3).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import queue
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from ..core.config import get_config
from ..core.crypto import keyring
from ..core.errors import ApiError
from ..core.paths import is_inside

log = logging.getLogger("sqlassist.connectors")


@dataclass
class ColumnMeta:
    name: str
    type: str


@dataclass
class Page:
    columns: list[ColumnMeta]
    rows: list[list[Any]]


@dataclass
class CatalogDump:
    tables: list[dict[str, Any]] = field(default_factory=list)
    fks: list[dict[str, Any]] = field(default_factory=list)


# ----------------------------------------------------------------------------- pooling
class Pool:
    def __init__(self, factory, max_size: int = 10, idle_s: int = 600, ping=None):
        self.factory, self.max_size, self.idle_s, self.ping = factory, max_size, idle_s, ping
        self._idle: queue.LifoQueue = queue.LifoQueue()
        self._count = 0
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 30):
        while True:
            try:
                con, ts = self._idle.get_nowait()
            except queue.Empty:
                break
            if time.time() - ts > self.idle_s or (self.ping and not self._ok(con)):
                self._discard(con)
                continue
            return con
        with self._lock:
            if self._count < self.max_size:
                self._count += 1
                try:
                    return self.factory()
                except Exception:
                    self._count -= 1
                    raise
        con, _ = self._idle.get(timeout=timeout)
        return con

    def _ok(self, con) -> bool:
        try:
            self.ping(con)
            return True
        except Exception:  # noqa: BLE001
            return False

    def release(self, con, broken: bool = False) -> None:
        if broken:
            self._discard(con)
        else:
            self._idle.put((con, time.time()))

    def _discard(self, con) -> None:
        with self._lock:
            self._count -= 1
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        while not self._idle.empty():
            self._discard(self._idle.get_nowait()[0])


# ----------------------------------------------------------------------------- base adapter
class BaseAdapter:
    engine = ""
    sqlglot_dialect = ""

    def __init__(self, conn_row: dict[str, Any], secret: dict[str, Any]):
        self.row = conn_row
        self.params = conn_row["params"] if isinstance(conn_row["params"], dict) else json.loads(conn_row["params"])
        self.secret = secret
        self.pool = Pool(self._connect, max_size=int(self.params.get("pool_max", 10)), ping=self._ping)
        self._running: dict[str, Any] = {}

    # --- to implement
    def _connect(self):  # pragma: no cover - engine specific
        raise NotImplementedError

    def _ping(self, con) -> None:
        cur = con.cursor()
        cur.execute(self.ping_sql)
        cur.fetchall()
        cur.close()

    ping_sql = "SELECT 1"

    def _begin_readonly(self, cur) -> None:
        pass

    def _set_timeout(self, con, cur, seconds: float) -> None:
        pass

    def _interrupt(self, con) -> None:
        pass

    def introspect(self, include_schemas: list[str] | None) -> CatalogDump:
        raise NotImplementedError

    def explain_estimate(self, sql: str) -> int | None:
        return None

    def write_probe(self) -> bool:
        """True if the account appears able to write (TR-CON-03)."""
        return False

    def server_version(self) -> str:
        return ""

    # --- common
    def _run(self, sql: str, timeout_s: float, run_id: str | None = None, fetch: int | None = None,
             params: list | None = None) -> tuple[list[ColumnMeta], list[list[Any]]]:
        con = self.pool.acquire()
        broken = False
        timer = None
        try:
            cur = con.cursor()
            self._begin_readonly(cur)
            self._set_timeout(con, cur, timeout_s)
            if run_id:
                self._running[run_id] = con
            timer = threading.Timer(timeout_s + 0.5, self._interrupt, args=(con,))
            timer.daemon = True
            timer.start()
            started = time.monotonic()
            try:
                cur.execute(sql, params) if params else cur.execute(sql)
                desc = cur.description or []
                data = cur.fetchmany(fetch) if fetch else cur.fetchall()
            except Exception as e:  # noqa: BLE001
                broken = True
                if time.monotonic() - started >= timeout_s - 0.05 or _looks_like_timeout(e):
                    raise ApiError("E-EXE-030", n=int(timeout_s)) from e
                raise ApiError("E-EXE-050", detail=_clean(e)) from e
            cols = [ColumnMeta(str(d[0]), _type_name(d[1])) for d in desc]
            self._end(cur)
            return cols, [list(r) for r in data]
        finally:
            if timer:
                timer.cancel()
            if run_id:
                self._running.pop(run_id, None)
            self.pool.release(con, broken=broken)

    def _end(self, cur) -> None:
        try:
            cur.execute("ROLLBACK")
        except Exception:  # noqa: BLE001
            pass
        try:
            cur.close()
        except Exception:  # noqa: BLE001
            pass

    def test(self) -> dict[str, Any]:
        t0 = time.monotonic()
        try:
            self._run(self.ping_sql, 10)
        except ApiError as e:
            raise ApiError("E-CON-001", detail=e.message) from e
        except Exception as e:  # noqa: BLE001
            raise ApiError("E-CON-001", detail=_clean(e)) from e
        latency = int((time.monotonic() - t0) * 1000)
        try:
            can_write = self.write_probe()
        except Exception:  # noqa: BLE001
            can_write = None
        return {"ok": True, "latency_ms": latency, "server_version": self._safe(self.server_version),
                "write_privilege": can_write,
                "warning": "This account can write — please use a read-only account (least privilege)." if can_write else None}

    def _safe(self, fn):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return ""

    def count(self, sql: str, timeout_s: float) -> int:
        _, rows = self._run(sql, timeout_s)
        return int(rows[0][0])

    def describe(self, sql: str, timeout_s: float = 30) -> list[ColumnMeta]:
        cols, _ = self._run(sql, timeout_s)
        return cols

    def fetch(self, sql: str, timeout_s: float, run_id: str | None = None, limit: int | None = None) -> Page:
        cols, rows = self._run(sql, timeout_s, run_id=run_id, fetch=limit)
        return Page(cols, rows)

    def stream(self, sql: str, batch: int = 5000, timeout_s: float = 600) -> Iterator[tuple[list[ColumnMeta], list[list[Any]]]]:
        con = self.pool.acquire()
        broken = False
        try:
            cur = con.cursor()
            self._begin_readonly(cur)
            self._set_timeout(con, cur, timeout_s)
            cur.execute(sql)
            cols = [ColumnMeta(str(d[0]), _type_name(d[1])) for d in cur.description or []]
            while True:
                chunk = cur.fetchmany(batch)
                if not chunk:
                    break
                yield cols, [list(r) for r in chunk]
            self._end(cur)
        except Exception:
            broken = True
            raise
        finally:
            self.pool.release(con, broken=broken)

    def cancel(self, run_id: str) -> None:
        con = self._running.get(run_id)
        if con is not None:
            self._interrupt(con)

    def close(self) -> None:
        self.pool.close()


def _type_name(t: Any) -> str:
    if t is None:
        return ""
    if isinstance(t, str):
        return t
    return getattr(t, "__name__", None) or getattr(t, "name", None) or str(t)


def _clean(e: Exception) -> str:
    return re.sub(r"\s+", " ", str(e)).strip()[:300]


def _looks_like_timeout(e: Exception) -> bool:
    s = str(e).lower()
    return any(k in s for k in ("timeout", "canceling statement", "interrupt", "max_statement_time", "dpi-1067", "ora-01013"))


# ----------------------------------------------------------------------------- DuckDB (target)
class DuckDBAdapter(BaseAdapter):
    engine, sqlglot_dialect = "DUCKDB", "duckdb"

    def __init__(self, conn_row, secret):
        super().__init__(conn_row, secret)
        self._shared = None
        self._lock = threading.Lock()

    def _path(self) -> str:
        p = self.params.get("path") or ":memory:"
        if p != ":memory:":
            guard_duckdb_path(p)
        return p

    def _connect(self):
        import duckdb

        with self._lock:
            if self._shared is None:
                path = self._path()
                try:
                    cfg = {"autoinstall_known_extensions": False, "autoload_known_extensions": False}
                    if path == ":memory:":
                        self._shared = duckdb.connect(":memory:", config=cfg)
                        for att in self.params.get("attach", []):
                            guard_duckdb_path(att["path"])
                            self._shared.execute(f"ATTACH '{_q(att['path'])}' AS {_ident(att['alias'])} (READ_ONLY)")
                        self._shared.execute("SET enable_external_access = false")
                    else:
                        if not Path(path).exists():
                            raise ApiError("E-CON-001", detail=f"file not found: {Path(path).name}")
                        self._shared = duckdb.connect(path, read_only=True, config=cfg)
                        self._shared.execute("SET enable_external_access = false")
                except duckdb.IOException as e:
                    if "lock" in str(e).lower():
                        raise ApiError("E-CON-003") from e
                    raise ApiError("E-CON-001", detail=_clean(e)) from e
            return _DuckCursorCon(self._shared.cursor())

    def _ping(self, con) -> None:
        con.cursor().execute("SELECT 1").fetchall()

    def _interrupt(self, con) -> None:
        try:
            con.raw.interrupt()
        except Exception:  # noqa: BLE001
            pass

    def _end(self, cur) -> None:
        pass

    def server_version(self) -> str:
        import duckdb
        return f"DuckDB {duckdb.__version__}"

    def introspect(self, include_schemas: list[str] | None) -> CatalogDump:
        con = self.pool.acquire()
        try:
            c = con.cursor()
            sch_filter = ""
            if include_schemas:
                sch_filter = " AND schema_name IN (" + ",".join(f"'{_q(s)}'" for s in include_schemas) + ")"
            tables = c.execute(
                "SELECT schema_name, table_name, 'TABLE' AS t, estimated_size, comment FROM duckdb_tables() "
                f"WHERE NOT internal AND NOT temporary {sch_filter} UNION ALL "
                "SELECT schema_name, view_name, 'VIEW', NULL, comment FROM duckdb_views() "
                f"WHERE NOT internal AND NOT temporary {sch_filter} ORDER BY 1, 2").fetchall()
            cols = c.execute("SELECT schema_name, table_name, column_name, data_type, is_nullable, column_index, comment "
                             f"FROM duckdb_columns() WHERE NOT internal {sch_filter}").fetchall()
            cons = c.execute("SELECT schema_name, table_name, constraint_type, constraint_column_names, "
                             "referenced_table, referenced_column_names, constraint_name FROM duckdb_constraints() "
                             f"WHERE constraint_type IN ('PRIMARY KEY','FOREIGN KEY') {sch_filter}").fetchall()
        finally:
            self.pool.release(con)
        pks = {(s, t): set(cn) for s, t, ct, cn, *_ in cons if ct == "PRIMARY KEY"}
        dump = CatalogDump()
        by_table: dict[tuple, list] = {}
        for s, t, cn, dt, nul, idx, cm in cols:
            by_table.setdefault((s, t), []).append({"name": cn, "type": dt, "nullable": nul, "ordinal": idx,
                                                     "pk": cn in pks.get((s, t), set()), "comment": cm})
        for s, t, typ, est, cm in tables:
            dump.tables.append({"schema": s, "name": t, "type": typ, "row_estimate": est, "comment": cm,
                                "columns": sorted(by_table.get((s, t), []), key=lambda x: x["ordinal"])})
        for s, t, ct, cn, rt, rc, name in cons:
            if ct == "FOREIGN KEY" and rt:
                dump.fks.append({"from_schema": s, "from_table": t, "from_cols": list(cn), "to_schema": s,
                                 "to_table": rt, "to_cols": list(rc or []), "name": name})
        return dump

    def explain_estimate(self, sql: str) -> int | None:
        try:
            _, rows = self._run(f"EXPLAIN {sql}", 10)
            text = "\n".join(str(r[-1]) for r in rows)
            m = re.findall(r"~\s*([\d,]+)\s*row", text, re.I) or re.findall(r"EC:\s*([\d,]+)", text)
            return int(m[0].replace(",", "")) if m else None
        except ApiError:
            return None


class _DuckCursorCon:
    """Adapts a DuckDB cursor to the minimal DB-API connection interface used by BaseAdapter."""

    def __init__(self, raw):
        self.raw = raw

    def cursor(self):
        return self.raw

    def close(self):
        try:
            self.raw.close()
        except Exception:  # noqa: BLE001
            pass


def guard_duckdb_path(p: str) -> None:
    """TR-CON-04: a target DuckDB path must not resolve inside data_dir."""
    cfg = get_config()
    path = Path(p).expanduser()
    if not path.is_absolute() or is_inside(path, cfg.data_dir) or path.resolve() == cfg.db_path.resolve():
        raise ApiError("E-CON-004")


# ----------------------------------------------------------------------------- PostgreSQL
class PostgresAdapter(BaseAdapter):
    engine, sqlglot_dialect = "POSTGRES", "postgres"

    def _connect(self):
        import psycopg

        p = self.params
        opts = "-c default_transaction_read_only=on"
        if p.get("search_path"):
            opts += f" -c search_path={p['search_path']}"
        try:
            con = psycopg.connect(host=p["host"], port=int(p.get("port", 5432)), dbname=p["database"],
                                  user=self.secret.get("username"), password=self.secret.get("password"),
                                  sslmode=p.get("sslmode", "prefer"), connect_timeout=10, options=opts,
                                  application_name="sqlassist")
        except Exception as e:  # noqa: BLE001
            raise ApiError("E-CON-001", detail=_clean(e)) from e
        con.read_only = True
        return con

    def _begin_readonly(self, cur) -> None:
        cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")

    def _set_timeout(self, con, cur, seconds: float) -> None:
        cur.execute(f"SET statement_timeout = {int(seconds * 1000)}")

    def _interrupt(self, con) -> None:
        try:
            con.cancel()
        except Exception:  # noqa: BLE001
            pass

    def server_version(self) -> str:
        return str(self._run("SELECT version()", 10)[1][0][0])

    def write_probe(self) -> bool:
        _, rows = self._run("SELECT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) OR EXISTS ("
                            "SELECT 1 FROM information_schema.table_privileges WHERE grantee = current_user "
                            "AND privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE'))", 10)
        return bool(rows[0][0])

    def introspect(self, include_schemas: list[str] | None) -> CatalogDump:
        sch = include_schemas or None
        flt = "AND t.table_schema = ANY(%s)" if sch else "AND t.table_schema NOT IN ('pg_catalog','information_schema')"
        prm = [sch] if sch else None
        _, tables = self._run(
            "SELECT t.table_schema, t.table_name, CASE WHEN t.table_type = 'VIEW' THEN 'VIEW' ELSE 'TABLE' END, "
            "c.reltuples::bigint, obj_description(c.oid) FROM information_schema.tables t "
            "LEFT JOIN pg_namespace n ON n.nspname = t.table_schema LEFT JOIN pg_class c ON c.relname = t.table_name "
            f"AND c.relnamespace = n.oid WHERE 1=1 {flt} ORDER BY 1, 2", 120, params=prm)
        _, cols = self._run(
            "SELECT t.table_schema, t.table_name, t.column_name, t.data_type, t.is_nullable = 'YES', t.ordinal_position "
            f"FROM information_schema.columns t WHERE 1=1 {flt}", 120, params=prm)
        _, keys = self._run(
            "SELECT tc.table_schema, tc.table_name, tc.constraint_type, tc.constraint_name, kcu.column_name, "
            "ccu.table_schema, ccu.table_name, ccu.column_name FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name "
            "AND tc.table_schema = kcu.table_schema LEFT JOIN information_schema.constraint_column_usage ccu "
            "ON tc.constraint_type = 'FOREIGN KEY' AND ccu.constraint_name = tc.constraint_name "
            f"WHERE tc.constraint_type IN ('PRIMARY KEY','FOREIGN KEY') {flt.replace('t.', 'tc.')}", 120, params=prm)
        return _assemble(tables, cols, keys)

    def explain_estimate(self, sql: str) -> int | None:
        try:
            _, rows = self._run(f"EXPLAIN (FORMAT JSON) {sql}", 10)
            plan = rows[0][0]
            plan = json.loads(plan) if isinstance(plan, str) else plan
            return int(plan[0]["Plan"]["Plan Rows"])
        except Exception:  # noqa: BLE001
            return None


# ----------------------------------------------------------------------------- MariaDB
class MariaDBAdapter(BaseAdapter):
    engine, sqlglot_dialect = "MARIADB", "mysql"

    def _connect(self):
        import mariadb

        p = self.params
        kw = dict(host=p["host"], port=int(p.get("port", 3306)), database=p.get("database"),
                  user=self.secret.get("username"), password=self.secret.get("password"), connect_timeout=10)
        if p.get("ssl"):
            kw["ssl"] = True
        try:
            return mariadb.connect(**kw)
        except Exception as e:  # noqa: BLE001
            raise ApiError("E-CON-001", detail=_clean(e)) from e

    def _begin_readonly(self, cur) -> None:
        cur.execute("START TRANSACTION READ ONLY")

    def _set_timeout(self, con, cur, seconds: float) -> None:
        cur.execute(f"SET SESSION max_statement_time = {float(seconds)}")

    def _interrupt(self, con) -> None:
        try:
            tid = con.thread_id
            import mariadb
            p = self.params
            k = mariadb.connect(host=p["host"], port=int(p.get("port", 3306)), user=self.secret.get("username"),
                                password=self.secret.get("password"))
            k.cursor().execute(f"KILL QUERY {int(tid)}")
            k.close()
        except Exception:  # noqa: BLE001
            pass

    def server_version(self) -> str:
        return str(self._run("SELECT VERSION()", 10)[1][0][0])

    def write_probe(self) -> bool:
        _, rows = self._run("SHOW GRANTS", 10)
        text = " ".join(str(r[0]).upper() for r in rows)
        return bool(re.search(r"\b(ALL PRIVILEGES|INSERT|UPDATE|DELETE|DROP|CREATE)\b", text))

    def introspect(self, include_schemas: list[str] | None) -> CatalogDump:
        sch = include_schemas or [self.params.get("database")]
        ph = ",".join(["?"] * len(sch))
        _, tables = self._run("SELECT TABLE_SCHEMA, TABLE_NAME, IF(TABLE_TYPE='VIEW','VIEW','TABLE'), TABLE_ROWS, "
                              f"TABLE_COMMENT FROM information_schema.TABLES WHERE TABLE_SCHEMA IN ({ph}) ORDER BY 1,2",
                              120, params=sch)
        _, cols = self._run("SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE='YES', ORDINAL_POSITION "
                            f"FROM information_schema.COLUMNS WHERE TABLE_SCHEMA IN ({ph})", 120, params=sch)
        _, keys = self._run(
            "SELECT k.TABLE_SCHEMA, k.TABLE_NAME, IF(k.CONSTRAINT_NAME='PRIMARY','PRIMARY KEY','FOREIGN KEY'), "
            "k.CONSTRAINT_NAME, k.COLUMN_NAME, k.REFERENCED_TABLE_SCHEMA, k.REFERENCED_TABLE_NAME, k.REFERENCED_COLUMN_NAME "
            f"FROM information_schema.KEY_COLUMN_USAGE k WHERE k.TABLE_SCHEMA IN ({ph}) AND "
            "(k.CONSTRAINT_NAME = 'PRIMARY' OR k.REFERENCED_TABLE_NAME IS NOT NULL)", 120, params=sch)
        return _assemble(tables, cols, keys)

    def explain_estimate(self, sql: str) -> int | None:
        try:
            _, rows = self._run(f"EXPLAIN FORMAT=JSON {sql}", 10)
            text = str(rows[0][0])
            m = re.findall(r'"rows":\s*(\d+)', text)
            return max(int(x) for x in m) if m else None
        except Exception:  # noqa: BLE001
            return None


# ----------------------------------------------------------------------------- Oracle
class OracleAdapter(BaseAdapter):
    engine, sqlglot_dialect = "ORACLE", "oracle"
    ping_sql = "SELECT 1 FROM DUAL"
    _thick_init = False

    def _connect(self):
        import oracledb

        p = self.params
        if p.get("thick") and not OracleAdapter._thick_init:
            oracledb.init_oracle_client(lib_dir=p.get("instant_client_dir") or None)
            OracleAdapter._thick_init = True
        kw: dict[str, Any] = dict(user=self.secret.get("username"), password=self.secret.get("password"))
        mode = p.get("mode", "easy")
        try:
            if mode == "tns":
                kw.update(dsn=p["tns_alias"], config_dir=p["tns_admin"])
            elif mode == "wallet":
                wdir = self._wallet_dir()
                kw.update(dsn=p["tns_alias"], config_dir=wdir, wallet_location=wdir,
                          wallet_password=self.secret.get("wallet_password"))
            else:
                kw.update(dsn=f"{p['host']}:{p.get('port', 1521)}/{p['service_name']}")
            con = oracledb.connect(**kw)
        except ApiError:
            raise
        except Exception as e:  # noqa: BLE001
            msg = _clean(e)
            if "TNS" in msg or "DPY-4" in msg:
                raise ApiError("E-CON-002") from e
            raise ApiError("E-CON-001", detail=msg) from e
        return con

    def _wallet_dir(self) -> str:
        """Wallet zip stored encrypted in data_dir/wallets; decrypted to a per-process owner-only temp dir."""
        cfg = get_config()
        enc = cfg.data_dir / "wallets" / f"{self.row['id']}.bin"
        if not enc.exists():
            raise ApiError("E-CON-002")
        blob = json.loads(enc.read_text())
        data = keyring().open(base64.b64decode(blob["ct"]), base64.b64decode(blob["nonce"]), blob["kv"],
                              f"wallet:{self.row['id']}")
        target = Path(tempfile.mkdtemp(prefix="sqlassist-wallet-"))
        os.chmod(target, 0o700)
        import io
        import zipfile
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(target)
        return str(target)

    def _begin_readonly(self, cur) -> None:
        cur.execute("SET TRANSACTION READ ONLY")

    def _set_timeout(self, con, cur, seconds: float) -> None:
        con.call_timeout = int(seconds * 1000)

    def _interrupt(self, con) -> None:
        try:
            con.cancel()
        except Exception:  # noqa: BLE001
            pass

    def server_version(self) -> str:
        return str(self._run("SELECT banner FROM v$version WHERE ROWNUM = 1", 10)[1][0][0])

    def write_probe(self) -> bool:
        _, rows = self._run("SELECT COUNT(*) FROM session_privs WHERE privilege LIKE '% ANY TABLE' "
                            "OR privilege IN ('CREATE TABLE','DBA')", 10)
        return int(rows[0][0]) > 0

    def introspect(self, include_schemas: list[str] | None) -> CatalogDump:
        owners = [s.upper() for s in (include_schemas or [self.secret.get("username", "")])]
        ph = ",".join(f":o{i}" for i in range(len(owners)))
        bind = {f"o{i}": o for i, o in enumerate(owners)}

        def run(sql):
            con = self.pool.acquire()
            try:
                cur = con.cursor()
                cur.execute(sql, bind)
                return cur.fetchall()
            finally:
                self.pool.release(con)
        tables = run("SELECT t.owner, t.table_name, 'TABLE', t.num_rows, c.comments FROM all_tables t LEFT JOIN "
                     "all_tab_comments c ON c.owner = t.owner AND c.table_name = t.table_name "
                     f"WHERE t.owner IN ({ph}) AND t.table_name NOT LIKE 'BIN$%' UNION ALL SELECT owner, view_name, "
                     f"'VIEW', NULL, NULL FROM all_views WHERE owner IN ({ph})")
        cols = run("SELECT owner, table_name, column_name, data_type, CASE nullable WHEN 'Y' THEN 1 ELSE 0 END, column_id "
                   f"FROM all_tab_columns WHERE owner IN ({ph})")
        keys = run("SELECT c.owner, c.table_name, DECODE(c.constraint_type,'P','PRIMARY KEY','FOREIGN KEY'), "
                   "c.constraint_name, cc.column_name, r.owner, r.table_name, rc.column_name FROM all_constraints c "
                   "JOIN all_cons_columns cc ON cc.owner = c.owner AND cc.constraint_name = c.constraint_name "
                   "LEFT JOIN all_constraints r ON r.owner = c.r_owner AND r.constraint_name = c.r_constraint_name "
                   "LEFT JOIN all_cons_columns rc ON rc.owner = r.owner AND rc.constraint_name = r.constraint_name "
                   f"AND rc.position = cc.position WHERE c.owner IN ({ph}) AND c.constraint_type IN ('P','R')")
        return _assemble(tables, cols, keys)

    def explain_estimate(self, sql: str) -> int | None:
        con = self.pool.acquire()
        try:
            cur = con.cursor()
            stmt_id = f"sqa{int(time.time() * 1000) % 10**9}"
            cur.execute(f"EXPLAIN PLAN SET STATEMENT_ID = '{stmt_id}' FOR {sql}")
            cur.execute("SELECT cardinality FROM plan_table WHERE statement_id = :s AND id = 0", s=stmt_id)
            r = cur.fetchone()
            con.rollback()
            return int(r[0]) if r and r[0] is not None else None
        except Exception:  # noqa: BLE001
            return None
        finally:
            self.pool.release(con)


def _assemble(tables, cols, keys) -> CatalogDump:
    dump = CatalogDump()
    pk: dict[tuple, set] = {}
    fk: dict[tuple, dict] = {}
    for s, t, ctype, cname, col, rs, rt, rcol in keys:
        if ctype == "PRIMARY KEY":
            pk.setdefault((s, t), set()).add(col)
        elif rt:
            d = fk.setdefault((s, t, cname), {"from_schema": s, "from_table": t, "from_cols": [], "to_schema": rs,
                                              "to_table": rt, "to_cols": [], "name": cname})
            if col not in d["from_cols"]:
                d["from_cols"].append(col)
                d["to_cols"].append(rcol)
    by: dict[tuple, list] = {}
    for s, t, c, dt, nul, pos in cols:
        by.setdefault((s, t), []).append({"name": c, "type": dt, "nullable": bool(nul), "ordinal": int(pos or 0),
                                          "pk": c in pk.get((s, t), set()), "comment": None})
    for s, t, typ, est, cm in tables:
        dump.tables.append({"schema": s, "name": t, "type": typ, "row_estimate": int(est) if est and est > 0 else None,
                            "comment": cm, "columns": sorted(by.get((s, t), []), key=lambda x: x["ordinal"])})
    dump.fks = list(fk.values())
    return dump


def _q(s: str) -> str:
    return str(s).replace("'", "''")


def _ident(s: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", s or ""):
        raise ApiError("E-VAL-001", detail="invalid alias")
    return s


ADAPTERS = {"DUCKDB": DuckDBAdapter, "POSTGRES": PostgresAdapter, "MARIADB": MariaDBAdapter, "ORACLE": OracleAdapter}


# ----------------------------------------------------------------------------- registry (lazy pools per key version)
class Registry:
    def __init__(self) -> None:
        self._adapters: dict[tuple[str, int, str], BaseAdapter] = {}
        self._lock = threading.Lock()

    def get(self, conn_row: dict[str, Any]) -> BaseAdapter:
        key = (str(conn_row["id"]), int(conn_row["secret_key_version"]), str(conn_row.get("updated_at")))
        with self._lock:
            a = self._adapters.get(key)
            if a is None:
                for k in [k for k in self._adapters if k[0] == key[0]]:
                    self._adapters.pop(k).close()
                secret = decrypt_secret(conn_row)
                a = ADAPTERS[conn_row["engine"]](conn_row, secret)
                self._adapters[key] = a
            return a

    def invalidate(self, connection_id: str) -> None:
        with self._lock:
            for k in [k for k in self._adapters if k[0] == str(connection_id)]:
                self._adapters.pop(k).close()

    def close_all(self) -> None:
        with self._lock:
            for a in self._adapters.values():
                a.close()
            self._adapters.clear()


registry = Registry()


def encrypt_secret(conn_id: str, secret: dict[str, Any]):
    return keyring().seal(json.dumps(secret), f"connections:{conn_id}:secret")


def decrypt_secret(row: dict[str, Any]) -> dict[str, Any]:
    raw = keyring().open_str(bytes(row["secret_ciphertext"]), bytes(row["secret_nonce"]), int(row["secret_key_version"]),
                             f"connections:{row['id']}:secret")
    return json.loads(raw)


def store_wallet(conn_id: str, data: bytes) -> None:
    cfg = get_config()
    sealed = keyring().seal(data, f"wallet:{conn_id}")
    path = cfg.data_dir / "wallets" / f"{conn_id}.bin"
    path.write_text(json.dumps({"ct": base64.b64encode(sealed.ciphertext).decode(),
                                "nonce": base64.b64encode(sealed.nonce).decode(), "kv": sealed.key_version}))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def cleanup_wallet_temp() -> None:
    for p in Path(tempfile.gettempdir()).glob("sqlassist-wallet-*"):
        shutil.rmtree(p, ignore_errors=True)
