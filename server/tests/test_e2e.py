"""End-to-end: login → connection → crawl → review → lock → chat → execute → intercept → paginate → export → purge."""
from __future__ import annotations

import io
import json

import openpyxl  # noqa: F401  (optional; skip export content check if missing)
import pytest

from tests.conftest import admin_password, login, sse_events, wait_job

NEW_ADMIN_PW = "Correct-Horse-42!"
ANALYST_PW = "Analyst-Pass-2026!"
state: dict = {}


def test_bootstrap_admin_must_change_password(client, env):
    pw = admin_password(env)
    r = client.post("/api/v1/auth/login", json={"user_id": "admin", "password": pw, "tz": "Asia/Singapore"})
    assert r.status_code == 200
    body = r.json()
    assert body["must_change_pw"] is True
    assert body["theme"] in ("morning", "afternoon", "night")
    s = login(client, "admin", pw)
    bad = s.post("/api/v1/auth/change-password", json={"current_password": pw, "new_password": "short"})
    assert bad.status_code == 422 and bad.json()["code"] == "E-AUTH-006"
    ok = s.post("/api/v1/auth/change-password", json={"current_password": pw, "new_password": NEW_ADMIN_PW})
    assert ok.status_code == 200, ok.text
    assert not (env["cfg"].data_dir / "run" / "initial-admin-password.txt").exists()


def test_generic_login_errors_and_csrf(client):
    r = client.post("/api/v1/auth/login", json={"user_id": "nobody", "password": "x"})
    assert r.status_code == 401 and r.json()["title"] == "Invalid user ID or password."
    s = login(client, "admin", NEW_ADMIN_PW)
    r = client.post("/api/v1/chat/sessions", json={})  # no CSRF header
    assert r.status_code == 403
    assert s.post("/api/v1/chat/sessions", json={}).status_code == 200


def test_connection_crawl_review_lock(client, env):
    s = login(client, "admin", NEW_ADMIN_PW)
    bad = s.post("/api/v1/admin/connections", json={"name": "AppStore", "country_code": "SG", "engine": "DUCKDB",
                                                     "params": {"path": str(env["cfg"].db_path)}})
    assert bad.status_code == 422 and bad.json()["code"] == "E-CON-004"  # TR-CON-04 path guard
    r = s.post("/api/v1/admin/connections", json={"name": "Demo Sales", "country_code": "SG", "engine": "DUCKDB",
                                                   "params": {"path": str(env["demo"])}, "is_authorized": True,
                                                   "allow_result_values_to_llm": True})
    assert r.status_code == 200, r.text
    cid = state["cid"] = r.json()["id"]
    t = s.post(f"/api/v1/admin/connections/{cid}/test").json()
    assert t["ok"] is True and t["write_privilege"] is False
    jid = s.post(f"/api/v1/admin/schema/{cid}/crawl", json={}).json()["job_id"]
    job = wait_job(s, jid)
    assert job["status"] == "SUCCEEDED", job
    snap_id = job["result"]["snapshot_id"]
    snap = s.get(f"/api/v1/admin/schema/snapshots/{snap_id}").json()
    by = {t["table_name"]: t for t in snap["tables"]}
    assert by["customers_bck"]["inclusion"] == "EXCLUDED" and by["customers_bck"]["base_table_hint"] == "customers"
    assert by["tmp_orders"]["inclusion"] == "EXCLUDED"
    assert by["sales_20251012"]["inclusion"] == "PENDING_REVIEW"
    assert by["orders"]["inclusion"] == "INCLUDED"
    email = next(c for c in by["customers"]["columns"] if c["column_name"] == "email")
    assert email["sensitivity"] == "SENSITIVE"
    assert len(snap["relationships"]) >= 4
    r = s.post(f"/api/v1/admin/schema/snapshots/{snap_id}/lock")
    assert r.status_code == 409 and r.json()["code"] == "E-SCH-001"  # lock gate
    pending = [t["id"] for t in snap["tables"] if t["inclusion"] == "PENDING_REVIEW"]
    assert s.post(f"/api/v1/admin/schema/snapshots/{snap_id}/bulk-inclusion",
                  json={"table_ids": pending, "inclusion": "EXCLUDED"}).status_code == 200
    r = s.post(f"/api/v1/admin/schema/snapshots/{snap_id}/lock")
    assert r.status_code == 200, r.text
    assert r.json()["embedded"] is True and len(r.json()["prompt_digest"]) == 64
    erd = s.get(f"/api/v1/schema/{cid}/erd").json()
    assert erd["mermaid"].startswith("erDiagram") and "orders" in erd["tables"]


