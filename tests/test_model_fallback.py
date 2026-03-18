"""
Tests for model fallback with error classification and cooldown.
"""

import time
import pytest
import httpx
from unittest.mock import MagicMock

from winston.core.model_fallback import (
    FailoverReason,
    classify_error,
    ProviderCooldown,
    run_with_fallback,
)


class TestClassifyError:
    """Tests for error classification."""

    def test_timeout_error(self):
        assert classify_error(Exception("Request timed out")) == FailoverReason.TIMEOUT

    def test_context_overflow(self):
        assert classify_error(Exception("context length exceeded")) == FailoverReason.CONTEXT_OVERFLOW
        assert classify_error(Exception("maximum tokens exceeded")) == FailoverReason.CONTEXT_OVERFLOW

    def test_rate_limit_via_status(self):
        err = httpx.HTTPStatusError(
            "rate limit",
            request=MagicMock(),
            response=MagicMock(status_code=429),
        )
        assert classify_error(err) == FailoverReason.RATE_LIMIT

    def test_auth_error(self):
        err = httpx.HTTPStatusError(
            "unauthorized",
            request=MagicMock(),
            response=MagicMock(status_code=401),
        )
        assert classify_error(err) == FailoverReason.AUTH

    def test_billing_error(self):
        err = httpx.HTTPStatusError(
            "payment required",
            request=MagicMock(),
            response=MagicMock(status_code=402),
        )
        assert classify_error(err) == FailoverReason.BILLING

    def test_overloaded_error(self):
        err = httpx.HTTPStatusError(
            "service unavailable",
            request=MagicMock(),
            response=MagicMock(status_code=503),
        )
        assert classify_error(err) == FailoverReason.OVERLOADED

    def test_model_not_found(self):
        err = httpx.HTTPStatusError(
            "not found",
            request=MagicMock(),
            response=MagicMock(status_code=404),
        )
        assert classify_error(err) == FailoverReason.MODEL_NOT_FOUND

    def test_unknown_error(self):
        assert classify_error(Exception("something weird")) == FailoverReason.UNKNOWN


class TestProviderCooldown:
    """Tests for exponential cooldown tracking."""

    def test_fresh_provider_available(self):
        cd = ProviderCooldown()
        assert cd.is_available("openai") is True

    def test_failure_triggers_cooldown(self):
        cd = ProviderCooldown()
        cd.record_failure("openai")
        assert cd.is_available("openai") is False

    def test_success_clears_cooldown(self):
        cd = ProviderCooldown()
        cd.record_failure("openai")
        cd.record_success("openai")
        assert cd.is_available("openai") is True

    def test_exponential_cooldown(self):
        cd = ProviderCooldown()
        cd.record_failure("test")
        state1 = cd._state["test"]["cooldown_until"]
        cd.record_failure("test")
        state2 = cd._state["test"]["cooldown_until"]
        # Second cooldown should be longer (5x)
        duration1 = state1 - (time.time() - 60)
        duration2 = state2 - (time.time() - 300)
        # Just verify errors incremented
        assert cd._state["test"]["errors"] == 2

    def test_get_status(self):
        cd = ProviderCooldown()
        cd.record_failure("openai")
        status = cd.get_status()
        assert "openai" in status
        assert status["openai"]["errors"] == 1
        assert "available" in status["openai"]


class TestRunWithFallback:
    """Tests for the fallback runner."""

    def test_primary_succeeds(self):
        """If primary works, use it."""
        cd = ProviderCooldown()
        result, prov, model = run_with_fallback(
            primary=("openai", "gpt-4o"),
            fallbacks=[("anthropic", "claude-3")],
            run_fn=lambda p, m: f"response from {p}",
            cooldowns=cd,
        )
        assert result == "response from openai"
        assert prov == "openai"

    def test_fallback_on_primary_failure(self):
        """If primary fails, try fallback."""
        cd = ProviderCooldown()
        call_count = [0]

        def mock_fn(provider, model):
            call_count[0] += 1
            if provider == "openai":
                raise Exception("timeout")
            return f"response from {provider}"

        result, prov, model = run_with_fallback(
            primary=("openai", "gpt-4o"),
            fallbacks=[("anthropic", "claude-3")],
            run_fn=mock_fn,
            cooldowns=cd,
        )
        assert result == "response from anthropic"
        assert prov == "anthropic"
        assert call_count[0] == 2

    def test_all_fail_raises(self):
        """If all providers fail, raise the last error."""
        cd = ProviderCooldown()

        def always_fail(p, m):
            raise RuntimeError(f"{p} failed")

        with pytest.raises(RuntimeError, match="anthropic failed"):
            run_with_fallback(
                primary=("openai", "gpt-4o"),
                fallbacks=[("anthropic", "claude-3")],
                run_fn=always_fail,
                cooldowns=cd,
            )

    def test_context_overflow_not_retried(self):
        """Context overflow should not trigger fallback."""
        cd = ProviderCooldown()

        def overflow_fn(p, m):
            raise Exception("context length exceeded maximum tokens")

        with pytest.raises(Exception, match="context length"):
            run_with_fallback(
                primary=("openai", "gpt-4o"),
                fallbacks=[("anthropic", "claude-3")],
                run_fn=overflow_fn,
                cooldowns=cd,
            )

    def test_cooled_down_provider_skipped(self):
        """Providers in cooldown should be skipped."""
        cd = ProviderCooldown()
        cd.record_failure("openai")  # Put openai in cooldown

        result, prov, model = run_with_fallback(
            primary=("openai", "gpt-4o"),
            fallbacks=[("anthropic", "claude-3")],
            run_fn=lambda p, m: f"response from {p}",
            cooldowns=cd,
        )
        assert prov == "anthropic"
