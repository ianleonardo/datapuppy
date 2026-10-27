"""Admin endpoints (TS §20.5, §18.4). All require SYSTEM_ADMIN."""
from __future__ import annotations

import base64
import csv
import io
import json
import shutil
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ...auth import ldap_auth
from ...auth import service as auth
from ...auth.deps import admin_ctx
from ...auth.passwords import check_policy, hash_password, random_password
from ...core.config import get_config
from ...core.crypto import keyring
from ...core.errors import ApiError
from ...core.logging import set_level
from ...jobs.runner import runner
from ...services import audit, purge, schema
from ...services.connectors import encrypt_secret, guard_duckdb_path, registry, store_wallet
from ...services.execution import in_pool
from ...services.llm import router as llm_router
from ...services.settings import RUNTIME_DEFAULTS, settings
from ...store.duck import store
from ...store.repos import RequestContext, chat_metadata, detach_connection_from_sessions, generation_success_rate

router = APIRouter(prefix="/admin", tags=["admin"])


def _a(ctx: RequestContext, action: str, context: dict, status: str = "SUCCESS"):
    return audit.record(store(), action=action, status=status, user_ref=ctx.user_ref, user_id=ctx.user_id, context=context)


# ============================================================================= users
class UserIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._@-]+$")
    display_name: str = Field(min_length=1, max_length=200)
    email: str | None = None
    role: str = Field(default="ANALYST", pattern="^(SYSTEM_ADMIN|ANALYST)$")
    auth_source: str = Field(default="LOCAL", pattern="^(LOCAL|LDAP)$")
    password: str | None = None


class UserPatch(BaseModel):
    display_name: str | None = None
    email: str | None = None
    role: str | None = Field(default=None, pattern="^(SYSTEM_ADMIN|ANALYST)$")
    is_active: bool | None = None


class GrantsIn(BaseModel):
    connections: list[str] = []
    countries: list[str] = []


@router.get("/users")
async def users(q: str | None = None, ctx: RequestContext = Depends(admin_ctx)):
    s = store()
    sql = "SELECT * FROM users"
    params: list = []
    if q:
        sql += " WHERE user_id ILIKE ? OR display_name ILIKE ?"
        params = [f"%{q}%", f"%{q}%"]
    rows_ = await s.fetchall(sql + " ORDER BY user_id", params)
    grants = await s.fetchall("SELECT user_ref, connection_id, country_code FROM user_grants")
    out = []
    for u in rows_:
        pu = auth.public_user(u)
        pu.pop("preferences", None)
        pu["grants"] = {"connections": [str(g["connection_id"]) for g in grants if str(g["user_ref"]) == str(u["id"])
                                        and g["connection_id"]],
                        "countries": [g["country_code"] for g in grants if str(g["user_ref"]) == str(u["id"]) and g["country_code"]]}
        out.append(pu)
    return {"items": out}


@router.post("/users")
async def create_user(body: UserIn, ctx: RequestContext = Depends(admin_ctx)):
    s = store()
    uid = body.user_id.casefold()
    if await s.fetchone("SELECT 1 AS x FROM users WHERE user_id = ?", [uid]):
        raise ApiError("E-VAL-001", detail="User ID already exists")
    temp = None
    pw_hash = None
    if body.auth_source == "LOCAL":
        temp = body.password or random_password(16)
        if body.password:
            check_policy(body.password, uid, int(settings.get("auth.password_min_length", 12)))
        pw_hash = hash_password(temp)
    nid = str(uuid.uuid4())
    await s.execute("INSERT INTO users(id, user_id, display_name, email, role, auth_source, password_hash, must_change_pw) "
                    "VALUES (?,?,?,?,?,?,?,?)", [nid, uid, body.display_name, body.email, body.role, body.auth_source,
                                                 pw_hash, body.auth_source == "LOCAL"])
    await _a(ctx, "USER_ADMIN", {"op": "create", "target": uid, "role": body.role, "auth_source": body.auth_source})
    return {"id": nid, "temporary_password": temp if not body.password else None}


@router.patch("/users/{uid}")
async def patch_user(uid: str, body: UserPatch, ctx: RequestContext = Depends(admin_ctx)):
    s = store()
    u = await s.fetchone("SELECT * FROM users WHERE id = ?", [uid])
    if not u:
        raise ApiError("E-NF-001")
    patch = body.model_dump(exclude_unset=True)
    if "role" in patch and u["role_from_ldap"]:
        raise ApiError("E-VAL-001", detail="Role is managed by LDAP group mapping")
    if uid == ctx.user_ref and (patch.get("role") == "ANALYST" or patch.get("is_active") is False):
        raise ApiError("E-VAL-001", detail="You can't demote or deactivate yourself")
    for k, v in patch.items():
        await s.execute(f"UPDATE users SET {k} = ?, updated_at = now() WHERE id = ?", [v, uid])
    if patch.get("is_active") is False or "role" in patch:
        await auth.revoke_user(s, uid)
    await _a(ctx, "USER_ADMIN", {"op": "update", "target": u["user_id"], "changes": patch})
    return {"ok": True}