def test_create_analyst_and_grants(client):
    s = login(client, "admin", NEW_ADMIN_PW)
    r = s.post("/api/v1/admin/users", json={"user_id": "alice", "display_name": "Alice", "password": ANALYST_PW})
    assert r.status_code == 200, r.text
    state["alice"] = r.json()["id"]
    r = s.post("/api/v1/admin/users", json={"user_id": "bob", "display_name": "Bob", "password": ANALYST_PW})
    state["bob"] = r.json()["id"]
    assert s.put(f"/api/v1/admin/users/{state['alice']}/grants", json={"countries": ["SG"]}).status_code == 200
    assert s.put(f"/api/v1/admin/users/{state['bob']}/grants", json={"connections": [state["cid"]]}).status_code == 200
    a = login(client, "alice", ANALYST_PW)
    assert a.get("/api/v1/admin/users").status_code == 403
    me = a.get("/api/v1/me").json()
    assert [c["id"] for c in me["connections"]] == [state["cid"]]
    tree = a.get("/api/v1/schema/tree").json()
    names = {t["name"] for c in tree["countries"] for d in c["databases"] for sch in d["schemas"] for t in sch["tables"]}
    assert "orders" in names and "customers_bck" not in names


def test_chat_generate_execute_intercept_paginate_export(client):
    a = login(client, "alice", ANALYST_PW)
    sess = a.post("/api/v1/chat/sessions", json={"connection_id": state["cid"]}).json()
    state["sid"] = sess["id"]
    r = a.post(f"/api/v1/chat/sessions/{sess['id']}/messages", json={"prompt": "Show recent SG orders with amounts"})
    assert r.status_code == 200
    events = sse_events(r.text)
    kinds = [e for e, _ in events]
    assert kinds[0] == "message.started" and "sql.proposed" in kinds and kinds[-1] == "message.completed", kinds
    done = json.loads(events[-1][1])
    sql = done["artifacts"]["sql"]
    assert done["artifacts"]["mood"] == "happy"
    # DD-08: AI SQL needs the Execute click
    r = a.post("/api/v1/queries/execute", json={"connection_id": state["cid"], "sql": sql, "origin": "AI"})
    assert r.status_code == 409 and r.json()["code"] == "E-EXE-010"
    r = a.post("/api/v1/queries/execute", json={"connection_id": state["cid"], "sql": sql, "origin": "AI",
                                                "confirmed_by_user": True, "session_id": state["sid"]})
    assert r.status_code == 200, r.text
    assert r.json()["total_rows"] == 50 and len(r.json()["rows"]) == 50
    # size interception
    big = "SELECT * FROM orders"
    r = a.post("/api/v1/queries/execute", json={"connection_id": state["cid"], "sql": big, "origin": "MANUAL"})
    assert r.status_code == 409 and r.json()["code"] == "E-EXE-020" and r.json()["rows"] == 6000
    r = a.post("/api/v1/queries/execute", json={"connection_id": state["cid"], "sql": big, "origin": "MANUAL",
                                                "confirmed_large": True, "session_id": state["sid"]})
    assert r.status_code == 200
    run = state["run"] = r.json()["run_id"]
    assert r.json()["total_pages"] == 120
    p2 = a.get(f"/api/v1/results/{run}/rows", params={"page": 2, "page_size": 100, "sort": "order_id:desc"}).json()
    assert p2["page"] == 2 and len(p2["rows"]) == 100 and p2["rows"][0][0] == 25900
    assert a.get(f"/api/v1/results/{run}/rows", params={"sort": "nope:asc"}).status_code == 422
    ex = a.post(f"/api/v1/results/{run}/export").json()
    dl = a.get(ex["download_url"])
    assert dl.status_code == 200 and dl.content[:2] == b"PK"
    assert a.get(ex["download_url"]).status_code == 404  # one-time token
    wb = openpyxl.load_workbook(io.BytesIO(dl.content), read_only=True)
    assert wb.sheetnames == ["Data", "Query"]


