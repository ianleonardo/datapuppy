"""Unit tests: safeguards corpus, smells, classifier, LDAP escaping, crypto, budgeting, cron, theme."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.errors import ApiError
from app.services.safeguards import count_sql, page_sql, static_preflight, validate

FORBIDDEN = [
    ("DUCKDB", "INSERT INTO t VALUES (1)"), ("DUCKDB", "UPDATE t SET a = 1"), ("DUCKDB", "DELETE FROM t"),
    ("DUCKDB", "DROP TABLE t"), ("DUCKDB", "CREATE TABLE x AS SELECT 1"), ("DUCKDB", "ALTER TABLE t ADD c INT"),
    ("DUCKDB", "SELECT 1; SELECT 2"), ("DUCKDB", "COPY t TO 'x.csv'"), ("DUCKDB", "ATTACH 'x.db' AS x"),
    ("DUCKDB", "INSTALL httpfs"), ("DUCKDB", "LOAD httpfs"), ("DUCKDB", "SELECT * FROM read_parquet('x')"),
    ("DUCKDB", "SELECT * FROM glob('*')"), ("DUCKDB", "PRAGMA database_list"), ("DUCKDB", "SET threads = 1"),
    ("POSTGRES", "SELECT pg_sleep(10)"), ("POSTGRES", "SELECT pg_read_file('/etc/passwd')"),
    ("POSTGRES", "SELECT * INTO new_t FROM t"), ("POSTGRES", "SELECT * FROM t FOR UPDATE"),
    ("POSTGRES", "SELECT dblink('x', 'y')"), ("POSTGRES", "BEGIN"), ("POSTGRES", "GRANT SELECT ON t TO u"),
    ("MARIADB", "SELECT SLEEP(5)"), ("MARIADB", "SELECT BENCHMARK(1000, MD5('a'))"), ("MARIADB", "SELECT LOAD_FILE('/x')"),
    ("ORACLE", "SELECT DBMS_RANDOM.VALUE FROM dual"), ("ORACLE", "SELECT UTL_HTTP.REQUEST('x') FROM dual"),
    ("ORACLE", "MERGE INTO t USING s ON (t.id = s.id) WHEN MATCHED THEN UPDATE SET t.a = 1"),
    ("ORACLE", "TRUNCATE TABLE t"), ("POSTGRES", "WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x"),
]

ALLOWED = [
    ("DUCKDB", "SELECT 1"), ("DUCKDB", "WITH a AS (SELECT 1 AS x) SELECT x FROM a"),
    ("POSTGRES", "SELECT a FROM t UNION ALL SELECT b FROM u"), ("MARIADB", "SELECT * FROM t LIMIT 10"),
    ("ORACLE", "SELECT * FROM t FETCH FIRST 10 ROWS ONLY"),
    ("POSTGRES", "SELECT date_trunc('month', d) m, sum(x), lag(sum(x)) OVER (ORDER BY 1) FROM t GROUP BY 1;"),
]


@pytest.mark.parametrize("engine,sql", FORBIDDEN)
def test_forbidden_blocked(engine, sql):
    with pytest.raises(ApiError) as e:
        validate(sql, engine)
    assert e.value.code in ("E-EXE-001", "E-EXE-002", "E-EXE-003")


@pytest.mark.parametrize("engine,sql", ALLOWED)
def test_allowed(engine, sql):
    assert validate(sql, engine).sql


def test_syntax_error_has_position():
    with pytest.raises(ApiError) as e:
        validate("SELECT a FROM t WHERE (", "POSTGRES")
    assert e.value.code == "E-EXE-002" and e.value.extra["issues"][0]["line"] >= 1


def test_wrappers_oracle_and_count_strips_order():
    v = validate("SELECT a, b FROM t ORDER BY a", "ORACLE")
    assert "ORDER BY" not in count_sql(v).upper()
    sql = page_sql(v, [("b", "desc")], 50, 100, ["a"])
    assert "FETCH" in sql.upper() and "OFFSET" in sql.upper()
    v2 = validate("SELECT a FROM t", "POSTGRES")
    assert "LIMIT 50 OFFSET 100" in page_sql(v2, [], 50, 100, []).upper()


def test_preflight_oracle_limit_hint():
    res = static_preflight("SELECT * FROM t LIMIT 5", "ORACLE", {"t": {"a"}})
    assert any(i["code"] == "DIALECT" for i in res["issues"])


def test_preflight_ambiguous_and_cartesian():
    cat = {"a": {"id", "name"}, "b": {"id", "x"}}
    res = static_preflight("SELECT id FROM a JOIN b ON a.id = b.id", "POSTGRES", cat)
    assert any(i["code"] == "AMBIGUOUS_COLUMN" for i in res["issues"])
    res = static_preflight("SELECT a.id FROM a JOIN b", "MARIADB", cat)
    assert any(i["code"] == "CARTESIAN" for i in res["issues"]) or not res["ok"]


@pytest.mark.parametrize("name,flag,sev", [
    ("CUSTOMER_BCK", "SM-01", "HIGH"), ("TMP_ORDERS", "SM-02", "HIGH"), ("OLD_INVOICE", "SM-03", "HIGH"),
    ("ACCOUNTS_20251012", "SM-04", "MEDIUM"), ("SALES_202510", "SM-05", "MEDIUM"), ("ORDERS_COPY2", "SM-06", "MEDIUM"),
    ("TEST_LOAD", "SM-07", "MEDIUM"), ("DNU_RATES", "SM-08", "HIGH"), ("TXN_ARCHIVE", "SM-09", "MEDIUM"),
    ("BIN$abc==$0", "SM-10", "HIGH"),
])
def test_smells(name, flag, sev):
    from app.services.schema import detect_smells
    flags, _, s = detect_smells(name)
    assert flag in flags and s == sev


def test_invalid_date_suffix_not_flagged():
    from app.services.schema import detect_smells
    flags, _, _ = detect_smells("ACCOUNTS_20251399")
    assert "SM-04" not in flags


def test_classifier_and_sensitivity():
    from app.services.schema import classify, column_sensitivity
    fact = {"name": "FACT_SALES", "columns": [{"name": "amount", "type": "DECIMAL"}, {"name": "sale_date", "type": "DATE"}]}
    assert classify(fact, 2, 0)[0] == "FACT"
    dim = {"name": "DIM_CUSTOMER", "columns": [{"name": "id", "type": "INT"}]}
    assert classify(dim, 0, 3)[0] == "DIMENSION"
    assert column_sensitivity("CUSTOMER_NRIC") == "RESTRICTED"
    assert column_sensitivity("email") == "SENSITIVE"
    assert column_sensitivity("order_total") == "INTERNAL"


def test_ldap_escaping():
    from app.auth.ldap_auth import build_filter, escape_dn_value
    f = build_filter("(&(objectClass=user)(sAMAccountName={user_id}))", "a*)(uid=*")
    assert "*)(" not in f and "\\2a" in f
    assert escape_dn_value("a,b=c") == "a\\,b\\=c"


def test_ldap_role_mapping():
    from app.auth.ldap_auth import map_role
    cfg = {"admin_groups": ["CN=Admins"], "analyst_groups": ["CN=Analysts"], "country_group_map": {"CN=SG": "SG"}}
    assert map_role(cfg, ["cn=admins"])[0] == "SYSTEM_ADMIN"
    assert map_role(cfg, ["CN=Analysts", "CN=SG"]) == ("ANALYST", True, ["SG"])
    assert map_role(cfg, ["CN=Other"])[0] is None


def test_crypto_roundtrip_and_aad(tmp_path):
    from app.core.crypto import KeyRing
    ring = KeyRing.load_or_create(tmp_path / "k")
    s = ring.seal("secret", "t:1:f")
    assert ring.open_str(s.ciphertext, s.nonce, s.key_version, "t:1:f") == "secret"
    with pytest.raises(Exception):
        ring.open(s.ciphertext, s.nonce, s.key_version, "t:2:f")


def test_password_policy():
    from app.auth.passwords import check_policy
    with pytest.raises(ApiError):
        check_policy("alllowercaseletters", "u")
    with pytest.raises(ApiError):
        check_policy("Has-Alice-123!", "alice")
    check_policy("Correct-Horse-42!", "bob")


def test_theme_windows():
    from app.auth.service import compute_theme
    utc = timezone.utc
    assert compute_theme("Asia/Singapore", datetime(2026, 1, 1, 1, 0, tzinfo=utc)) == "morning"     # 09:00 SGT
    assert compute_theme("Asia/Singapore", datetime(2026, 1, 1, 6, 0, tzinfo=utc)) == "afternoon"   # 14:00 SGT
    assert compute_theme("Asia/Singapore", datetime(2026, 1, 1, 14, 0, tzinfo=utc)) == "night"      # 22:00 SGT
    assert compute_theme("Bad/Zone", datetime(2026, 1, 1, 3, 0, tzinfo=utc)) == "night"


def test_cron():
    from app.jobs.runner import cron_matches
    assert cron_matches("0 2 * * *", datetime(2026, 1, 1, 2, 0))
    assert not cron_matches("0 2 * * *", datetime(2026, 1, 1, 3, 0))
    assert cron_matches("*/15 * * * *", datetime(2026, 1, 1, 3, 45))


def test_budget_and_tokens():
    from app.services.llm import BaseProvider, fit, heuristic_tokens
    assert heuristic_tokens("abc" * 10) == 10 and heuristic_tokens("你好") == 2

    class P(BaseProvider):
        def __init__(self):
            self.max_model_len, self.max_output_tokens, self._tokenizer = 4000, 1000, None
    out = fit(P(), "x" * 300, [("schema", ["a" * 3000] * 5, 0.4), ("knowledge", ["b" * 300] * 50, 0.25)], "hi")
    assert 0 < len(out["schema"]) < 5 and len(out["knowledge"]) < 50
    with pytest.raises(ApiError):
        fit(P(), "", [], "z" * 20000)


def test_redaction():
    from app.core.logging import redact
    s = redact('Authorization: Bearer abc.def x-goog-api-key: KEY123 {"password": "hunter2"}')
    assert "abc.def" not in s and "KEY123" not in s and "hunter2" not in s


def test_rbac_matrix():
    from app.permissions import MATRIX, can
    for cap, roles in MATRIX.items():
        assert can("SYSTEM_ADMIN", cap)
        assert can("ANALYST", cap) == (roles["ANALYST"] != "no")


def test_chunking():
    from app.services.rag import chunk
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 200 for i in range(10))
    ch = chunk([{"text": text, "meta": {}}], 300, 50)
    assert len(ch) > 3 and all(c["tokens"] <= 400 for c in ch)


# ---------------------------------------------------------------- unknown names → similar suggestions (SQL editor)
NAME_CAT = {"orders": {"order_id", "customer_id", "order_date", "status"},
            "customers": {"customer_id", "name", "country_code"},
            "payments": {"payment_id", "order_id", "amount", "status"},
            "order_items": {"order_id", "line_no", "quantity"}}


def _unknown(sql):
    from app.services.safeguards import static_preflight
    r = static_preflight(sql, "DUCKDB", NAME_CAT, set())
    return {i["token"]: i["suggestions"] for i in r["issues"] if i["code"].startswith("UNKNOWN") and "token" in i}, r


def test_suggests_similar_table_and_columns():
    found, r = _unknown("SELECT ordr_id, statu FROM orderz WHERE statu = 'statu'")
    assert found["orderz"][0] == "orders"
    assert found["ordr_id"][0] == "order_id" and found["statu"][0] == "status"
    assert sum(1 for i in r["issues"] if i.get("token") == "statu") == 1          # reported once
    assert r["corrected_sql"] == "SELECT order_id, status FROM orders WHERE status = 'statu'"  # literal untouched


def test_suggests_plural_and_qualified_columns():
    found, r = _unknown("SELECT o.orderid, c.nam FROM orders o JOIN customer c ON c.customer_id = o.customer_id")
    assert found["customer"] == ["customers"]
    assert found["orderid"][0] == "order_id" and found["nam"] == ["name"]
    assert "JOIN customers c" in r["corrected_sql"] and "o.order_id" in r["corrected_sql"]


def test_ambiguous_or_weak_matches_are_only_suggested():
    found, r = _unknown("SELECT amt FROM payments")
    assert found["amt"] == ["amount"] and r["corrected_sql"] is None              # suggested, not auto-applied
    found, r = _unknown("SELECT zzz FROM payments")
    assert "zzz" not in found and r["ok"] is False                               # nothing similar: plain error


def test_excluded_tables_are_not_suggested():
    from app.services.safeguards import static_preflight
    r = static_preflight("SELECT 1 FROM order", "DUCKDB", NAME_CAT, {"orders"})
    sugg = [i for i in r["issues"] if i["code"] == "UNKNOWN_TABLE"][0].get("suggestions", [])
    assert "orders" not in sugg


def test_similar_names_proposals():
    from app.services.safeguards import similar_names, static_preflight
    cols = ["customer_id", "customer_name", "email", "phone", "country_code", "segment", "signup_date"]
    best = lambda w: (similar_names(w, cols) or [("", 0)])[0][0]  # noqa: E731
    assert best("emial") == "email" and best("segmnt") == "segment"
    assert best("name") == "customer_name" and best("nme") == "customer_name"
    assert best("id") == "customer_id" and best("date") == "signup_date"
    assert similar_names("xyz", cols) == []
    cat = {"users": {"id", "name", "email"}, "orders": {"id", "user_id", "amount"}, "order_items": {"order_id"}}
    for engine in ("DUCKDB", "POSTGRES", "ORACLE", "MARIADB"):
        r = static_preflight("SELECT u.nam FROM user u", engine, cat, set())
        tables = [i for i in r["issues"] if i["code"] == "UNKNOWN_TABLE"]
        assert tables and tables[0]["suggestions"][0] == "users" and "Did you mean users?" in tables[0]["message"]
        assert r["corrected_sql"] == "SELECT u.name FROM users u"
    # a whole-name match always outranks a part match
    assert similar_names("order", ["orders", "order_items"])[0][0] == "orders"


def test_keyword_typos_are_proposed():
    from app.services.safeguards import static_preflight
    cat = {"project_documents": {"id", "project", "title"}, "projects": {"id", "name"}}

    def run(sql):
        r = static_preflight(sql, "DUCKDB", cat, set())
        return {i["token"]: i["suggestions"] for i in r["issues"] if i["code"] in ("KEYWORD_TYPO", "BARE_AGGREGATE")}, r

    found, r = run("select count from project_documents were project = ''")
    assert found == {"were": ["where"], "count": ["count(*)"]}
    assert r["corrected_sql"] == "select count(*) from project_documents where project = ''"
    were = [i for i in r["issues"] if i.get("token") == "were"][0]
    assert (were["line"], were["col"], were["end_col"]) == (1, 37, 41)
    for bad, word, kw in [("selct * from projects", "selct", "select"), ("SELECT * FORM projects", "FORM", "FROM"),
                          ("select name from projects oder by name", "oder", "order"),
                          ("select name from projects grup by name", "grup", "group"),
                          ("select name from projects order bye name", "bye", "by")]:
        found, _ = run(bad)
        assert found.get(word) == [kw], (bad, found)
    # a valid statement is never "corrected": an alias that resembles a keyword stays an alias
    found, r = run("SELECT p.name FROM projects oder JOIN project_documents p ON p.id = oder.id")
    assert "oder" not in found
    # keyword and name mistakes together, positions mapped back to the original text
    _, r = run("select nme from projcts were id = 1")
    codes = {i["code"]: i["col"] for i in r["issues"]}
    assert codes["KEYWORD_TYPO"] == 25 and codes["UNKNOWN_TABLE"] == 17 and codes["UNKNOWN_COLUMN"] == 8
    assert r["corrected_sql"] == "select name from projects where id = 1"