@router.post("/users/{uid}/reset-password")
async def reset_password(uid: str, ctx: RequestContext = Depends(admin_ctx)):
    s = store()
    u = await s.fetchone("SELECT * FROM users WHERE id = ?", [uid])
    if not u or u["auth_source"] != "LOCAL":
        raise ApiError("E-VAL-001", detail="Only local accounts have passwords here")
    temp = random_password(16)
    await s.execute("UPDATE users SET password_hash = ?, must_change_pw = true, failed_logins = 0, locked_until = NULL "
                    "WHERE id = ?", [hash_password(temp), uid])
    await auth.revoke_user(s, uid)
    await _a(ctx, "USER_ADMIN", {"op": "reset_password", "target": u["user_id"]})
    return {"temporary_password": temp}


@router.post("/users/{uid}/unlock")
async def unlock(uid: str, ctx: RequestContext = Depends(admin_ctx)):
    s = store()
    await s.execute("UPDATE users SET failed_logins = 0, locked_until = NULL WHERE id = ?", [uid])
    await _a(ctx, "USER_ADMIN", {"op": "unlock", "target_ref": uid})
    return {"ok": True}


@router.put("/users/{uid}/grants")
async def set_grants(uid: str, body: GrantsIn, ctx: RequestContext = Depends(admin_ctx)):
    s = store()

    def unit(c):
        c.execute("DELETE FROM user_grants WHERE user_ref = ?", [uid])
        for cid in sorted(set(body.connections)):
            c.execute("INSERT INTO user_grants(user_ref, connection_id) VALUES (?, ?)", [uid, cid])
        for cc in sorted(set(body.countries)):
            c.execute("INSERT INTO user_grants(user_ref, country_code) VALUES (?, ?)", [uid, cc])
    await s.write(unit)
    await _a(ctx, "USER_ADMIN", {"op": "grants", "target_ref": uid, **body.model_dump()})
    return {"ok": True}


# ============================================================================= auth settings & LDAP
class AuthSettingsIn(BaseModel):
    values: dict[str, Any]
    ldap_bind_password: str | None = None


AUTH_KEYS = {"auth.local_enabled", "auth.ldap_enabled", "auth.lockout_threshold", "auth.lockout_minutes",
             "auth.password_min_length", "auth.password_history", "auth.password_expiry_days", "auth.session_idle_min",
             "auth.session_absolute_h", "ldap"}


@router.get("/auth/settings")
async def get_auth_settings(ctx: RequestContext = Depends(admin_ctx)):
    s = store()
    has_pw = bool(await s.fetchone("SELECT 1 AS x FROM app_settings WHERE key = 'ldap.bind_password'"))
    return {"values": {k: settings.get(k) for k in sorted(AUTH_KEYS)}, "ldap_bind_password_set": has_pw}


@router.put("/auth/settings")
async def put_auth_settings(body: AuthSettingsIn, ctx: RequestContext = Depends(admin_ctx)):
    s = store()
    changed = {}
    for k, v in body.values.items():
        if k not in AUTH_KEYS:
            raise ApiError("E-VAL-001", detail=f"Unknown auth setting {k}")
        if k == "ldap":
            v = {**RUNTIME_DEFAULTS["ldap"], **(settings.get("ldap") or {}), **v}
        old = settings.get(k)
        await settings.set(s, k, v, ctx.user_ref)
        changed[k] = {"old": old, "new": v}
    if body.ldap_bind_password:
        sealed = keyring().seal(body.ldap_bind_password, "app_settings:ldap:bind_password")
        await settings.set(s, "ldap.bind_password", {"ct": base64.b64encode(sealed.ciphertext).decode(),
                                                      "nonce": base64.b64encode(sealed.nonce).decode(),
                                                      "kv": sealed.key_version}, ctx.user_ref)
        changed["ldap.bind_password"] = "updated"
    if (settings.get("ldap") or {}).get("verify_ssl") is False:
        changed["warning"] = "LDAP TLS verification bypassed"
    await _a(ctx, "AUTH_CONFIG_CHANGE", {"changes": changed})
    return {"ok": True}


class LdapTestIn(BaseModel):
    test_user: str | None = None


@router.post("/auth/ldap/test")
async def ldap_test(body: LdapTestIn, ctx: RequestContext = Depends(admin_ctx)):
    try:
        return await in_pool(ldap_auth.test_connection, settings.get("ldap") or {}, auth._ldap_bind_password(store()),
                             body.test_user)
    except ApiError as e:
        return {"ok": False, "detail": e.message}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": type(e).__name__ + ": " + str(e)[:200]}


# ============================================================================= countries / guidelines / starter prompts
class CountryIn(BaseModel):
    code: str = Field(min_length=2, max_length=2)
    name: str
    is_active: bool = True


@router.get("/countries")
async def countries(ctx: RequestContext = Depends(admin_ctx)):
    return {"items": await store().fetchall("SELECT * FROM countries ORDER BY code")}


@router.post("/countries")
async def upsert_country(body: CountryIn, ctx: RequestContext = Depends(admin_ctx)):
    await store().execute("INSERT INTO countries VALUES (?, ?, ?) ON CONFLICT (code) DO UPDATE SET name = excluded.name, "
                          "is_active = excluded.is_active", [body.code.upper(), body.name, body.is_active])
    await _a(ctx, "SETTINGS_CHANGE", {"countries": body.model_dump()})
    return {"ok": True}