def test_interpret_hygiene_review(client):
    a = login(client, "alice", ANALYST_PW)
    r = a.post(f"/api/v1/chat/sessions/{state['sid']}/messages",
               json={"prompt": "Give me insights", "intent": "INTERPRET_RESULT", "run_id": state["run"]})
    done = json.loads(sse_events(r.text)[-1][1])
    assert done["artifacts"]["insights"] and done["artifacts"]["mood"] == "excited"
    r = a.post(f"/api/v1/chat/sessions/{state['sid']}/messages", json={"prompt": "Any backup tables I should ignore?"})
    done = json.loads(sse_events(r.text)[-1][1])
    assert done["artifacts"]["intent"] == "HYGIENE_CHECK" and done["artifacts"]["mood"] == "sniffing"
    assert "hidden" in done["content"][0]["content"][0]["text"]  # analysts see counts only
    r = a.post(f"/api/v1/chat/sessions/{state['sid']}/messages",
               json={"prompt": "Check this SQL:\n```sql\nDELETE FROM orders\n```", "intent": "REVIEW_SQL"})
    done = json.loads(sse_events(r.text)[-1][1])
    assert done["artifacts"]["mood"] == "protective"


def test_preflight_and_forbidden(client):
    a = login(client, "alice", ANALYST_PW)
    pf = a.post("/api/v1/queries/preflight", json={"connection_id": state["cid"],
                                                   "sql": "SELECT o.nope FROM orders o JOIN ghosts g ON 1=1"}).json()
    codes = {i["code"] for i in pf["issues"]}
    assert not pf["ok"] and "UNKNOWN_TABLE" in codes and "UNKNOWN_COLUMN" in codes
    for bad in ["DROP TABLE orders", "SELECT 1; DELETE FROM orders", "SELECT * FROM read_csv('/etc/passwd')",
                "SELECT * FROM '/etc/passwd'", "ATTACH 'x.db'", "COPY orders TO 'x.csv'"]:
        r = a.post("/api/v1/queries/execute", json={"connection_id": state["cid"], "sql": bad, "origin": "MANUAL"})
        assert r.status_code == 422, (bad, r.text)


def test_cross_tenant_isolation(client):
    b = login(client, "bob", ANALYST_PW)
    assert b.get(f"/api/v1/chat/sessions/{state['sid']}").status_code == 404
    assert b.delete(f"/api/v1/chat/sessions/{state['sid']}").status_code == 404
    assert b.get(f"/api/v1/results/{state['run']}/rows").status_code == 410
    assert b.post(f"/api/v1/results/{state['run']}/export").status_code == 404
    assert b.get("/api/v1/chat/sessions").json()["items"] == []


