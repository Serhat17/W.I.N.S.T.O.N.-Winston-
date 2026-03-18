"""
Tests for the fixed-window rate limiter.
"""

import time
import pytest
from winston.security.rate_limiter import RateLimiter, RateLimitResult


class TestRateLimiter:
    """Tests for RateLimiter."""

    def test_allows_within_limit(self):
        """Requests within the limit should be allowed."""
        rl = RateLimiter(max_requests=5, window_ms=60_000)
        for i in range(5):
            result = rl.consume("client1")
            assert result.allowed is True
            assert result.remaining == 4 - i

    def test_blocks_over_limit(self):
        """Requests exceeding the limit should be blocked."""
        rl = RateLimiter(max_requests=3, window_ms=60_000)
        for _ in range(3):
            rl.consume("client1")
        result = rl.consume("client1")
        assert result.allowed is False
        assert result.remaining == 0
        assert result.retry_after_ms > 0

    def test_separate_keys(self):
        """Different keys should have independent buckets."""
        rl = RateLimiter(max_requests=2, window_ms=60_000)
        rl.consume("a")
        rl.consume("a")
        result_a = rl.consume("a")
        result_b = rl.consume("b")
        assert result_a.allowed is False
        assert result_b.allowed is True

    def test_window_reset(self):
        """After the window expires, the bucket should reset."""
        rl = RateLimiter(max_requests=1, window_ms=50)  # 50ms window
        rl.consume("x")
        result = rl.consume("x")
        assert result.allowed is False
        time.sleep(0.06)  # Wait for window to expire
        result = rl.consume("x")
        assert result.allowed is True

    def test_cleanup_removes_stale(self):
        """Cleanup should remove old buckets."""
        rl = RateLimiter(max_requests=10, window_ms=60_000)
        rl.consume("old_client")
        # Force the bucket to look old
        rl._buckets["old_client"]["start"] = time.monotonic() * 1000 - 400_000
        rl.consume("fresh_client")
        rl.cleanup(max_age_ms=300_000)
        assert "old_client" not in rl._buckets
        assert "fresh_client" in rl._buckets

    def test_result_is_namedtuple(self):
        """RateLimitResult should be a proper NamedTuple."""
        rl = RateLimiter(max_requests=5, window_ms=60_000)
        result = rl.consume("test")
        assert isinstance(result, RateLimitResult)
        assert isinstance(result, tuple)
        assert result.allowed is True

    def test_retry_after_decreases(self):
        """Retry-after should decrease as time passes within window."""
        rl = RateLimiter(max_requests=1, window_ms=200)
        rl.consume("x")
        r1 = rl.consume("x")
        time.sleep(0.05)
        r2 = rl.consume("x")
        assert r1.retry_after_ms >= r2.retry_after_ms

    def test_single_request_limit(self):
        """Edge case: max_requests=1."""
        rl = RateLimiter(max_requests=1, window_ms=60_000)
        r = rl.consume("z")
        assert r.allowed is True
        assert r.remaining == 0
        r2 = rl.consume("z")
        assert r2.allowed is False
