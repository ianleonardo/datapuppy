"""In-memory token buckets (TS §20.7, DD-12). Counters reset on restart — acceptable for an internal tool."""
from __future__ import annotations

import math
import threading
import time

from ..core.errors import ApiError


class TokenBucket:
    def __init__(self) -> None:
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def take(self, key: str, capacity: int, per_seconds: float) -> None:
        if capacity <= 0:
            return
        rate = capacity / per_seconds
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (float(capacity), now))
            tokens = min(capacity, tokens + (now - last) * rate)
            if tokens < 1:
                wait = math.ceil((1 - tokens) / rate)
                self._buckets[key] = (tokens, now)
                raise ApiError("E-RATE-001", n=wait)
            self._buckets[key] = (tokens - 1, now)
            if len(self._buckets) > 50_000:  # crude GC
                self._buckets = {k: v for k, v in self._buckets.items() if now - v[1] < 3600}


limiter = TokenBucket()


def limit(kind: str, who: str, per_min: int | None = None, per_hour: int | None = None) -> None:
    if per_min is not None:
        limiter.take(f"{kind}:{who}", int(per_min), 60)
    if per_hour is not None:
        limiter.take(f"{kind}:{who}", int(per_hour), 3600)