def test_private_rag_isolation(client):
    a = login(client, "alice", ANALYST_PW)
    files = {"file": ("glossary.txt", b"Revenue means settled payment amount in SGD. GMV includes refunds.", "text/plain")}
    r = a.post("/api/v1/rag/documents", files=files, data={"scope": "PRIVATE"})
    assert r.status_code == 202, r.text
    assert wait_job(a, r.json()["job_id"])["status"] == "SUCCEEDED"
    doc = a.get(f"/api/v1/rag/documents/{r.json()['id']}").json()
    assert doc["status"] == "READY" and doc["chunk_count"] == 1
    dup = a.post("/api/v1/rag/documents", files=files, data={"scope": "PRIVATE"})
    assert dup.status_code == 409
    bad = a.post("/api/v1/rag/documents", files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert bad.status_code == 422 and bad.json()["code"] == "E-RAG-001"
    fake = a.post("/api/v1/rag/documents", files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    assert fake.json()["code"] == "E-RAG-003"
    shared = a.post("/api/v1/rag/documents", files=files, data={"scope": "GLOBAL"})
    assert shared.status_code == 403
    b = login(client, "bob", ANALYST_PW)
    assert b.get(f"/api/v1/rag/documents/{r.json()['id']}").status_code == 404
    assert b.get("/api/v1/rag/documents").json()["items"] == []
    # TR-RAG-02: identical-embedding PRIVATE chunk of another user must never be retrieved
    from app.services.rag import retrieve_knowledge
    from app.store.duck import store
    from app.store.repos import RequestContext
    import asyncio
    from tests.fake_llm import _vec
    ctx_b = RequestContext(user_ref=state["bob"], user_id="bob", role="ANALYST")
    q = "Revenue means settled payment amount in SGD. GMV includes refunds."
    res = asyncio.run(retrieve_knowledge(store(), ctx_b, q, _vec(q), "SG", state["cid"]))
    assert res == []


def test_lockout(client):
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"user_id": "bob", "password": "wrong"})
    r = client.post("/api/v1/auth/login", json={"user_id": "bob", "password": ANALYST_PW})
    assert r.status_code == 423 and r.json()["code"] == "E-AUTH-005"
    s = login(client, "admin", NEW_ADMIN_PW)
    assert s.post(f"/api/v1/admin/users/{state['bob']}/unlock").status_code == 200
    login(client, "bob", ANALYST_PW)


def test_audit_chain_and_admin_metadata(client, env):
    s = login(client, "admin", NEW_ADMIN_PW)
    items = s.get("/api/v1/admin/audit", params={"action": "QUERY_EXECUTION"}).json()["items"]
    statuses = {i["status"] for i in items}
    assert {"SUCCESS", "INTERCEPTED", "FAILED"} <= statuses
    meta = s.get("/api/v1/admin/chat-metadata").json()["items"]
    assert meta and set(meta[0]) == {"session_id", "owner", "created_at", "updated_at", "message_count"}
    from app.services.audit import verify_chain
    from app.store.duck import store
    res = verify_chain(store(), env["cfg"].log_dir)
    assert res["ok"], res
    sysinfo = s.get("/api/v1/admin/system").json()
    assert sysinfo["paths"]["data_dir"] == str(env["cfg"].data_dir)


def test_llm_provider_settings_and_secret_handling(client, env):
    s = login(client, "admin", NEW_ADMIN_PW)
    provs = s.get("/api/v1/admin/llm-providers").json()["items"]
    sp = next(p for p in provs if p["kind"] == "SPIRARE")
    assert sp["api_key_set"] is True and "api_key_ciphertext" not in sp
    assert sp["verify_ssl"] is False and sp["use_proxy"] is False and "TLS verification bypassed" in sp["warnings"]
    t = s.post(f"/api/v1/admin/llm-providers/{sp['id']}/test").json()
    assert t["chat"]["ok"] and t["embed_dim"] == 1024
    from tests.fake_llm import CALLS
    assert any(c.get("auth") == "Bearer test-key-123" for c in CALLS)
    log_text = "".join(p.read_text() for p in env["cfg"].log_dir.glob("*.log"))
    assert "test-key-123" not in log_text and NEW_ADMIN_PW not in log_text


def test_purge(client):
    s = login(client, "admin", NEW_ADMIN_PW)
    pv = s.post("/api/v1/admin/purge/preview", json={"scope": "CHAT", "date_to": "2099-12-31"}).json()
    assert pv["chat_sessions"] >= 1
    assert s.post("/api/v1/admin/purge/execute", json={"scope": "CHAT", "date_to": "2099-12-31"}).status_code == 422
    jid = s.post("/api/v1/admin/purge/execute", json={"scope": "CHAT", "date_to": "2099-12-31", "confirm": "PURGE"}).json()["job_id"]
    job = wait_job(s, jid)
    assert job["status"] == "SUCCEEDED" and job["result"]["remaining"]["chat_sessions"] == 0
    a = s.get("/api/v1/admin/audit", params={"action": "PURGE_EXECUTED"}).json()["items"]
    assert a


