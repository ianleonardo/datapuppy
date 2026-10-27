"""Auth & profile endpoints (TS §20.1)."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from ...auth import service as auth
from ...auth.deps import current_ctx
from ...core.config import get_config
from ...core.errors import ApiError
from ...permissions import has_connection_access
from ...services import audit
from ...services.settings import settings
from ...store.duck import store
from ...store.repos import RequestContext

router = APIRouter(tags=["auth"])


class LoginIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)
    tz: str | None = Field(default=None, max_length=64)


class ChangePwIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=1, max_length=512)


class PrefsIn(BaseModel):
    layout: Any | None = None
    pinned_theme: str | None = None
    default_connection: str | None = None
    mascot_muted: bool | None = None


def _set_cookies(resp: Response, request: Request, token: str, csrf: str) -> None:
    secure = request.url.scheme == "https" or get_config().scheme == "https"
    resp.set_cookie(auth.COOKIE, token, httponly=True, samesite="lax", secure=secure, path="/")
    resp.set_cookie(auth.CSRF_COOKIE, csrf, httponly=False, samesite="lax", secure=secure, path="/")


@router.get("/auth/config")
async def auth_config() -> dict:
    """Unauthenticated: what the login page needs (no secrets)."""
    return {"local_enabled": bool(settings.get("auth.local_enabled", True)),
            "ldap_enabled": bool(settings.get("auth.ldap_enabled")) or bool((settings.get("ldap") or {}).get("enabled")),
            "theme_windows": settings.get("theme.windows"),
            "insecure_http": get_config().scheme == "http" and not get_config().get("server.proxy_headers"),
            "mascot_enabled": bool(settings.get("ui.mascot_enabled", True))}


@router.post("/auth/login")
async def login(body: LoginIn, request: Request, response: Response) -> dict:
    ip = request.client.host if request.client else "unknown"
    res = await auth.login(store(), body.user_id, body.password, body.tz, ip, request.headers.get("user-agent"))
    _set_cookies(response, request, res["token"], res["csrf"])
    return {"user": res["user"], "role": res["user"]["role"], "theme": res["theme"], "must_change_pw": res["must_change_pw"],
            "csrf_token": res["csrf"]}


@router.post("/auth/logout")
async def logout(request: Request, response: Response, ctx: RequestContext = Depends(current_ctx)) -> dict:
    token = request.cookies.get(auth.COOKIE)
    if token:
        await auth.revoke(store(), auth.sha(token))
    await audit.record(store(), action="LOGOUT", user_ref=ctx.user_ref, user_id=ctx.user_id, context={})
    response.delete_cookie(auth.COOKIE, path="/")
    response.delete_cookie(auth.CSRF_COOKIE, path="/")
    return {"ok": True}


@router.post("/auth/change-password")
async def change_password(body: ChangePwIn, ctx: RequestContext = Depends(current_ctx)) -> dict:
    await auth.change_password(store(), ctx, body.current_password, body.new_password)
    if ctx.user_id == "admin":
        auth.remove_initial_password_file(get_config())
    return {"ok": True}


@router.get("/me")
async def me(ctx: RequestContext = Depends(current_ctx)) -> dict:
    s = store()
    user = await s.fetchone("SELECT * FROM users WHERE id = ?", [ctx.user_ref])
    conns = await s.fetchall("SELECT id, name, country_code, engine, is_authorized FROM connections ORDER BY country_code, name")
    visible = [c for c in conns if has_connection_access(ctx, c)]
    pub = auth.public_user(user)
    return {"user": pub, "role": ctx.role, "auth_source": ctx.auth_source, "theme": ctx.theme, "csrf_token": ctx.csrf_token,
            "grants": {"connections": sorted(ctx.grants_connections), "countries": sorted(ctx.grants_countries)},
            "connections": [{"id": str(c["id"]), "name": c["name"], "country_code": c["country_code"],
                             "engine": c["engine"]} for c in visible],
            "preferences": pub["preferences"], "must_change_pw": pub["must_change_pw"],
            "ui": {"mascot_enabled": bool(settings.get("ui.mascot_enabled", True)),
                   "page_size_default": settings.get("grid.page_size_default", 50),
                   "page_size_options": settings.get("grid.page_size_options"),
                   "max_rows": settings.get("safeguard.max_rows"), "max_cols": settings.get("safeguard.max_cols")}}


@router.put("/me/preferences")
async def put_prefs(body: PrefsIn, ctx: RequestContext = Depends(current_ctx)) -> dict:
    s = store()
    user = await s.fetchone("SELECT preferences FROM users WHERE id = ?", [ctx.user_ref])
    prefs = json.loads(user["preferences"]) if isinstance(user["preferences"], str) else (user["preferences"] or {})
    patch = body.model_dump(exclude_unset=True)
    if "pinned_theme" in patch and patch["pinned_theme"] not in (None, "", *auth.THEMES):
        raise ApiError("E-VAL-001", detail="pinned_theme must be morning, afternoon, night or empty")
    for k, v in patch.items():
        if v in (None, "") and k in prefs:
            prefs.pop(k)
        elif v not in (None, ""):
            prefs[k] = v
    await s.execute("UPDATE users SET preferences = ?, updated_at = now() WHERE id = ?", [json.dumps(prefs), ctx.user_ref])
    prefs.pop("pw_history", None)
    return {"preferences": prefs}