@router.delete("/countries/{code}")
async def delete_country(code: str, ctx: RequestContext = Depends(admin_ctx)):
    s = store()
    if await s.fetchone("SELECT 1 AS x FROM connections WHERE country_code = ?", [code]):
        raise ApiError("E-VAL-001", detail="Country still has connections")
    await s.execute("DELETE FROM user_grants WHERE country_code = ?", [code])
    await s.execute("DELETE FROM countries WHERE code = ?", [code])
    await _a(ctx, "SETTINGS_CHANGE", {"countries": {"deleted": code}})
    return {"ok": True}


class GuidelineIn(BaseModel):
    country_code: str | None = None
    connection_id: str | None = None
    title: str
    body_md: str
    priority: int = 100
    is_active: bool = True


@router.get("/guidelines")
async def guidelines(ctx: RequestContext = Depends(admin_ctx)):
    return {"items": await store().fetchall("SELECT * FROM guidelines ORDER BY priority, title")}


@router.post("/guidelines")
async def create_guideline(body: GuidelineIn, ctx: RequestContext = Depends(admin_ctx)):
    if not body.country_code and not body.connection_id:
        raise ApiError("E-VAL-001", detail="Attach the guideline to a country or a connection")
    gid = str(uuid.uuid4())
    await store().execute("INSERT INTO guidelines VALUES (?,?,?,?,?,?,?)",
                          [gid, body.country_code, body.connection_id, body.title, body.body_md, body.priority, body.is_active])
    await _a(ctx, "SETTINGS_CHANGE", {"guideline": {"op": "create", "id": gid, "title": body.title}})
    return {"id": gid}


@router.put("/guidelines/{gid}")
async def update_guideline(gid: str, body: GuidelineIn, ctx: RequestContext = Depends(admin_ctx)):
    await store().execute("UPDATE guidelines SET country_code=?, connection_id=?, title=?, body_md=?, priority=?, is_active=? "
                          "WHERE id = ?", [body.country_code, body.connection_id, body.title, body.body_md, body.priority,
                                           body.is_active, gid])
    await _a(ctx, "SETTINGS_CHANGE", {"guideline": {"op": "update", "id": gid}})
    return {"ok": True}


@router.delete("/guidelines/{gid}")
async def delete_guideline(gid: str, ctx: RequestContext = Depends(admin_ctx)):
    await store().execute("DELETE FROM guidelines WHERE id = ?", [gid])
    await _a(ctx, "SETTINGS_CHANGE", {"guideline": {"op": "delete", "id": gid}})
    return {"ok": True}


class StarterIn(BaseModel):
    items: list[dict[str, Any]]


@router.get("/starter-prompts")
async def get_starters(ctx: RequestContext = Depends(admin_ctx)):
    return {"items": await store().fetchall("SELECT * FROM starter_prompts ORDER BY sort_order")}


@router.put("/starter-prompts")
async def put_starters(body: StarterIn, ctx: RequestContext = Depends(admin_ctx)):
    def unit(c):
        c.execute("DELETE FROM starter_prompts")
        for i, it in enumerate(body.items, 1):
            c.execute("INSERT INTO starter_prompts VALUES (?,?,?,?,?,?)",
                      [i, it["label"], it["prompt"], it.get("intent", "GENERATE_SQL"), i, it.get("is_active", True)])
    await store().write(unit)
    await _a(ctx, "SETTINGS_CHANGE", {"starter_prompts": len(body.items)})
    return {"ok": True}


# ============================================================================= connections
class ConnectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    country_code: str = Field(min_length=2, max_length=2)
    engine: str = Field(pattern="^(ORACLE|MARIADB|POSTGRES|DUCKDB)$")
    params: dict[str, Any] = {}
    secret: dict[str, Any] | None = None      # username/password/wallet_password — write-only
    is_authorized: bool = False
    allow_result_values_to_llm: bool = False
    limits_override: dict[str, Any] | None = None


def _public_conn(c: dict) -> dict:
    return {k: (json.loads(v) if k in ("params", "limits_override") and isinstance(v, str) else v)
            for k, v in c.items() if not k.startswith("secret_")}


@router.get("/connections")
async def connections(ctx: RequestContext = Depends(admin_ctx)):
    rows_ = await store().fetchall("SELECT * FROM connections ORDER BY country_code, name")
    out = []
    for c in rows_:
        pc = _public_conn(c)
        snap = schema.locked_snapshot(store(), str(c["id"]))
        pc["locked_snapshot"] = {"id": str(snap["id"]), "version": snap["version"]} if snap else None
        out.append(pc)
    return {"items": out}


def _validate_params(engine: str, params: dict) -> None:
    need = {"POSTGRES": ["host", "database"], "MARIADB": ["host"], "DUCKDB": [], "ORACLE": []}[engine]
    for k in need:
        if not params.get(k):
            raise ApiError("E-VAL-001", detail=f"params.{k} is required")
    if engine == "DUCKDB":
        if params.get("path") and params["path"] != ":memory:":
            guard_duckdb_path(params["path"])
        for att in params.get("attach", []):
            guard_duckdb_path(att.get("path", ""))
    if engine == "ORACLE":
        mode = params.get("mode", "easy")
        req = {"easy": ["host", "service_name"], "tns": ["tns_alias", "tns_admin"], "wallet": ["tns_alias"]}[mode]
        for k in req:
            if not params.get(k):
                raise ApiError("E-VAL-001", detail=f"params.{k} is required for Oracle {mode} mode")


