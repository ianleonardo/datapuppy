"""AES-256-GCM secret encryption with a master key file outside data_dir (TS §21.2)."""
from __future__ import annotations

import base64
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .paths import write_private

KEY_ENV = "SQLASSIST_MASTER_KEY"


@dataclass
class Sealed:
    ciphertext: bytes
    nonce: bytes
    key_version: int


class KeyRing:
    """Holds one or more master keys. File format: one line per key, `v<N>:<base64>`; last line is current."""

    def __init__(self, keys: dict[int, bytes]):
        if not keys:
            raise ValueError("empty keyring")
        self.keys = keys
        self.current_version = max(keys)

    @classmethod
    def load_or_create(cls, path: Path) -> "KeyRing":
        env = os.environ.get(KEY_ENV)
        if env:
            return cls({1: base64.b64decode(env)})
        if not path.exists():
            write_private(path, f"v1:{base64.b64encode(secrets.token_bytes(32)).decode()}\n")
        return cls.parse(path.read_text(encoding="utf-8"))

    @classmethod
    def parse(cls, text: str) -> "KeyRing":
        keys: dict[int, bytes] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if ":" in line and line.startswith("v"):
                ver, b64 = line.split(":", 1)
                keys[int(ver[1:])] = base64.b64decode(b64)
            else:
                keys[1] = base64.b64decode(line)
        for k in keys.values():
            if len(k) != 32:
                raise ValueError("master key must be 256-bit")
        return cls(keys)

    def add_version(self, path: Path) -> int:
        new_v = self.current_version + 1
        self.keys[new_v] = secrets.token_bytes(32)
        self.current_version = new_v
        write_private(path, "".join(f"v{v}:{base64.b64encode(k).decode()}\n" for v, k in sorted(self.keys.items())))
        return new_v

    def seal(self, plaintext: str | bytes, aad: str) -> Sealed:
        data = plaintext.encode() if isinstance(plaintext, str) else plaintext
        nonce = secrets.token_bytes(12)
        ct = AESGCM(self.keys[self.current_version]).encrypt(nonce, data, aad.encode())
        return Sealed(ct, nonce, self.current_version)

    def open(self, ciphertext: bytes, nonce: bytes, key_version: int, aad: str) -> bytes:
        return AESGCM(self.keys[key_version]).decrypt(nonce, bytes(ciphertext), aad.encode())

    def open_str(self, ciphertext: bytes, nonce: bytes, key_version: int, aad: str) -> str:
        return self.open(ciphertext, nonce, key_version, aad).decode()


_ring: KeyRing | None = None


def init_keyring(path: Path) -> KeyRing:
    global _ring
    _ring = KeyRing.load_or_create(path)
    return _ring


def keyring() -> KeyRing:
    if _ring is None:
        raise RuntimeError("keyring not initialised")
    return _ring
