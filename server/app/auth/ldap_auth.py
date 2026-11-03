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
    """Substitute the escaped login name for {username} (also accepted: {user_id}, {login_id})."""
    from ldap3.utils.conv import escape_filter_chars

    esc = escape_filter_chars(user_id)
    return template.replace("{username}", esc).replace("{user_id}", esc).replace("{login_id}", esc)


DEFAULT_DOMAIN = "NTSGPDOM"


def is_configured(cfg: dict[str, Any]) -> bool:
    """At least one real LDAP server URL (not the placeholder)."""
    urls = cfg.get("server_urls") or []
    urls = [urls] if isinstance(urls, str) else urls
    return any(str(u).strip() and "<" not in str(u) for u in urls)


def login_name(user_id: str) -> str:
    """The plain login ID: 'NTSGPDOM\\ian' and 'ian@corp.com' both become 'ian'."""
    return user_id.strip().split("\\")[-1].split("@")[0]


def effective_mode(cfg: dict[str, Any]) -> str:
    """'domain' = bind as DOMAIN\\login_id (Active Directory, no service account needed); 'search' = look the user up
    with a service account first. Older settings ('direct_bind', or 'search_then_bind' without a service account)
    map to 'domain'."""
    mode = cfg.get("bind_mode", "domain")
    if mode == "search_then_bind" and cfg.get("bind_dn"):
        return "search"
    return "domain"


def bind_name(cfg: dict[str, Any], user_id: str) -> str:
    """The name sent in the bind request for domain mode."""
    name = login_name(user_id)
    tmpl = (cfg.get("user_dn_template") or "").strip()
    if effective_mode(cfg) == "domain" and cfg.get("bind_mode") == "direct_bind" and tmpl and "<" not in tmpl:
        return tmpl.replace("{user_id}", escape_dn_value(name)).replace("{username}", escape_dn_value(name))
    typed = user_id.strip().split("\\")
    domain = (typed[0] if len(typed) > 1 else "") or (cfg.get("domain") or "").strip() or DEFAULT_DOMAIN
    return f"{domain}\\{name}"


def _server_pool(cfg: dict[str, Any]):
    from ldap3 import FIRST, Server, ServerPool, Tls

    validate = ssl.CERT_REQUIRED if cfg.get("verify_ssl", True) else ssl.CERT_NONE
    tls = Tls(validate=validate, ca_certs_file=cfg.get("ca_cert_path") or None)
    urls = cfg.get("server_urls") or []
    if isinstance(urls, str):
        urls = [u.strip() for u in urls.replace("\n", ",").split(",") if u.strip()]
    servers = [Server(url, use_ssl=url.lower().startswith("ldaps://"), tls=tls,
                      connect_timeout=int(cfg.get("connect_timeout_s", 10)), get_info="NONE") for url in urls]
    return ServerPool(servers, FIRST, active=1, exhaust=True)


def _connection(cfg: dict[str, Any], user: str | None, password: str | None):
    from ldap3 import Connection

    conn = Connection(_server_pool(cfg), user=user, password=password, auto_bind=False, raise_exceptions=False,
                      receive_timeout=int(cfg.get("connect_timeout_s", 10)) * 2)
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


def _find_entry(conn, cfg: dict[str, Any], name: str):
    from ldap3 import SUBTREE

    attrs = [cfg.get("attr_display_name", "displayName"), cfg.get("attr_email", "mail"), cfg.get("group_attr", "memberOf")]
    conn.search(cfg["base_dn"], build_filter(cfg.get("user_filter") or "(sAMAccountName={username})", name), SUBTREE,
                attributes=attrs, size_limit=2)
    return [e for e in conn.response or [] if e.get("type") == "searchResEntry"]


def authenticate(cfg: dict[str, Any], user_id: str, password: str, bind_password: str | None) -> LdapIdentity | None:
    """Return identity on success, None on bad credentials, raise E-AUTH-007 if the directory is unreachable."""
    if not password:
        return None  # prevent unauthenticated (anonymous) binds
    name = login_name(user_id)
    if not name:
        return None
    try:
        if effective_mode(cfg) == "search":
            svc = _connection(cfg, cfg.get("bind_dn") or None, bind_password)
            if not svc.bind():
                log.error("LDAP service-account bind failed: %s", svc.result)
                raise ApiError("E-AUTH-007")
            entries = _find_entry(svc, cfg, name)
            svc.unbind()
            if len(entries) != 1:
                return None
            dn = entries[0]["dn"]
            display, email, groups = _attrs(entries[0], cfg)
            user_conn = _connection(cfg, dn, password)
            ok = user_conn.bind()
            user_conn.unbind()
            return LdapIdentity(dn, display or name, email, groups) if ok else None
        # domain mode: bind as DOMAIN\login_id, then read the user's own entry with the same connection
        conn = _connection(cfg, bind_name(cfg, user_id), password)
        if not conn.bind():
            log.info("LDAP bind refused for %s: %s", bind_name(cfg, user_id), (conn.result or {}).get("description"))
            return None
        dn, display, email, groups = bind_name(cfg, user_id), name, None, []
        try:
            entries = _find_entry(conn, cfg, name)
        except Exception as exc:  # noqa: BLE001 — signing in works even when the lookup does not
            log.warning("LDAP user lookup failed after a successful bind: %s: %s", type(exc).__name__, exc)
            entries = []
        if not entries:
            log.warning("LDAP bind worked but no entry matched %s under %s — check Base DN and User filter",
                        build_filter(cfg.get("user_filter") or "", name), cfg.get("base_dn"))
        else:
            dn = entries[0]["dn"]
            display, email, groups = _attrs(entries[0], cfg)
        conn.unbind()
        return LdapIdentity(dn, display or name, email, groups)
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.error("LDAP error: %s: %s", type(exc).__name__, exc)
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