@router.post("/connections")
async def create_connection(body: ConnectionIn, ctx: RequestContext = Depends(admin_ctx)):
    _validate_params(body.engine, body.params)
    cid = str(uuid.uuid4())
    sealed = encrypt_secret(cid, body.secret or {})
    try:
        await store().execute(
            "INSERT INTO connections(id, name, country_code, engine, params, secret_ciphertext, secret_nonce, secret_key_version, "
            "is_authorized, allow_result_values_to_llm, limits_override, created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [cid, body.name, body.country_code.upper(), body.engine, json.dumps(body.params), sealed.ciphertext, sealed.nonce,
             sealed.key_version, body.is_authorized, body.allow_result_values_to_llm,
             json.dumps(body.limits_override) if body.limits_override else None, ctx.user_ref])
    except Exception as e:  # noqa: BLE001
        await _a(ctx, "DB_CONNECTION_CREATE", {"name": body.name, "error": str(e)[:200]}, "FAILED")
        raise ApiError("E-VAL-001", detail="A connection with that name already exists in this country") from e
    await _a(ctx, "DB_CONNECTION_CREATE", {"connection_id": cid, "name": body.name, "engine": body.engine,
                                           "country": body.country_code, "params": body.params})
    return {"id": cid}


@router.put("/connections/{cid}")
async def update_connection(cid: str, body: ConnectionIn, ctx: RequestContext = Depends(admin_ctx)):
    s = store()
    old = await s.fetchone("SELECT * FROM connections WHERE id = ?", [cid])
    if not old:
        raise ApiError("E-NF-001")
    _validate_params(body.engine, body.params)
    sets = ("name = ?, country_code = ?, engine = ?, params = ?, is_authorized = ?, allow_result_values_to_llm = ?, "
            "limits_override = ?, updated_at = now()")
    vals: list = [body.name, body.country_code.upper(), body.engine, json.dumps(body.params), body.is_authorized,
                  body.allow_result_values_to_llm, json.dumps(body.limits_override) if body.limits_override else None]
    if body.secret:  # write-only: only replaced when provided
        sealed = encrypt_secret(cid, body.secret)
        sets += ", secret_ciphertext = ?, secret_nonce = ?, secret_key_version = ?"
        vals += [sealed.ciphertext, sealed.nonce, sealed.key_version]
    await s.execute(f"UPDATE connections SET {sets} WHERE id = ?", vals + [cid])
    registry.invalidate(cid)  # editing a connection invalidates its pool
    await _a(ctx, "DB_CONNECTION_EDIT", {"connection_id": cid, "name": body.name, "secret_changed": bool(body.secret),
                                         "authorized": body.is_authorized, "llm_values": body.allow_result_values_to_llm})
    return {"ok": True}


@router.delete("/connections/{cid}")
async def delete_connection(cid: str, ctx: RequestContext = Depends(admin_ctx)):
    s = store()

    def unit(c):  # application-level cascade
        snaps = [str(r[0]) for r in c.execute("SELECT id FROM schema_snapshots WHERE connection_id = ?", [cid]).fetchall()]
        for sid in snaps:
            c.execute("DELETE FROM schema_columns WHERE table_id IN (SELECT id FROM schema_tables WHERE snapshot_id = ?)", [sid])
            c.execute("DELETE FROM schema_relationships WHERE snapshot_id = ?", [sid])
            c.execute("DELETE FROM schema_tables WHERE snapshot_id = ?", [sid])
        c.execute("DELETE FROM schema_snapshots WHERE connection_id = ?", [cid])
        c.execute("DELETE FROM schema_cards WHERE connection_id = ?", [cid])
        c.execute("DELETE FROM guidelines WHERE connection_id = ?", [cid])
        c.execute("DELETE FROM user_grants WHERE connection_id = ?", [cid])
        detach_connection_from_sessions(c, cid)
        c.execute("DELETE FROM connections WHERE id = ?", [cid])
    await s.write(unit)
    registry.invalidate(cid)
    await _a(ctx, "DB_CONNECTION_DELETE", {"connection_id": cid})
    return {"ok": True}


@router.post("/connections/{cid}/wallet")
async def upload_wallet(cid: str, file: UploadFile = File(...), ctx: RequestContext = Depends(admin_ctx)):
    data = await file.read()
    if not data.startswith(b"PK"):
        raise ApiError("E-CON-002")
    store_wallet(cid, data)
    registry.invalidate(cid)
    await _a(ctx, "DB_CONNECTION_EDIT", {"connection_id": cid, "wallet": "uploaded"})
    return {"ok": True}


@router.post("/connections/{cid}/test")
async def test_connection(cid: str, ctx: RequestContext = Depends(admin_ctx)):
    s = store()
    c = await s.fetchone("SELECT * FROM connections WHERE id = ?", [cid])
    if not c:
        raise ApiError("E-NF-001")
    registry.invalidate(cid)
    try:
        res = await in_pool(registry.get(c).test)
        status = "OK" + (" (write privilege!)" if res.get("write_privilege") else "")
        await _a(ctx, "DB_CONNECTION_TEST", {"connection_id": cid, **res})
    except ApiError as e:
        res = {"ok": False, "error": e.message, "code": e.code}
        status = e.code
        await _a(ctx, "DB_CONNECTION_TEST", {"connection_id": cid, "error": e.message}, "FAILED")
    await s.execute("UPDATE connections SET last_test_status = ?, last_tested_at = now() WHERE id = ?", [status, cid])
    return res


