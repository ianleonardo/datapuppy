"""Login flow, sessions and bootstrap admin (TS §18.1–18.2, DD-10, DD-16)."""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ..core.config import Config
from ..core.crypto import keyring
from ..core.errors import ApiError
from ..core.paths import write_private
from ..services import audit
from ..services.settings import settings
from ..store.duck import AppStore
from ..store.repos import RequestContext
from . import ldap_auth
from .passwords import check_policy, hash_password, needs_rehash, random_password, verify_password
from .ratelimit import limit

log = logging.getLogger("sqlassist.auth")
COOKIE = "sqlassist_session"
CSRF_COOKIE = "sqlassist_csrf"
THEMES = ("morning", "afternoon", "night")


def sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def compute_theme(tz: str | None, now: datetime | None = None) -> str:
    """DD-10: theme from client timezone at login, fixed for the session."""
    try:
        zone = ZoneInfo(tz) if tz else timezone.utc
    except Exception:  # noqa: BLE001
        zone = timezone.utc
    hour = (now or datetime.now(timezone.utc)).astimezone(zone).hour
    windows = settings.get("theme.windows") or {"morning": [5, 12], "afternoon": [12, 18], "night": [18, 5]}
    for name in ("morning", "afternoon", "night"):
        start, end = windows.get(name, [0, 0])
        if (start <= hour < end) if start < end else (hour >= start or hour < end):
            return name
    return "night"


# ----------------------------------------------------------------------------- session cache (DD-12)
@dataclass
class CachedSession:
    token_hash: str
    user_ref: str
    csrf: str
    theme: str
    created_at: float
    last_seen: float
    flushed_seen: float


class SessionCache:
    def __init__(self) -> None:
        self._c: dict[str, CachedSession] = {}
        self._lock = threading.Lock()

    def put(self, s: CachedSession) -> None:
        with self._lock:
            self._c[s.token_hash] = s

    def get(self, h: str) -> CachedSession | None:
        return self._c.get(h)

    def drop(self, h: str) -> None:
        with self._lock:
            self._c.pop(h, None)

    def drop_user(self, user_ref: str) -> None:
        with self._lock:
            for k in [k for k, v in self._c.items() if v.user_ref == user_ref]:
                self._c.pop(k)

    def dirty(self) -> list[CachedSession]:
        return [s for s in list(self._c.values()) if s.last_seen - s.flushed_seen >= 60]


sessions = SessionCache()


async def create_session(store: AppStore, user: dict, tz: str | None, ip: str | None, ua: str | None) -> tuple[str, str, str]:
    token = secrets.token_urlsafe(32)  # 256-bit
    csrf = secrets.token_urlsafe(24)
    prefs = _prefs(user)
    theme = prefs.get("pinned_theme") if prefs.get("pinned_theme") in THEMES else compute_theme(tz)
    absolute_h = int(settings.get("auth.session_absolute_h", 12))
    h = sha(token)
    await store.execute(
        "INSERT INTO auth_sessions(token_hash, user_ref, csrf_token, theme, client_tz, ip, user_agent, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, now() + to_hours(?))",
        [h, str(user["id"]), csrf, theme, tz, ip, (ua or "")[:300], absolute_h])
    now = time.time()
    sessions.put(CachedSession(h, str(user["id"]), csrf, theme, now, now, now))
    return token, csrf, theme


async def resolve_session(store: AppStore, token: str | None) -> tuple[CachedSession, dict] | None:
    if not token:
        return None
    h = sha(token)
    now = time.time()
    idle = int(settings.get("auth.session_idle_min", 30)) * 60
    absolute = int(settings.get("auth.session_absolute_h", 12)) * 3600
    s = sessions.get(h)
    if s is None:
        row = await store.fetchone("SELECT token_hash, user_ref, csrf_token, theme, epoch(created_at) AS c, "
                                   "epoch(last_seen_at) AS l, epoch(expires_at) AS e FROM auth_sessions WHERE token_hash = ?", [h])
        if not row or row["e"] < now:
            return None
        s = CachedSession(h, str(row["user_ref"]), row["csrf_token"], row["theme"], row["c"], row["l"], row["l"])
        sessions.put(s)
    if now - s.last_seen > idle or now - s.created_at > absolute:
        await revoke(store, token_hash=h)
        return None
    user = await store.fetchone("SELECT * FROM users WHERE id = ? AND is_active", [s.user_ref])
    if not user:
        await revoke(store, token_hash=h)
        return None
    s.last_seen = now
    return s, user


async def flush_last_seen(store: AppStore) -> None:
    """Write-behind of last_seen_at every 60 s (TS §18.1)."""
    for s in sessions.dirty():
        ts = s.last_seen
        await store.execute("UPDATE auth_sessions SET last_seen_at = to_timestamp(?) WHERE token_hash = ?", [ts, s.token_hash])
        s.flushed_seen = ts
    await store.execute("DELETE FROM auth_sessions WHERE expires_at < now() OR last_seen_at < now() - to_minutes(?)",
                        [int(settings.get("auth.session_idle_min", 30)) + 5])


