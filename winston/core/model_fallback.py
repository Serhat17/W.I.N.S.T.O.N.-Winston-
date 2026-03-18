"""
Model fallback with error classification and exponential cooldown.
Inspired by OpenClaw's model-fallback approach.
"""

import logging
import time
from enum import Enum
from typing import Callable, Tuple

logger = logging.getLogger("winston.fallback")


class FailoverReason(Enum):
    AUTH = "auth"
    BILLING = "billing"
    OVERLOADED = "overloaded"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    MODEL_NOT_FOUND = "model_not_found"
    CONTEXT_OVERFLOW = "context_overflow"
    UNKNOWN = "unknown"


def classify_error(error: Exception) -> FailoverReason:
    """Classify an LLM error for fallback decisions."""
    msg = str(error).lower()

    # HTTP status codes from httpx
    status = None
    if hasattr(error, "response") and error.response is not None:
        status = error.response.status_code
    elif hasattr(error, "status_code"):
        status = error.status_code

    if status is not None:
        if status == 401:
            return FailoverReason.AUTH
        if status == 402:
            return FailoverReason.BILLING
        if status == 429:
            return FailoverReason.RATE_LIMIT
        if status in (502, 503):
            return FailoverReason.OVERLOADED
        if status == 404:
            return FailoverReason.MODEL_NOT_FOUND

    # Timeout patterns
    if "timeout" in msg or "timed out" in msg:
        return FailoverReason.TIMEOUT

    # Context overflow patterns
    if "context" in msg and ("length" in msg or "window" in msg or "overflow" in msg):
        return FailoverReason.CONTEXT_OVERFLOW
    if "maximum" in msg and "tokens" in msg:
        return FailoverReason.CONTEXT_OVERFLOW

    return FailoverReason.UNKNOWN


class ProviderCooldown:
    """Tracks provider health with exponential cooldown."""

    def __init__(self):
        self._state: dict = {}  # provider -> {errors, cooldown_until}

    def record_failure(self, provider: str):
        state = self._state.setdefault(provider, {"errors": 0, "cooldown_until": 0.0})
        state["errors"] += 1
        # Exponential: 60s -> 5m -> 25m -> 1h max
        cooldown_s = min(3600, 60 * (5 ** min(state["errors"] - 1, 3)))
        state["cooldown_until"] = time.time() + cooldown_s
        logger.info(f"Provider {provider}: failure #{state['errors']}, cooldown {cooldown_s}s")

    def record_success(self, provider: str):
        self._state.pop(provider, None)

    def is_available(self, provider: str) -> bool:
        state = self._state.get(provider)
        if not state:
            return True
        return time.time() >= state["cooldown_until"]

    def get_status(self) -> dict:
        """Return status of all tracked providers."""
        now = time.time()
        result = {}
        for provider, state in self._state.items():
            remaining = max(0, state["cooldown_until"] - now)
            result[provider] = {
                "errors": state["errors"],
                "cooldown_remaining_s": int(remaining),
                "available": remaining <= 0,
            }
        return result


def run_with_fallback(
    primary: Tuple[str, str],
    fallbacks: list,
    run_fn: Callable,
    cooldowns: ProviderCooldown,
) -> Tuple[str, str, str]:
    """Try primary, then fallbacks. Returns (response, used_provider, used_model).

    Args:
        primary: (provider_name, model_name)
        fallbacks: list of (provider_name, model_name) tuples
        run_fn: fn(provider, model) -> response text
        cooldowns: ProviderCooldown instance for tracking health
    """
    candidates = [primary] + fallbacks
    last_error = None

    for provider, model in candidates:
        if not cooldowns.is_available(provider):
            logger.info(f"Skipping {provider} (in cooldown)")
            continue
        try:
            result = run_fn(provider, model)
            cooldowns.record_success(provider)
            return (result, provider, model)
        except Exception as e:
            reason = classify_error(e)
            logger.warning(f"Provider {provider}/{model} failed: {reason.value} - {e}")
            if reason == FailoverReason.CONTEXT_OVERFLOW:
                raise  # Not retryable via fallback
            cooldowns.record_failure(provider)
            last_error = e

    raise last_error or RuntimeError("All providers failed")