# ============================================================================= schema governance
class CrawlIn(BaseModel):
    include_schemas: list[str] | None = None


class TablePatch(BaseModel):
    alias: str | None = None
    description: str | None = None
    classification: str | None = Field(default=None, pattern="^(FACT|DIMENSION|STAGING|BACKUP|TEMP|ARCHIVE|UNKNOWN)$")
    inclusion: str | None = Field(default=None, pattern="^(INCLUDED|EXCLUDED|PENDING_REVIEW)$")


class ColumnPatch(BaseModel):
    alias: str | None = None
    description: str | None = None
    sensitivity: str | None = Field(default=None, pattern="^(PUBLIC|INTERNAL|SENSITIVE|RESTRICTED)$")
    is_masked: bool | None = None


class RelIn(BaseModel):
    from_table_id: str
    from_columns: list[str]
    to_table_id: str
    to_columns: list[str]
    name: str | None = None


@router.post("/schema/{cid}/crawl")
async def crawl(cid: str, body: CrawlIn, ctx: RequestContext = Depends(admin_ctx)):
    s = store()
    if await s.fetchone("SELECT 1 AS x FROM jobs WHERE type = 'crawl_schema' AND status IN ('QUEUED','RUNNING') AND "
                        "json_extract_string(payload, '$.connection_id') = ?", [cid]):
        raise ApiError("E-SCH-002")
    jid = await runner().enqueue("crawl_schema", {"connection_id": cid, "include_schemas": body.include_schemas,
                                                  "user_ref": ctx.user_ref, "user_id": ctx.user_id}, ctx.user_ref,
                                 idempotency_key=f"crawl:{cid}", max_attempts=1)
    return {"job_id": jid}


@router.get("/schema/{cid}/snapshots")
async def snapshots(cid: str, ctx: RequestContext = Depends(admin_ctx)):
    return {"items": await store().fetchall("SELECT id, version, state, stats, locked_at, prompt_digest, created_at FROM "
                                            "schema_snapshots WHERE connection_id = ? ORDER BY version DESC", [cid])}


@router.get("/schema/snapshots/{sid}")
async def snapshot(sid: str, ctx: RequestContext = Depends(admin_ctx)):
    s = store()
    snap = await s.fetchone("SELECT * FROM schema_snapshots WHERE id = ?", [sid])
    if not snap:
        raise ApiError("E-NF-001")
    tables = schema.snapshot_tables(s, sid)
    cols = schema.table_columns(s, [str(t["id"]) for t in tables])
    for t in tables:
        t["columns"] = cols.get(str(t["id"]), [])
    return {**snap, "tables": tables, "relationships": schema.relationships(s, sid)}


async def _editable_table(tid: str) -> dict:
    t = await store().fetchone("SELECT t.*, s.state FROM schema_tables t JOIN schema_snapshots s ON s.id = t.snapshot_id "
                               "WHERE t.id = ?", [tid])
    if not t:
        raise ApiError("E-NF-001")
    if t["state"] != "DRAFT":
        raise ApiError("E-VAL-001", detail="Unlock to a new draft before editing")
    return t


@router.patch("/schema/tables/{tid}")
async def patch_table(tid: str, body: TablePatch, ctx: RequestContext = Depends(admin_ctx)):
    t = await _editable_table(tid)
    patch = body.model_dump(exclude_unset=True)
    sets = [f"{k} = ?" for k in patch] + ["decided_by = ?", "decided_at = now()"]
    await store().execute(f"UPDATE schema_tables SET {', '.join(sets)} WHERE id = ?", list(patch.values()) + [ctx.user_ref, tid])
    await _a(ctx, "SCHEMA_EDIT", {"table": t["table_name"], "changes": patch})
    return {"ok": True}


@router.patch("/schema/columns/{col_id}")
async def patch_column(col_id: str, body: ColumnPatch, ctx: RequestContext = Depends(admin_ctx)):
    c = await store().fetchone("SELECT table_id, column_name FROM schema_columns WHERE id = ?", [col_id])
    if not c:
        raise ApiError("E-NF-001")
    t = await _editable_table(str(c["table_id"]))
    patch = body.model_dump(exclude_unset=True)
    if patch:
        await store().execute(f"UPDATE schema_columns SET {', '.join(f'{k} = ?' for k in patch)} WHERE id = ?",
                              list(patch.values()) + [col_id])
    await _a(ctx, "SCHEMA_EDIT", {"table": t["table_name"], "column": c["column_name"], "changes": patch})
    return {"ok": True}


@router.post("/schema/snapshots/{sid}/bulk-inclusion")
async def bulk_inclusion(sid: str, body: dict, ctx: RequestContext = Depends(admin_ctx)):
    ids, inclusion = body.get("table_ids", []), body.get("inclusion")
    if inclusion not in ("INCLUDED", "EXCLUDED", "PENDING_REVIEW"):
        raise ApiError("E-VAL-001", detail="bad inclusion")
    await store().execute("UPDATE schema_tables SET inclusion = ?, decided_by = ?, decided_at = now() WHERE snapshot_id = ? "
                          "AND list_contains(?, CAST(id AS VARCHAR))", [inclusion, ctx.user_ref, sid, ids])
    await _a(ctx, "SCHEMA_EDIT", {"snapshot_id": sid, "bulk": inclusion, "count": len(ids)})
    return {"ok": True}