@pytest.mark.parametrize("path", ["/api/v1/admin/users", "/api/v1/admin/system", "/api/v1/admin/audit",
                                  "/api/v1/admin/llm-providers", "/api/v1/admin/connections", "/api/v1/admin/settings"])
def test_rbac_admin_only(client, path):
    a = login(client, "alice", ANALYST_PW)
    assert a.get(path).status_code == 403


def test_markdown_upload_and_viewer(client):
    a = login(client, "alice", ANALYST_PW)
    md = (b"# Glossary\n\nIntro line.\n\n## Revenue\n\nSettled payment amount.\n\n```sql\n# not a heading\nSELECT 1\n```\n"
          b"\n## GMV\n\nIncludes refunds.\n")
    r = a.post("/api/v1/rag/documents", files={"file": ("terms.md", md, "text/markdown")}, data={"scope": "PRIVATE"})
    assert r.status_code == 202, r.text
    assert wait_job(a, r.json()["job_id"])["status"] == "SUCCEEDED"
    doc_id = r.json()["id"]
    assert a.get(f"/api/v1/rag/documents/{doc_id}").json()["status"] == "READY"
    t = a.get(f"/api/v1/rag/documents/{doc_id}/text").json()
    assert t["text"] == md.decode() and t["truncated"] is False
    f = a.get(f"/api/v1/rag/documents/{doc_id}/file")
    assert f.status_code == 200 and f.content == md
    assert f.headers["x-frame-options"] == "SAMEORIGIN" and "frame-ancestors 'self'" in f.headers["content-security-policy"]
    assert "inline" in f.headers["content-disposition"]
    assert "attachment" in a.get(f"/api/v1/rag/documents/{doc_id}/file?download=true").headers["content-disposition"]
    assert a.get("/api/v1/rag/documents").headers["x-frame-options"] == "DENY"
    b = login(client, "bob", ANALYST_PW)
    assert b.get(f"/api/v1/rag/documents/{doc_id}/text").status_code == 404
    assert b.get(f"/api/v1/rag/documents/{doc_id}/file").status_code == 404


def test_markdown_sections_split_on_headings():
    from app.services.rag import markdown_sections
    secs = markdown_sections("# A\nx\n## B\ny\n```\n# code\n```\n### C\nz\n# D\nw")
    assert [s["meta"]["heading"] for s in secs] == ["A", "A > B", "A > B > C", "D"]
    assert "# code" in secs[1]["text"]


def test_csv_reference_rejected(client):
    a = login(client, "alice", ANALYST_PW)
    r = a.post("/api/v1/rag/documents", files={"file": ("terms.csv", b"term,meaning\nGMV,gross\n", "text/csv")},
               data={"scope": "PRIVATE"})
    assert r.status_code == 422 and r.json()["code"] == "E-RAG-001"


