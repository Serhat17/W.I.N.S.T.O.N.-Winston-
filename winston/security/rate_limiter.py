"""
Fixed-window rate limiter per client key.
Inspired by OpenClaw's fixed-window-rate-limit approach.
"""

import time
from typing import NamedTuple


class RateLimitResult(NamedTuple):
    allowed: bool
    retry_after_ms: int
    remaining: int


class RateLimiter:
    """Fixed-window rate limiter per client key."""

    def __init__(self, max_requests: int = 30, window_ms: int = 60_000):
        self._max = max_requests
        self._window_ms = window_ms
        self._buckets: dict = {}  # key -> {count, start}

    def consume(self, key: str) -> RateLimitResult:
        """Try to consume a request for the given key.

        Returns a RateLimitResult indicating whether the request is allowed,
        how long to wait if not, and how many requests remain in the window.
        """
        now = time.monotonic() * 1000
        bucket = self._buckets.get(key)
        if bucket is None or (now - bucket["start"]) >= self._window_ms:
            self._buckets[key] = {"count": 1, "start": now}
            return RateLimitResult(True, 0, self._max - 1)
        if bucket["count"] >= self._max:
            retry = int(bucket["start"] + self._window_ms - now)
            return RateLimitResult(False, max(0, retry), 0)
        bucket["count"] += 1
        return RateLimitResult(True, 0, self._max - bucket["count"])

    def cleanup(self, max_age_ms: int = 300_000):
        """Remove stale buckets older than max_age_ms."""
        now = time.monotonic() * 1000
        stale = [k for k, v in self._buckets.items() if (now - v["start"]) > max_age_ms]
        for k in stale:
            del self._buckets[k]