@router.post("/schema/snapshots/{sid}/relationships")
async def add_rel(sid: str, body: RelIn, ctx: RequestContext = Depends(admin_ctx)):
    await store().execute("INSERT INTO schema_relationships(snapshot_id, from_table_id, from_columns, to_table_id, to_columns, "
                          "source, name) VALUES (?,?,?,?,?, 'ADMIN_DEFINED', ?)",
                          [sid, body.from_table_id, body.from_columns, body.to_table_id, body.to_columns, body.name])
    await _a(ctx, "SCHEMA_EDIT", {"snapshot_id": sid, "relationship": body.model_dump()})
    return {"ok": True}


@router.delete("/schema/snapshots/{sid}/relationships/{rid}")
async def del_rel(sid: str, rid: str, ctx: RequestContext = Depends(admin_ctx)):
    await store().execute("DELETE FROM schema_relationships WHERE id = ? AND snapshot_id = ?", [rid, sid])
    await _a(ctx, "SCHEMA_EDIT", {"snapshot_id": sid, "relationship_deleted": rid})
    return {"ok": True}


@router.post("/schema/snapshots/{sid}/lock")
async def lock(sid: str, ctx: RequestContext = Depends(admin_ctx)):
    try:
        res = await schema.lock_snapshot(store(), sid, ctx.user_ref, llm_router.embedder(store()))
    except ApiError as e:
        await _a(ctx, "SCHEMA_LOCK", {"snapshot_id": sid, "error": e.code}, "FAILED")
        raise
    await _a(ctx, "SCHEMA_LOCK", res)
    return res


@router.post("/schema/snapshots/{sid}/unlock")
async def unlock_snapshot(sid: str, ctx: RequestContext = Depends(admin_ctx)):
    new_id = await schema.unlock_to_draft(store(), sid)
    await _a(ctx, "SCHEMA_EDIT", {"snapshot_id": sid, "unlocked_to": new_id})
    return {"draft_id": new_id}


@router.get("/schema/snapshots/{sid}/diff")
async def snapshot_diff(sid: str, ctx: RequestContext = Depends(admin_ctx)):
    return schema.diff(store(), sid)


# ============================================================================= LLM providers
class ProviderIn(BaseModel):
    kind: str = Field(pattern="^(GEMINI|SPIRARE)$")
    display_name: str
    chat_url: str
    chat_model: str
    embed_url: str | None = None
    embed_model: str | None = None
    max_model_len: int | None = Field(default=None, ge=1024)
    max_output_tokens: int | None = Field(default=None, ge=16)
    verify_ssl: bool = True
    use_proxy: bool = True
    proxy_url: str | None = None
    ca_bundle_path: str | None = None
    timeout_s: int = Field(default=120, ge=5, le=900)
    params: dict[str, Any] = {}
    api_key: str | None = None   # write-only; empty string clears
    is_enabled: bool = True


PROV_FIELDS = ("kind", "display_name", "chat_url", "chat_model", "embed_url", "embed_model", "max_model_len",
               "max_output_tokens", "verify_ssl", "use_proxy", "proxy_url", "ca_bundle_path", "timeout_s", "params",
               "is_enabled")


def _public_provider(r: dict) -> dict:
    out = {k: v for k, v in r.items() if not k.startswith("api_key_")}
    out["api_key_set"] = bool(r.get("api_key_ciphertext"))
    for k in ("params", "last_test"):
        if isinstance(out.get(k), str):
            out[k] = json.loads(out[k])
    out["warnings"] = (["TLS verification bypassed"] if not r["verify_ssl"] else []) + \
                      (["Proxy bypassed"] if not r["use_proxy"] else [])
    return out


@router.get("/llm-providers")
async def providers(ctx: RequestContext = Depends(admin_ctx)):
    return {"items": [_public_provider(r) for r in await store().fetchall("SELECT * FROM llm_providers ORDER BY kind")]}


async def _save_key(pid: str, key: str | None) -> None:
    if key is None:
        return
    if key == "":
        await store().execute("UPDATE llm_providers SET api_key_ciphertext = NULL, api_key_nonce = NULL, api_key_version = NULL "
                              "WHERE id = ?", [pid])
        return
    sealed = keyring().seal(key, f"llm_providers:{pid}:api_key")
    await store().execute("UPDATE llm_providers SET api_key_ciphertext = ?, api_key_nonce = ?, api_key_version = ? WHERE id = ?",
                          [sealed.ciphertext, sealed.nonce, sealed.key_version, pid])


@router.post("/llm-providers")
async def create_provider(body: ProviderIn, ctx: RequestContext = Depends(admin_ctx)):
    pid = str(uuid.uuid4())
    vals = [getattr(body, f) if f != "params" else json.dumps(body.params) for f in PROV_FIELDS]
    await store().execute(f"INSERT INTO llm_providers(id, {', '.join(PROV_FIELDS)}) VALUES (?, {', '.join('?' for _ in PROV_FIELDS)})",
                          [pid] + vals)
    await _save_key(pid, body.api_key)
    llm_router.invalidate()
    await _a(ctx, "LLM_CONFIG_CHANGE", {"op": "create", "provider": pid, "kind": body.kind, "verify_ssl": body.verify_ssl,
                                        "use_proxy": body.use_proxy, "api_key": "set" if body.api_key else "unchanged"})
    return {"id": pid}


