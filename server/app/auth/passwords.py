"""Argon2id hashing and password policy (TS §18.2)."""
from __future__ import annotations

import re
import secrets
import string

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from ..core.errors import ApiError

_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2)


def hash_password(pw: str) -> str:
    return _hasher.hash(pw)


def verify_password(stored: str | None, pw: str) -> bool:
    if not stored:
        _hasher.hash("timing-equaliser")
        return False
    try:
        return _hasher.verify(stored, pw)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored: str) -> bool:
    try:
        return _hasher.check_needs_rehash(stored)
    except InvalidHashError:
        return True


def check_policy(pw: str, user_id: str, min_len: int = 12, history: list[str] | None = None) -> None:
    if len(pw) < min_len:
        raise ApiError("E-AUTH-006", rule=f"at least {min_len} characters")
    classes = sum(bool(re.search(p, pw)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    if classes < 3:
        raise ApiError("E-AUTH-006", rule="use 3 of 4: lowercase, uppercase, digit, symbol")
    if user_id and user_id.lower() in pw.lower():
        raise ApiError("E-AUTH-006", rule="must not contain your user ID")
    for old in history or []:
        if verify_password(old, pw):
            raise ApiError("E-AUTH-006", rule="must not reuse a recent password")


def random_password(n: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits + "-_!@#%"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(n))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw)):
            return pw