async def revoke(store: AppStore, token_hash: str) -> None:
    sessions.drop(token_hash)
    await store.execute("DELETE FROM auth_sessions WHERE token_hash = ?", [token_hash])


async def revoke_user(store: AppStore, user_ref: str) -> None:
    sessions.drop_user(user_ref)
    await store.execute("DELETE FROM auth_sessions WHERE user_ref = ?", [user_ref])


def _prefs(user: dict) -> dict:
    p = user.get("preferences") or {}
    return json.loads(p) if isinstance(p, str) else p


async def build_context(store: AppStore, user: dict, s: CachedSession | None = None) -> RequestContext:
    grants = await store.fetchall("SELECT connection_id, country_code FROM user_grants WHERE user_ref = ?", [str(user["id"])])
    return RequestContext(
        user_ref=str(user["id"]), user_id=user["user_id"], role=user["role"], display_name=user["display_name"],
        grants_connections={str(g["connection_id"]) for g in grants if g["connection_id"]},
        grants_countries={g["country_code"] for g in grants if g["country_code"]},
        theme=s.theme if s else "morning", csrf_token=s.csrf if s else "", auth_source=user["auth_source"])


# ----------------------------------------------------------------------------- login
async def login(store: AppStore, user_id: str, password: str, tz: str | None, ip: str, ua: str | None) -> dict[str, Any]:
    limit("login-ip", ip, per_min=settings.get("ratelimit.login_per_min_ip", 20))
    uid = (user_id or "").strip().casefold()
    user = await store.fetchone("SELECT * FROM users WHERE user_id = ?", [uid])

    async def fail(reason: str, user_ref: str | None = None) -> None:
        await audit.record(store, action="LOGIN", status="FAILED", user_ref=user_ref, user_id=uid,
                           context={"reason": reason, "ip": ip})
        raise ApiError("E-AUTH-004")

    if user and user["locked_until"] and user["locked_until"] > datetime.now(timezone.utc):
        mins = max(1, int((user["locked_until"] - datetime.now(timezone.utc)).total_seconds() // 60) + 1)
        await audit.record(store, action="LOGIN", status="FAILED", user_ref=str(user["id"]), user_id=uid,
                           context={"reason": "LOCKED", "ip": ip})
        raise ApiError("E-AUTH-005", n=mins)
    if user and not user["is_active"]:
        await fail("INACTIVE", str(user["id"]))

    ldap_cfg = settings.get("ldap") or {}
    ldap_enabled = bool(settings.get("auth.ldap_enabled")) or bool(ldap_cfg.get("enabled"))
    ok = False
    if user and user["auth_source"] == "LOCAL":
        if not settings.get("auth.local_enabled", True):
            await fail("LOCAL_DISABLED", str(user["id"]))
        ok = verify_password(user["password_hash"], password)
        if ok and needs_rehash(user["password_hash"]):
            await store.execute("UPDATE users SET password_hash = ? WHERE id = ?", [hash_password(password), str(user["id"])])
    elif ldap_enabled and (user is None or user["auth_source"] == "LDAP"):
        if user is None and not ldap_cfg.get("auto_provision", True):
            await fail("NO_SUCH_USER")
        ident = ldap_auth.authenticate(ldap_cfg, uid, password, _ldap_bind_password(store))
        if ident:
            role, from_ldap, countries = ldap_auth.map_role(ldap_cfg, ident.groups)
            if role is None:
                await fail("LDAP_NOT_IN_GROUP", str(user["id"]) if user else None)
            user = await _sync_ldap_user(store, uid, ident, role, from_ldap, countries, user)
            ok = True
    else:
        verify_password(None, password)  # constant-ish timing

    if not ok:
        if user:
            threshold = int(settings.get("auth.lockout_threshold", 5))
            minutes = int(settings.get("auth.lockout_minutes", 15))
            await store.execute(
                "UPDATE users SET failed_logins = failed_logins + 1, locked_until = CASE WHEN failed_logins + 1 >= ? "
                "THEN now() + to_minutes(?) ELSE locked_until END WHERE id = ?", [threshold, minutes, str(user["id"])])
        await fail("BAD_CREDENTIALS", str(user["id"]) if user else None)

    await store.execute("UPDATE users SET failed_logins = 0, locked_until = NULL, last_login_at = now() WHERE id = ?",
                        [str(user["id"])])
    token, csrf, theme = await create_session(store, user, tz, ip, ua)
    await audit.record(store, action="LOGIN", user_ref=str(user["id"]), user_id=uid,
                       context={"ip": ip, "auth_source": user["auth_source"], "theme": theme})
    must_change = bool(user["must_change_pw"])
    exp_days = int(settings.get("auth.password_expiry_days", 0))
    if exp_days and user["auth_source"] == "LOCAL" and user["pw_changed_at"]:
        if user["pw_changed_at"] < datetime.now(timezone.utc) - timedelta(days=exp_days):
            must_change = True
    return {"token": token, "csrf": csrf, "theme": theme, "user": public_user(user), "must_change_pw": must_change}


def _ldap_bind_password(store: AppStore) -> str | None:
    row = store.fetchall_sync("SELECT value FROM app_settings WHERE key = 'ldap.bind_password'")
    if not row:
        return None
    v = json.loads(row[0]["value"]) if isinstance(row[0]["value"], str) else row[0]["value"]
    try:
        import base64
        return keyring().open_str(base64.b64decode(v["ct"]), base64.b64decode(v["nonce"]), v["kv"], "app_settings:ldap:bind_password")
    except Exception:  # noqa: BLE001
        return None


async def _sync_ldap_user(store: AppStore, uid: str, ident, role: str, from_ldap: bool, countries: list[str],
                          user: dict | None) -> dict:
    if user is None:
        new_id = str(uuid.uuid4())
        await store.execute("INSERT INTO users(id, user_id, display_name, email, role, auth_source, ldap_dn, role_from_ldap) "
                            "VALUES (?, ?, ?, ?, ?, 'LDAP', ?, ?)", [new_id, uid, ident.display_name, ident.email, role,
                                                                     ident.dn, from_ldap])
        user_ref = new_id
    else:
        user_ref = str(user["id"])
        if from_ldap:
            await store.execute("UPDATE users SET display_name = ?, email = ?, ldap_dn = ?, role = ?, role_from_ldap = true, "
                                "updated_at = now() WHERE id = ?", [ident.display_name, ident.email, ident.dn, role, user_ref])
        else:
            await store.execute("UPDATE users SET display_name = ?, email = ?, ldap_dn = ?, updated_at = now() WHERE id = ?",
                                [ident.display_name, ident.email, ident.dn, user_ref])
    for cc in countries:
        exists = await store.fetchone("SELECT 1 AS x FROM user_grants WHERE user_ref = ? AND country_code = ?", [user_ref, cc])
        if not exists:
            await store.execute("INSERT INTO user_grants(user_ref, country_code) VALUES (?, ?)", [user_ref, cc])
    return await store.fetchone("SELECT * FROM users WHERE id = ?", [user_ref])


async def change_password(store: AppStore, ctx: RequestContext, current: str, new: str) -> None:
    user = await store.fetchone("SELECT * FROM users WHERE id = ?", [ctx.user_ref])
    if user["auth_source"] != "LOCAL":
        raise ApiError("E-VAL-001", detail="Password is managed by the directory (LDAP).")
    if not verify_password(user["password_hash"], current):
        await audit.record(store, action="PASSWORD_CHANGE", status="FAILED", user_ref=ctx.user_ref, user_id=ctx.user_id,
                           context={"reason": "BAD_CURRENT"})
        raise ApiError("E-AUTH-004")
    prefs = _prefs(user)
    history = prefs.get("pw_history", [])
    check_policy(new, user["user_id"], int(settings.get("auth.password_min_length", 12)),
                 [user["password_hash"]] + history)
    keep = int(settings.get("auth.password_history", 5))
    prefs["pw_history"] = ([user["password_hash"]] + history)[: max(0, keep - 1)]
    await store.execute("UPDATE users SET password_hash = ?, must_change_pw = false, pw_changed_at = now(), "
                        "preferences = ?, updated_at = now() WHERE id = ?",
                        [hash_password(new), json.dumps(prefs), ctx.user_ref])
    await audit.record(store, action="PASSWORD_CHANGE", user_ref=ctx.user_ref, user_id=ctx.user_id, context={})


def public_user(u: dict) -> dict[str, Any]:
    prefs = _prefs(u)
    prefs.pop("pw_history", None)
    return {"id": str(u["id"]), "user_id": u["user_id"], "display_name": u["display_name"], "email": u["email"],
            "role": u["role"], "auth_source": u["auth_source"], "is_active": u["is_active"],
            "role_from_ldap": u["role_from_ldap"], "must_change_pw": u["must_change_pw"],
            "locked": bool(u["locked_until"] and u["locked_until"] > datetime.now(timezone.utc)),
            "last_login_at": u["last_login_at"], "preferences": prefs}


# ----------------------------------------------------------------------------- bootstrap
def ensure_bootstrap_admin(store: AppStore, cfg: Config) -> str | None:
    n = store.fetchall_sync("SELECT count(*) AS n FROM users")[0]["n"]
    if n:
        return None
    pw = random_password(20)
    store.write_sync(lambda c: c.execute(
        "INSERT INTO users(user_id, display_name, role, auth_source, password_hash, must_change_pw) "
        "VALUES ('admin', 'Administrator', 'SYSTEM_ADMIN', 'LOCAL', ?, true)", [hash_password(pw)]))
    write_private(cfg.data_dir / "run" / "initial-admin-password.txt", pw + "\n")
    log.warning("Bootstrap admin created; password written to data_dir/run/initial-admin-password.txt")
    return pw


def remove_initial_password_file(cfg: Config) -> None:
    p = cfg.data_dir / "run" / "initial-admin-password.txt"
    if p.exists():
        try:
            p.unlink()
        except OSError:
            pass
