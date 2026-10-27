"""File logging with configurable location (TS §23.1).

app.log, access.log, llm.log — JSON lines, rotated daily. A redaction filter strips
passwords, API keys, Authorization headers, cookies and session tokens.
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from .config import Config

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
user_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("user_id", default=None)

_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?[^\s,;\"']+"),
    re.compile(r"(?i)(x-goog-api-key\s*[:=]\s*)[^\s,;\"']+"),
    re.compile(r"(?i)((?:api[_-]?key|password|passwd|secret|token|cookie|sqlassist_session)\"?\s*[:=]\s*\"?)[^\s,;\"'}]+"),
    re.compile(r"(?i)([?&]key=)[^&\s]+"),
]


def redact(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        text = pat.sub(lambda m: m.group(1) + "***", text)
    return text


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        record.msg = redact(msg)
        record.args = ()
        extra = getattr(record, "extra", None)
        if isinstance(extra, dict):
            record.extra = json.loads(redact(json.dumps(extra, default=str)))
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
            "user_id": user_id_var.get(),
        }
        extra = getattr(record, "extra", None)
        if extra:
            payload["extra"] = extra
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


def _handler(path: Path, keep: int, cfg: Config) -> logging.Handler:
    max_mb = int(cfg.get("logging.max_file_mb") or 0)
    if sys.platform == "win32":
        try:  # rename-safe on Windows (TS §23.1)
            from concurrent_log_handler import ConcurrentRotatingFileHandler

            return ConcurrentRotatingFileHandler(
                str(path), maxBytes=(max_mb or 50) * 1024 * 1024, backupCount=keep, encoding="utf-8"
            )
        except ImportError:
            pass
    if max_mb:
        from logging.handlers import RotatingFileHandler

        return RotatingFileHandler(path, maxBytes=max_mb * 1024 * 1024, backupCount=keep, encoding="utf-8")
    return TimedRotatingFileHandler(path, when="midnight", backupCount=keep, encoding="utf-8", utc=True)


def configure_logging(cfg: Config) -> None:
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    keep = int(cfg.get("logging.retention_days") or 30)
    fmt: logging.Formatter = (
        JsonFormatter() if cfg.get("logging.format", "json") == "json"
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    redactor = RedactionFilter()

    def setup(name: str, filename: str, keep_n: int, propagate: bool = False) -> logging.Logger:
        lg = logging.getLogger(name)
        for h in list(lg.handlers):
            lg.removeHandler(h)
        h = _handler(cfg.log_dir / filename, keep_n, cfg)
        h.setFormatter(fmt)
        h.addFilter(redactor)
        lg.addHandler(h)
        lg.propagate = propagate
        return lg

    root = setup("", "app.log", keep)
    root.setLevel(cfg.get("logging.level", "INFO"))
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.WARNING)
    console.setFormatter(fmt)
    console.addFilter(redactor)
    root.addHandler(console)
    setup("sqlassist.access", "access.log", keep).setLevel(logging.INFO)
    setup("sqlassist.llm", "llm.log", 14).setLevel(logging.INFO)
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").propagate = False


def set_level(level: str) -> None:
    logging.getLogger().setLevel(level.upper())


def log(logger: logging.Logger, level: int, msg: str, **extra) -> None:
    logger.log(level, msg, extra={"extra": extra} if extra else None)