def test_guidelines_for_all_and_guideline_documents(client):
    import asyncio
    from app.services.orchestrator import _guidelines
    from app.services.rag import retrieve_knowledge
    from app.store.duck import store
    from app.store.repos import RequestContext
    from tests.fake_llm import _vec

    adm = login(client, "admin", NEW_ADMIN_PW)
    # a guideline with no country and no database applies everywhere
    r = adm.post("/api/v1/admin/guidelines", json={"title": "Fiscal year", "body_md": "FY starts 1 April.",
                                                     "country_code": "", "connection_id": None})
    assert r.status_code == 200, r.text
    g = [x for x in adm.get("/api/v1/admin/guidelines").json()["items"] if x["title"] == "Fiscal year"][0]
    assert g["country_code"] is None and g["connection_id"] is None
    conn = {"id": state["cid"], "country_code": "SG"}
    assert any("FY starts 1 April" in line for line in asyncio.run(_guidelines(store(), conn)))
    conn_other = {"id": "00000000-0000-0000-0000-000000000000", "country_code": "MY"}
    assert any("FY starts 1 April" in line for line in asyncio.run(_guidelines(store(), conn_other)))

    # guideline documents: admin-only, never private, embedded and retrieved separately from references
    text = b"# Refund policy\n\nRefunded payments never count as revenue. Use status SETTLED only for revenue totals.\n"
    files = {"file": ("revenue-policy.md", text, "text/markdown")}
    a = login(client, "alice", ANALYST_PW)
    assert a.post("/api/v1/rag/documents", files=files, data={"scope": "PRIVATE", "kind": "GUIDELINE"}).status_code == 403
    adm = login(client, "admin", NEW_ADMIN_PW)  # one shared test client: each login replaces the session
    bad = adm.post("/api/v1/rag/documents", files=files, data={"scope": "PRIVATE", "kind": "GUIDELINE"})
    assert bad.status_code == 422, bad.text
    up = adm.post("/api/v1/rag/documents", files=files, data={"scope": "GLOBAL", "kind": "GUIDELINE"})
    assert up.status_code == 202, up.text
    assert wait_job(adm, up.json()["job_id"])["status"] == "SUCCEEDED"
    gid = up.json()["id"]
    assert [d["id"] for d in adm.get("/api/v1/rag/documents?kind=GUIDELINE").json()["items"]] == [gid]
    a = login(client, "alice", ANALYST_PW)
    assert gid not in [d["id"] for d in a.get("/api/v1/rag/documents").json()["items"]]  # References panel
    assert a.get(f"/api/v1/rag/documents/{gid}/text").json()["text"].startswith("# Refund policy")

    ctx_b = RequestContext(user_ref=state["bob"], user_id="bob", role="ANALYST")
    q = "Refunded payments never count as revenue"
    hits = asyncio.run(retrieve_knowledge(store(), ctx_b, q, _vec(q), "SG", state["cid"], kind="GUIDELINE"))
    assert hits and hits[0]["filename"] == "revenue-policy.md"
    refs = asyncio.run(retrieve_knowledge(store(), ctx_b, q, _vec(q), "SG", state["cid"]))
    assert all(h["filename"] != "revenue-policy.md" for h in refs)


def test_query_history_is_private_and_deduplicated(client):
    a = login(client, "alice", ANALYST_PW)
    cid = state["cid"]
    run = lambda sql, origin="MANUAL": a.post("/api/v1/queries/execute",  # noqa: E731
                                              json={"connection_id": cid, "sql": sql, "origin": origin,
                                                    "confirmed_by_user": True})
    assert run("SELECT 1 AS hist_one").status_code == 200
    assert run("SELECT 2 AS hist_two", "AI").status_code == 200
    assert run("SELECT 1 AS hist_one").status_code == 200          # same SQL again
    items = a.get("/api/v1/queries/history", params={"connection_id": cid, "q": "hist_"}).json()["items"]
    assert [i["sql_text"] for i in items] == ["SELECT 1 AS hist_one", "SELECT 2 AS hist_two"]   # newest first, deduped
    assert items[0]["times_run"] == 2 and items[0]["status"] == "SUCCEEDED" and items[0]["connection_name"]
    manual = a.get("/api/v1/queries/history", params={"q": "hist_", "origin": "manual"}).json()["items"]
    assert [i["sql_text"] for i in manual] == ["SELECT 1 AS hist_one"]
    assert a.get("/api/v1/queries/history", params={"origin": "bogus"}).status_code == 422
    b = login(client, "bob", ANALYST_PW)
    assert b.get("/api/v1/queries/history", params={"q": "hist_"}).json()["items"] == []


def test_pinned_theme_survives_refresh(client):
    a = login(client, "alice", ANALYST_PW)
    for t in ("night", "afternoon", "morning"):
        assert a.put("/api/v1/me/preferences", json={"pinned_theme": t}).status_code == 200
        me = a.get("/api/v1/me").json()           # what a browser refresh loads
        assert me["theme"] == t and me["preferences"]["pinned_theme"] == t
    a.put("/api/v1/me/preferences", json={"pinned_theme": "night"})
    a2 = login(client, "alice", ANALYST_PW)        # a new sign-in also starts pinned
    assert a2.get("/api/v1/me").json()["theme"] == "night"
    assert a2.put("/api/v1/me/preferences", json={"pinned_theme": ""}).status_code == 200   # unpin
    assert "pinned_theme" not in a2.get("/api/v1/me").json()["preferences"]   # clock decides from next sign-in
