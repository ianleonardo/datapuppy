"""Runtime settings registry (TS Appendix B, type R). Stored in app_settings; cached in memory (DD-12)."""
from __future__ import annotations

import json
from typing import Any

from ..store.duck import AppStore

DEFAULT_PERSONA = (
    "You are {mascot}, a friendly SQL buddy — a loyal, playful guardian puppy who is great at SQL. "
    "Be casual, warm and concise, like a helpful friend. Use contractions. Celebrate wins, and when "
    "something breaks, offer gentle, constructive help. Never sound like a terminal."
)

RUNTIME_DEFAULTS: dict[str, Any] = {
    "logging.level": "INFO",
    "duckdb.backup_cron": "0 2 * * *",
    "duckdb.backup_keep": 7,
    "auth.local_enabled": True,
    "auth.ldap_enabled": False,
    "auth.lockout_threshold": 5,
    "auth.lockout_minutes": 15,
    "auth.password_min_length": 12,
    "auth.password_history": 5,
    "auth.password_expiry_days": 0,
    "auth.session_idle_min": 30,
    "auth.session_absolute_h": 12,
    "ldap": {
        "enabled": False,
        "server_urls": ["ldaps://<ldap-host>:636"],
        "use_starttls": False,
        "verify_ssl": True,
        "ca_cert_path": "",
        "bind_mode": "search_then_bind",
        "bind_dn": "",
        "user_dn_template": "{user_id}@<domain>",
        "base_dn": "DC=<domain>,DC=<tld>",
        "user_filter": "(&(objectClass=user)(sAMAccountName={user_id}))",
        "attr_display_name": "displayName",
        "attr_email": "mail",
        "group_attr": "memberOf",
        "admin_groups": [],
        "analyst_groups": [],
        "country_group_map": {},
        "auto_provision": True,
        "connect_timeout_s": 5,
    },
    "ai.failover_enabled": True,
    "ai.prompt_budget_cap": 64000,
    "ai.max_repair_rounds": 2,
    "ai.history_turns": 6,
    "ai.interpret_max_rows": 1000,
    "ai.interpret_sample_rows": 20,
    "ai.persona": DEFAULT_PERSONA,
    "ai.debug_prompt_logging": False,
    "safeguard.max_rows": 1000,
    "safeguard.max_cols": 50,
    "safeguard.count_timeout_s": 5,
    "safeguard.statement_timeout_s": 120,
    "grid.page_size_default": 50,
    "grid.page_size_options": [25, 50, 100, 200],
    "results.handle_ttl_min": 60,
    "export.sync_row_limit": 10000,
    "export.file_ttl_h": 24,
    "rag.max_file_mb": 50,
    "rag.chunk_tokens": 800,
    "rag.chunk_overlap": 120,
    "rag.csv_rows_per_chunk": 50,
    "rag.knowledge_top_k": 6,
    "rag.schema_top_k": 12,
    "rag.schema_max_tables": 20,
    "rag.hnsw_threshold": 200000,
    "schema.max_tables": 5000,
    "theme.windows": {"morning": [5, 12], "afternoon": [12, 18], "night": [18, 5]},
    "ui.mascot_enabled": True,
    "ratelimit.chat_per_min": 30,
    "ratelimit.execute_per_min": 60,
    "ratelimit.preflight_per_min": 30,
    "ratelimit.uploads_per_hour": 20,
    "ratelimit.login_per_min_ip": 20,
    "retention.audit_days": 0,
    "retention.chat_days": 0,
    "audit.purge_mirror_files": "delete",
    "metrics.enabled": False,
}

SECRET_KEYS = {"ldap.bind_password"}


class Settings:
    def __init__(self) -> None:
        self._cache: dict[str, Any] = dict(RUNTIME_DEFAULTS)

    def load(self, store: AppStore) -> None:
        for row in store.fetchall_sync("SELECT key, value FROM app_settings"):
            if row["key"] in SECRET_KEYS:
                continue
            self._cache[row["key"]] = json.loads(row["value"]) if isinstance(row["value"], str) else row["value"]

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._cache:
            return self._cache[key]
        if "." in key:  # nested lookup e.g. ldap.base_dn
            head, tail = key.split(".", 1)
            node = self._cache.get(head)
            if isinstance(node, dict):
                return node.get(tail, default)
        return default

    def all(self) -> dict[str, Any]:
        return dict(self._cache)

    async def set(self, store: AppStore, key: str, value: Any, user_ref: str | None) -> None:
        payload = json.dumps(value)
        await store.write(lambda c: c.execute(
            "INSERT INTO app_settings(key, value, updated_by, updated_at) VALUES (?, ?, ?, now()) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_by = excluded.updated_by, "
            "updated_at = now()", [key, payload, user_ref]))
        if key not in SECRET_KEYS:
            self._cache[key] = value


settings = Settings()