@router.put("/llm-providers/{pid}")
async def update_provider(pid: str, body: ProviderIn, ctx: RequestContext = Depends(admin_ctx)):
    s = store()
    old = await s.fetchone("SELECT * FROM llm_providers WHERE id = ?", [pid])
    if not old:
        raise ApiError("E-NF-001")
    vals = [getattr(body, f) if f != "params" else json.dumps(body.params) for f in PROV_FIELDS]
    await s.execute(f"UPDATE llm_providers SET {', '.join(f'{f} = ?' for f in PROV_FIELDS)} WHERE id = ?", vals + [pid])
    await _save_key(pid, body.api_key)
    llm_router.invalidate()
    changes = {f: {"old": old[f], "new": getattr(body, f)} for f in PROV_FIELDS
               if f != "params" and old[f] != getattr(body, f)}
    await _a(ctx, "LLM_CONFIG_CHANGE", {"op": "update", "provider": pid, "changes": changes,
                                        "api_key": "cleared" if body.api_key == "" else "set" if body.api_key else "unchanged"})
    return {"ok": True}


@router.delete("/llm-providers/{pid}")
async def delete_provider(pid: str, ctx: RequestContext = Depends(admin_ctx)):
    await store().execute("DELETE FROM llm_providers WHERE id = ?", [pid])
    llm_router.invalidate()
    await _a(ctx, "LLM_CONFIG_CHANGE", {"op": "delete", "provider": pid})
    return {"ok": True}


@router.post("/llm-providers/{pid}/test")
async def test_provider(pid: str, ctx: RequestContext = Depends(admin_ctx)):
    s = store()
    p = llm_router.provider_by_id(s, pid)
    try:
        res = await p.test()
    finally:
        await p.aclose()
    if res.get("chat") and not res["chat"]["ok"] and "json_schema" == p.params.get("structured_output_mode", "json_schema"):
        res["hint"] = "If the gateway rejects response_format, set params.structured_output_mode to json_object or prompt_only."
    await s.execute("UPDATE llm_providers SET last_test = ?, embed_dim = coalesce(?, embed_dim) WHERE id = ?",
                    [json.dumps(res), res.get("embed_dim"), pid])
    return res


@router.post("/llm-providers/{pid}/set-primary")
async def set_primary(pid: str, for_: str = Query("chat", alias="for"), ctx: RequestContext = Depends(admin_ctx)):
    col = {"chat": "is_primary_chat", "embed": "is_primary_embed"}.get(for_)
    if not col:
        raise ApiError("E-VAL-001", detail="for must be chat or embed")
    s = store()
    old = await s.fetchone(f"SELECT id, embed_model FROM llm_providers WHERE {col}")

    def unit(c):
        c.execute(f"UPDATE llm_providers SET {col} = false")
        c.execute(f"UPDATE llm_providers SET {col} = true WHERE id = ?", [pid])
    await s.write(unit)
    llm_router.invalidate()
    job = None
    if for_ == "embed" and (not old or str(old["id"]) != pid):
        job = await runner().enqueue("reindex", {}, ctx.user_ref, idempotency_key="reindex")  # vectors never compared across models
    await _a(ctx, "LLM_CONFIG_CHANGE", {"op": "set_primary", "for": for_, "provider": pid, "reindex_job": job})
    return {"ok": True, "reindex_job": job}


# ============================================================================= settings
@router.get("/settings")
async def get_settings(ctx: RequestContext = Depends(admin_ctx)):
    vals = {k: v for k, v in settings.all().items() if k not in AUTH_KEYS and not k.startswith("system.")}
    return {"values": vals, "defaults": {k: v for k, v in RUNTIME_DEFAULTS.items() if k not in AUTH_KEYS}}


@router.put("/settings/{key}")
async def put_setting(key: str, body: dict, ctx: RequestContext = Depends(admin_ctx)):
    if key not in RUNTIME_DEFAULTS and key not in ("schema.smell_patterns", "schema.sensitivity_patterns"):
        raise ApiError("E-VAL-001", detail=f"Unknown runtime setting {key}")
    if key in AUTH_KEYS:
        raise ApiError("E-VAL-001", detail="Use /admin/auth/settings")
    old = settings.get(key)
    value = body.get("value")
    if key == "rag.max_file_mb":
        value = min(int(value), 50)
    await settings.set(store(), key, value, ctx.user_ref)
    if key == "logging.level":
        set_level(str(value))
    await _a(ctx, "SETTINGS_CHANGE", {"key": key, "old": old, "new": value})
    return {"ok": True}


