"""Configuration loader (TS §24.2).

Precedence (lowest → highest):
  1. Built-in defaults (below)
  2. config/sqlassist.toml  (path from SQLASSIST_CONFIG)
  3. Environment variables SQLASSIST_<SECTION>__<KEY> plus SQLASSIST_DATA_DIR / SQLASSIST_LOG_DIR
  4. Runtime settings in app_settings (handled by services.settings, not here)

Paths, host, port and the key file are static: a change requires a restart.
"""
from __future__ import annotations

import copy
import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

INSTALL_DIR = Path(__file__).resolve().parents[3]

DEFAULTS: dict[str, Any] = {
    "server": {
        "host": "0.0.0.0",
        "port": 8080,
        "public_base_url": "",
        "ssl_certfile": "",
        "ssl_keyfile": "",
        "allow_insecure_http": False,
        "proxy_headers": False,
    },
    "paths": {"data_dir": "./data"},
    "logging": {"dir": "", "level": "INFO", "format": "json", "retention_days": 30, "max_file_mb": 0},
    "duckdb": {"memory_limit": "4GB", "threads": 8, "backup_cron": "0 2 * * *", "backup_keep": 7},
    "store": {"read_cursors": 8},
    "jobs": {"max_workers": 4, "poll_interval_s": 1.0},
    "connector": {"max_threads": 32},
    "security": {"master_key_file": "./config/master.key"},
    "rag": {"embed_dim": 1024},
    "llm": {
        "defaults": {"primary_chat": "SPIRARE", "primary_embed": "SPIRARE"},
        "gemini": {
            "chat_model": "gemini-3.6-flash",
            "embed_model": "gemini-embedding-2",
            "base_url": "https://generativelanguage.googleapis.com/v1beta",
            "verify_ssl": True,
            "use_proxy": True,
            "api_key": "",
            "max_model_len": 1_000_000,
            "max_output_tokens": 8192,
        },
        "spirare": {
            "chat_url": "https://s-egp.sg.uobnet.com/nlp/v1/chat/completions",
            "chat_model": "Qwen3.5-122B-A10B",
            "max_model_len": 32768,
            "max_output_tokens": 4096,
            "embed_url": "https://s-egp.sg.uobnet.com/embedding/v1/embeddings",
            "embed_model": "/data/genai/models/bge-m3",
            "verify_ssl": False,
            "use_proxy": False,
            "api_key": "",
        },
    },
    "auth": {
        "local_enabled": True,
        "ldap_enabled": False,
        "ldap": {
            "server_urls": ["ldaps://<ldap-host>:636"],
            "base_dn": "DC=<domain>,DC=<tld>",
            "user_filter": "(&(objectClass=user)(sAMAccountName={user_id}))",
            "bind_mode": "search_then_bind",
            "bind_dn": "",
            "bind_password": "",
        },
    },
}


def _deep_merge(base: dict, over: dict) -> dict:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _coerce(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        low = raw.lower()
        if low in ("true", "yes", "on"):
            return True
        if low in ("false", "no", "off"):
            return False
        return raw


def _resolve(p: str | Path, base: Path) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(str(p))))
    return (path if path.is_absolute() else (base / path)).resolve()


@dataclass
class Config:
    raw: dict[str, Any]
    config_file: Path | None
    install_dir: Path = INSTALL_DIR
    data_dir: Path = field(init=False)
    log_dir: Path = field(init=False)
    master_key_file: Path = field(init=False)

    def __post_init__(self) -> None:
        self.data_dir = _resolve(self.get("paths.data_dir"), self.install_dir)
        log_dir = self.get("logging.dir") or ""
        self.log_dir = _resolve(log_dir, self.install_dir) if log_dir else self.data_dir / "logs"
        self.master_key_file = _resolve(self.get("security.master_key_file"), self.install_dir)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    # -- convenience --------------------------------------------------------
    @property
    def scheme(self) -> str:
        return "https" if self.get("server.ssl_certfile") and self.get("server.ssl_keyfile") else "http"

    @property
    def host(self) -> str:
        return str(self.get("server.host"))

    @property
    def port(self) -> int:
        return int(self.get("server.port"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db" / "sqlassist.duckdb"

    def sub(self, name: str) -> Path:
        return self.data_dir / name


def load_config(path: str | os.PathLike | None = None, env: dict[str, str] | None = None) -> Config:
    env = dict(os.environ if env is None else env)
    raw = copy.deepcopy(DEFAULTS)
    cfg_path = Path(path or env.get("SQLASSIST_CONFIG") or INSTALL_DIR / "config" / "sqlassist.toml")
    used: Path | None = None
    if cfg_path.is_file():
        with cfg_path.open("rb") as fh:
            _deep_merge(raw, tomllib.load(fh))
        used = cfg_path.resolve()

    for key, value in env.items():
        if not key.startswith("SQLASSIST_") or "__" not in key:
            continue
        parts = [p.lower() for p in key[len("SQLASSIST_"):].split("__")]
        node = raw
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = _coerce(value)
    if env.get("SQLASSIST_DATA_DIR"):
        raw["paths"]["data_dir"] = env["SQLASSIST_DATA_DIR"]
    if env.get("SQLASSIST_LOG_DIR"):
        raw["logging"]["dir"] = env["SQLASSIST_LOG_DIR"]
    return Config(raw=raw, config_file=used)


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def set_config(cfg: Config) -> None:
    global _config
    _config = cfg