def diagnose(cfg: dict[str, Any], bind_password: str | None, user_id: str | None, password: str | None) -> dict[str, Any]:
    """Step-by-step check for the admin 'Test' button: connect → bind → find the user → groups."""
    steps: list[dict[str, Any]] = []

    def step(name: str, ok: bool, detail: str = "") -> bool:
        steps.append({"step": name, "ok": ok, "detail": detail})
        return ok

    urls = cfg.get("server_urls") or []
    if not urls or any("<" in str(u) for u in urls):
        return {"ok": False, "steps": steps, "detail": "Server URLs are empty or still contain a placeholder."}
    mode = effective_mode(cfg)
    try:
        from ldap3 import Connection

        conn = Connection(_server_pool(cfg), auto_bind=False, raise_exceptions=True)
        conn.open()
        if cfg.get("use_starttls"):
            conn.start_tls()
        step("Connect", True, f"{', '.join(urls)} ({'StartTLS' if cfg.get('use_starttls') else 'TLS' if str(urls[0]).lower().startswith('ldaps') else 'plain'})")
    except Exception as exc:  # noqa: BLE001
        reason = f"{type(exc).__name__}: {exc}"
        if any(w in reason.lower() for w in ("certificate", "hostname", "ssl")):
            reason += " — turn off 'Verify TLS certificate' or set a CA bundle path."
        step("Connect", False, reason)
        return {"ok": False, "steps": steps, "detail": "Cannot connect: " + reason}
    conn.unbind()
    if not user_id or not password:
        who = "a service account" if mode == "search" else f"{bind_name(cfg, 'USER')}"
        return {"ok": True, "steps": steps, "detail": f"Directory reachable. Enter a user ID and password to test sign-in (binds as {who})."}
    name = login_name(user_id)
    try:
        if mode == "search":
            svc = _connection(cfg, cfg.get("bind_dn") or None, bind_password)
            if not step("Service account bind", bool(svc.bind()), str((svc.result or {}).get("description", ""))):
                return {"ok": False, "steps": steps, "detail": "Service account bind failed"}
            entries = _find_entry(svc, cfg, name)
            svc.unbind()
            if not step("Find user", len(entries) == 1, f"{len(entries)} match(es) under {cfg.get('base_dn')}"):
                return {"ok": False, "steps": steps, "detail": "User not found (or ambiguous)"}
            entry = entries[0]
            uc = _connection(cfg, entry["dn"], password)
            ok = bool(uc.bind())
            step("User bind", ok, entry["dn"] if ok else str((uc.result or {}).get("description", "")))
            uc.unbind()
            if not ok:
                return {"ok": False, "steps": steps, "detail": "Wrong password"}
        else:
            bn = bind_name(cfg, user_id)
            uc = _connection(cfg, bn, password)
            ok = bool(uc.bind())
            res = uc.result or {}
            step(f"Bind as {bn}", ok, "" if ok else f"{res.get('description', '')} {str(res.get('message', ''))[:120]}".strip())
            if not ok:
                return {"ok": False, "steps": steps, "detail": "Bind refused: wrong password, or the domain name is wrong"}
            entries = _find_entry(uc, cfg, name)
            uc.unbind()
            if not step("Find user entry", len(entries) >= 1,
                        f"{len(entries)} match(es) under {cfg.get('base_dn')} for {build_filter(cfg.get('user_filter') or '', name)}"):
                return {"ok": False, "steps": steps, "detail": "Signed in, but the entry was not found: check Base DN and User filter"}
            entry = entries[0]
        display, email, groups = _attrs(entry, cfg)
        step("Attributes", True, f"name={display or '-'}, mail={email or '-'}, {len(groups)} group(s)")
        role, _, _ = map_role(cfg, groups)
        step("Role", role is not None, role or "not in an allowed group")
        return {"ok": role is not None, "steps": steps, "detail": f"Sign-in works for {display or name}" if role else "Not in an allowed group"}
    except Exception as exc:  # noqa: BLE001
        step("Error", False, f"{type(exc).__name__}: {exc}")
        return {"ok": False, "steps": steps, "detail": f"{type(exc).__name__}: {exc}"}
