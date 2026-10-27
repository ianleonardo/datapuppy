"""FastAPI dependencies: RequestContext from cookie, CSRF double-submit, RBAC (TS §6.2, §18.1, §18.5)."""
from __future__ import annotations

import secrets

from fastapi import Depends, Request

from ..core.errors import ApiError
from ..core.logging import user_id_var
from ..permissions import can
from ..store.duck import store
from ..store.repos import RequestContext
from .service import COOKIE, build_context, resolve_session

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


async def current_ctx(request: Request) -> RequestContext:
    token = request.cookies.get(COOKIE)
    resolved = await resolve_session(store(), token)
    if not resolved:
        raise ApiError("E-AUTH-001")
    sess, user = resolved
    if request.method not in SAFE_METHODS:
        sent = request.headers.get("X-CSRF-Token", "")
        if not sent or not secrets.compare_digest(sent, sess.csrf):
            raise ApiError("E-AUTH-008", status=403)
    ctx = await build_context(store(), user, sess)
    ctx.request_id = getattr(request.state, "request_id", "")
    user_id_var.set(ctx.user_id)
    request.state.ctx = ctx
    return ctx


async def admin_ctx(ctx: RequestContext = Depends(current_ctx)) -> RequestContext:
    if not ctx.is_admin:
        raise ApiError("E-AUTH-002")
    return ctx


def require(capability: str):
    async def dep(ctx: RequestContext = Depends(current_ctx)) -> RequestContext:
        if not can(ctx.role, capability):
            raise ApiError("E-AUTH-002")
        return ctx
    return dep
