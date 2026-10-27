"""LDAP bind authentication with optional group→role mapping (TS §18.3, DD-16).

User input is escaped with escape_filter_chars (filters) and DN escaping (templates) — TR-AUTH-04.
LDAP passwords are never stored or cached.
"""
from __future__ import annotations

import logging
import ssl
from dataclasses import dataclass, field
from typing import Any

from ..core.errors import ApiError

log = logging.getLogger("sqlassist.auth.ldap")


@dataclass
class LdapIdentity:
    dn: str
    display_name: str
    email: str | None
    groups: list[str] = field(default_factory=list)


def escape_dn_value(value: str) -> str:
    """RFC 4514 escaping for a value substituted into a DN template."""
    out = []
    for i, ch in enumerate(value):
        if ch in ',+"\\<>;=' or (ch == "#" and i == 0) or (ch == " " and i in (0, len(value) - 1)):
            out.append("\\" + ch)
        elif ch == "\x00":
            out.append("\\00")
        else:
            out.append(ch)
    return "".join(out)


def build_filter(template: str, user_id: str) -> str:
    from ldap3.utils.conv import escape_filter_chars

    return template.replace("{user_id}", escape_filter_chars(user_id))


def _server_pool(cfg: dict[str, Any]):
    from ldap3 import FIRST, Server, ServerPool, Tls

    tls = None
    validate = ssl.CERT_REQUIRED if cfg.get("verify_ssl", True) else ssl.CERT_NONE
    tls = Tls(validate=validate, ca_certs_file=cfg.get("ca_cert_path") or None)
    servers = [Server(url, use_ssl=url.lower().startswith("ldaps://"), tls=tls,
                      connect_timeout=int(cfg.get("connect_timeout_s", 5)), get_info="NONE")
               for url in cfg.get("server_urls", [])]
    return ServerPool(servers, FIRST, active=1, exhaust=True)


def _connection(cfg: dict[str, Any], user: str | None, password: str | None):
    from ldap3 import Connection

    conn = Connection(_server_pool(cfg), user=user, password=password, auto_bind=False, raise_exceptions=False,
                      receive_timeout=int(cfg.get("connect_timeout_s", 5)) * 2)
    if not conn.open():
        raise ApiError("E-AUTH-007")
    if cfg.get("use_starttls"):
        conn.start_tls()
    return conn


def _attrs(entry: dict, cfg: dict[str, Any]) -> tuple[str, str | None, list[str]]:
    a = entry.get("attributes", {})

    def first(v):
        return (v[0] if isinstance(v, list) and v else v) or None
    return (first(a.get(cfg.get("attr_display_name", "displayName"))) or "",
            first(a.get(cfg.get("attr_email", "mail"))),
            list(a.get(cfg.get("group_attr", "memberOf")) or []))


def authenticate(cfg: dict[str, Any], user_id: str, password: str, bind_password: str | None) -> LdapIdentity | None:
    """Return identity on success, None on bad credentials, raise E-AUTH-007 if the directory is unreachable."""
    if not password:
        return None  # prevent unauthenticated (anonymous) binds
    attrs = [cfg.get("attr_display_name", "displayName"), cfg.get("attr_email", "mail"), cfg.get("group_attr", "memberOf")]
    try:
        from ldap3 import SUBTREE

        if cfg.get("bind_mode", "search_then_bind") == "search_then_bind":
            svc = _connection(cfg, cfg.get("bind_dn") or None, bind_password)
            if not svc.bind():
                log.error("LDAP service bind failed")
                raise ApiError("E-AUTH-007")
            svc.search(cfg["base_dn"], build_filter(cfg["user_filter"], user_id), SUBTREE, attributes=attrs, size_limit=2)
            entries = [e for e in svc.response or [] if e.get("type") == "searchResEntry"]
            svc.unbind()
            if len(entries) != 1:
                return None
            dn = entries[0]["dn"]
            display, email, groups = _attrs(entries[0], cfg)
            user_conn = _connection(cfg, dn, password)
            ok = user_conn.bind()
            user_conn.unbind()
            return LdapIdentity(dn, display or user_id, email, groups) if ok else None
        # direct_bind
        dn = cfg["user_dn_template"].replace("{user_id}", escape_dn_value(user_id))
        conn = _connection(cfg, dn, password)
        if not conn.bind():
            return None
        display, email, groups = user_id, None, []
        conn.search(cfg["base_dn"], build_filter(cfg["user_filter"], user_id), SUBTREE, attributes=attrs, size_limit=1)
        entries = [e for e in conn.response or [] if e.get("type") == "searchResEntry"]
        if entries:
            dn = entries[0]["dn"]
            display, email, groups = _attrs(entries[0], cfg)
        conn.unbind()
        return LdapIdentity(dn, display or user_id, email, groups)
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.error("LDAP error: %s", type(exc).__name__)
        raise ApiError("E-AUTH-007") from exc


def map_role(cfg: dict[str, Any], groups: list[str]) -> tuple[str | None, bool, list[str]]:
    """Returns (role or None if not allowed, role_from_ldap, country grants)."""
    gl = {g.lower() for g in groups}
    admin = {g.lower() for g in cfg.get("admin_groups", [])}
    analyst = {g.lower() for g in cfg.get("analyst_groups", [])}
    countries = [c for g, c in (cfg.get("country_group_map") or {}).items() if g.lower() in gl]
    if admin & gl:
        return "SYSTEM_ADMIN", True, countries
    if analyst:
        return ("ANALYST", True, countries) if analyst & gl else (None, False, [])
    return "ANALYST", False, countries


def test_connection(cfg: dict[str, Any], bind_password: str | None, test_user: str | None) -> dict[str, Any]:
    from ldap3 import SUBTREE

    svc = _connection(cfg, cfg.get("bind_dn") or None, bind_password)
    if not svc.bind():
        return {"ok": False, "detail": "service bind failed"}
    found = None
    if test_user:
        svc.search(cfg["base_dn"], build_filter(cfg["user_filter"], test_user), SUBTREE, attributes=["cn"], size_limit=2)
        found = [e["dn"] for e in svc.response or [] if e.get("type") == "searchResEntry"]
    svc.unbind()
    return {"ok": True, "found": found}
