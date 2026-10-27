"""Directory tree under data_dir / log_dir (TS §24.1)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .config import Config

DATA_SUBDIRS = ("db", "uploads", "exports", "wallets", "backups", "extensions", "tmp", "run")


def ensure_tree(cfg: Config) -> None:
    for sub in DATA_SUBDIRS:
        (cfg.data_dir / sub).mkdir(parents=True, exist_ok=True)
    (cfg.log_dir / "audit" / "purged").mkdir(parents=True, exist_ok=True)
    for d in (cfg.data_dir, cfg.data_dir / "run", cfg.data_dir / "wallets"):
        restrict(d)


def restrict(path: Path) -> None:
    """Owner-only permissions (chmod 700/600 on POSIX; best effort on Windows)."""
    if sys.platform == "win32":
        return  # NTFS ACLs are applied by the install runbook (icacls), see TS §21.2
    try:
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
    except OSError:
        pass


def write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)
    restrict(path)


def is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False
