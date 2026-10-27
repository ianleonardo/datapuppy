"""RBAC matrix (TS §18.5, FS 1). Parametrised tests are generated from this table (TR-RBAC-02)."""
from __future__ import annotations

MATRIX: dict[str, dict[str, str]] = {
    # capability:               {role: "yes" | "no" | "granted" | "own"}
    "connection.crud":          {"SYSTEM_ADMIN": "yes", "ANALYST": "no"},
    "schema.govern":            {"SYSTEM_ADMIN": "yes", "ANALYST": "no"},
    "schema.view":              {"SYSTEM_ADMIN": "yes", "ANALYST": "granted"},
    "documents.shared.write":   {"SYSTEM_ADMIN": "yes", "ANALYST": "no"},
    "documents.private":        {"SYSTEM_ADMIN": "own", "ANALYST": "own"},
    "chat.content":             {"SYSTEM_ADMIN": "own", "ANALYST": "own"},
    "chat.metadata.all":        {"SYSTEM_ADMIN": "yes", "ANALYST": "no"},
    "audit.view":               {"SYSTEM_ADMIN": "yes", "ANALYST": "no"},
    "purge.execute":            {"SYSTEM_ADMIN": "yes", "ANALYST": "no"},
    "system.manage":            {"SYSTEM_ADMIN": "yes", "ANALYST": "no"},
    "llm.configure":            {"SYSTEM_ADMIN": "yes", "ANALYST": "no"},
    "auth.configure":           {"SYSTEM_ADMIN": "yes", "ANALYST": "no"},
    "sql.execute":              {"SYSTEM_ADMIN": "yes", "ANALYST": "granted"},
}


def can(role: str, capability: str) -> bool:
    return MATRIX.get(capability, {}).get(role, "no") != "no"


def has_connection_access(ctx, connection: dict) -> bool:
    """Admins see all connections; analysts need a connection or country grant, and the connection must be authorized."""
    if ctx.is_admin:
        return True
    if not connection.get("is_authorized"):
        return False
    return str(connection["id"]) in ctx.grants_connections or connection["country_code"] in ctx.grants_countries
