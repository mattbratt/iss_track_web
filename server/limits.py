"""A small per-visitor request limit, for the endpoints that cost CPU."""
from __future__ import annotations

import time


class RequestLimiter:
    """A token bucket per key: `rate` requests per minute, with a burst of
    `burst`. Idle keys are forgotten."""

    def __init__(self, rate: float = 60.0, burst: int = 20) -> None:
        self._rate = rate / 60.0
        self._burst = float(burst)
        self._buckets: dict[str, tuple[float, float]] = {}
        self._last_prune = 0.0

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        tokens, seen = self._buckets.get(key, (self._burst, now))
        tokens = min(self._burst, tokens + (now - seen) * self._rate)
        allowed = tokens >= 1.0
        self._buckets[key] = (tokens - 1.0 if allowed else tokens, now)
        if now - self._last_prune > 300:
            self._prune(now)
        return allowed

    def _prune(self, now: float) -> None:
        self._last_prune = now
        full_after = self._burst / self._rate
        for key, (_tokens, seen) in list(self._buckets.items()):
            if now - seen > full_after:
                del self._buckets[key]