# ============================================================================= system
@router.get("/system")
async def system(ctx: RequestContext = Depends(admin_ctx)):
    from ...main import STARTED_AT
    cfg = get_config()
    s = store()
    backups = sorted((cfg.data_dir / "backups").glob("sqlassist-*.duckdb"))
    du = shutil.disk_usage(cfg.data_dir)
    jobs = await s.fetchall("SELECT status, count(*) AS n FROM jobs GROUP BY status")
    alert = settings.get("system.audit_alert")
    return {
        "paths": {"data_dir": str(cfg.data_dir), "log_dir": str(cfg.log_dir), "config_file": str(cfg.config_file or ""),
                  "db_file": str(cfg.db_path)},
        "uptime_s": int((datetime.now(timezone.utc) - STARTED_AT).total_seconds()),
        "db_size_bytes": s.file_size(), "disk_free_bytes": du.free, "disk_total_bytes": du.total,
        "extensions": s.extensions, "jobs": {r["status"]: r["n"] for r in jobs}, "queue_depth": runner().queue_depth(),
        "runner_alive": runner().alive,
        "last_backup": {"file": backups[-1].name, "size": backups[-1].stat().st_size,
                        "at": datetime.fromtimestamp(backups[-1].stat().st_mtime, timezone.utc).isoformat()} if backups else None,
        "log_level": settings.get("logging.level"), "quality": generation_success_rate(s),
        "audit_alert": alert, "compaction_pending": purge.compact_pending_marker().exists(),
        "provider_errors_1h": _provider_errors(cfg)}


def _provider_errors(cfg) -> dict:
    out: dict[str, int] = {}
    p = cfg.log_dir / "llm.log"
    if not p.exists():
        return out
    cutoff = datetime.now(timezone.utc).timestamp() - 3600
    try:
        lines = p.read_text(encoding="utf-8").splitlines()[-5000:]
    except OSError:
        return out
    for ln in lines:
        try:
            r = json.loads(ln)
            if datetime.fromisoformat(r["ts"]).timestamp() < cutoff:
                continue
            ex = r.get("extra") or {}
            if ex.get("status") != "OK":
                out[ex.get("provider", "?")] = out.get(ex.get("provider", "?"), 0) + 1
        except (ValueError, KeyError):
            continue
    return out


@router.post("/system/backup")
async def backup_now(ctx: RequestContext = Depends(admin_ctx)):
    return {"job_id": await runner().enqueue("backup", {"user_ref": ctx.user_ref, "user_id": ctx.user_id}, ctx.user_ref,
                                             idempotency_key="backup")}


@router.post("/system/compact")
async def compact(ctx: RequestContext = Depends(admin_ctx)):
    purge.request_compaction()
    await _a(ctx, "SETTINGS_CHANGE", {"compaction": "requested for next restart"})
    return {"ok": True, "note": "Compaction runs during the next restart."}


@router.post("/system/verify-audit")
async def verify(ctx: RequestContext = Depends(admin_ctx)):
    return {"job_id": await runner().enqueue("verify_audit", {}, ctx.user_ref, idempotency_key="verify_audit")}


@router.get("/jobs")
async def list_jobs(ctx: RequestContext = Depends(admin_ctx)):
    return {"items": await store().fetchall("SELECT id, type, status, progress, attempts, error, created_at, started_at, "
                                            "finished_at FROM jobs ORDER BY created_at DESC LIMIT 100")}


# ============================================================================= audit / chat metadata / purge
@router.get("/audit")
async def audit_list(action: str | None = None, status: str | None = None, user_id: str | None = None,
                     date_from: str | None = None, date_to: str | None = None, before_id: int | None = None,
                     limit: int = 200, ctx: RequestContext = Depends(admin_ctx)):
    items = audit.query(store(), action=action, status=status, user_id=user_id, date_from=date_from, date_to=date_to,
                        limit=min(limit, 1000), before_id=before_id)
    return {"items": items, "actions": sorted(audit.ACTIONS)}


@router.get("/audit/export.csv")
async def audit_csv(action: str | None = None, status: str | None = None, user_id: str | None = None,
                    date_from: str | None = None, date_to: str | None = None, ctx: RequestContext = Depends(admin_ctx)):
    items = audit.query(store(), action=action, status=status, user_id=user_id, date_from=date_from, date_to=date_to,
                        limit=100_000)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "ts", "user_id", "action", "status", "context", "row_hash"])
    for r in items:
        w.writerow([r["id"], r["ts"], r["user_id"], r["action"], r["status"], json.dumps(r["context"]), r["row_hash"]])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=audit.csv"})


@router.get("/chat-metadata")
async def chat_meta(ctx: RequestContext = Depends(admin_ctx)):
    return {"items": chat_metadata(store())}


class PurgeIn(BaseModel):
    scope: str = Field(pattern="^(AUDIT|CHAT|BOTH)$")
    date_from: str | None = None
    date_to: str
    confirm: str | None = None


@router.post("/purge/preview")
async def purge_preview(body: PurgeIn, ctx: RequestContext = Depends(admin_ctx)):
    return purge.preview(store(), body.scope, body.date_from, body.date_to)


@router.post("/purge/execute")
async def purge_execute(body: PurgeIn, ctx: RequestContext = Depends(admin_ctx)):
    if body.confirm != "PURGE":
        raise ApiError("E-VAL-001", detail="Type PURGE to confirm")
    if await store().fetchone("SELECT 1 AS x FROM jobs WHERE type = 'purge' AND status IN ('QUEUED','RUNNING')"):
        raise ApiError("E-PRG-001")
    jid = await runner().enqueue("purge", {**body.model_dump(exclude={"confirm"}), "user_ref": ctx.user_ref,
                                           "user_id": ctx.user_id}, ctx.user_ref, max_attempts=1)
    return {"job_id": jid}
