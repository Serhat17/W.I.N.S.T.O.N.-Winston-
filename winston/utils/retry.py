"""
Retry utility — Declarative retry policies with exponential backoff and jitter.

Inspired by OpenClaw's retry.ts pattern.  Provides a simple decorator and a
direct ``retry_call`` helper for wrapping flaky operations (HTTP requests,
LLM API calls, channel delivery).
"""

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Any

logger = logging.getLogger("winston.utils.retry")


@dataclass
class RetryPolicy:
    """Declarative retry configuration."""
    attempts: int = 3
    min_delay: float = 0.3       # seconds
    max_delay: float = 30.0      # seconds
    backoff_factor: float = 2.0  # exponential multiplier
    jitter: float = 0.1          # ±10% random variance
    retry_on: tuple = ()         # Exception types to retry on (empty = all)
    retry_on_status: tuple = (429, 502, 503, 504)  # HTTP status codes
    label: str = ""              # Descriptive label for logging


# ── Pre-built policies ───────────────────────────────────────────────

DEFAULT_POLICY = RetryPolicy()

HTTP_POLICY = RetryPolicy(
    attempts=3,
    min_delay=0.5,
    max_delay=15.0,
    label="http",
)

LLM_POLICY = RetryPolicy(
    attempts=2,
    min_delay=1.0,
    max_delay=30.0,
    backoff_factor=3.0,
    label="llm",
)

CHANNEL_POLICY = RetryPolicy(
    attempts=3,
    min_delay=0.5,
    max_delay=10.0,
    label="channel",
)


def retry_call(
    fn: Callable[..., Any],
    *args,
    policy: RetryPolicy = DEFAULT_POLICY,
    on_retry: Optional[Callable[[int, Exception, float], None]] = None,
    **kwargs,
) -> Any:
    """
    Call *fn* with retry logic according to *policy*.

    Args:
        fn: The callable to invoke.
        *args: Positional arguments for *fn*.
        policy: Retry configuration.
        on_retry: Optional callback(attempt, exception, delay) called before each retry.
        **kwargs: Keyword arguments for *fn*.

    Returns:
        The return value of *fn* on success.

    Raises:
        The last exception if all attempts are exhausted.
    """
    last_exc: Optional[Exception] = None
    delay = policy.min_delay

    for attempt in range(1, policy.attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc

            # Check if we should retry this exception type
            if policy.retry_on and not isinstance(exc, policy.retry_on):
                raise

            # Check for HTTP status codes (if the exception carries one)
            status = _extract_status(exc)
            if status and status not in policy.retry_on_status:
                raise

            if attempt >= policy.attempts:
                break

            # Parse Retry-After header if available
            retry_after = _parse_retry_after(exc)
            actual_delay = retry_after if retry_after else delay

            # Apply jitter
            jitter_range = actual_delay * policy.jitter
            actual_delay += random.uniform(-jitter_range, jitter_range)
            actual_delay = max(0.1, min(actual_delay, policy.max_delay))

            label = f" [{policy.label}]" if policy.label else ""
            logger.warning(
                f"Retry{label} attempt {attempt}/{policy.attempts} "
                f"after {actual_delay:.1f}s — {type(exc).__name__}: {exc}"
            )

            if on_retry:
                on_retry(attempt, exc, actual_delay)

            time.sleep(actual_delay)

            # Exponential backoff for next attempt
            delay = min(delay * policy.backoff_factor, policy.max_delay)

    # All attempts exhausted
    raise last_exc  # type: ignore[misc]


def _extract_status(exc: Exception) -> Optional[int]:
    """Try to extract an HTTP status code from an exception."""
    # httpx.HTTPStatusError
    if hasattr(exc, "response") and hasattr(exc.response, "status_code"):
        return exc.response.status_code
    # requests.HTTPError
    if hasattr(exc, "response") and hasattr(exc.response, "status_code"):
        return exc.response.status_code
    return None


def _parse_retry_after(exc: Exception) -> Optional[float]:
    """Try to parse a Retry-After header value from an exception's response."""
    try:
        response = getattr(exc, "response", None)
        if response is None:
            return None
        header = None
        if hasattr(response, "headers"):
            header = response.headers.get("Retry-After") or response.headers.get("retry-after")
        if header is None:
            return None
        # Could be seconds (integer) or HTTP-date
        try:
            return float(header)
        except ValueError:
            return None
    except Exception:
        return None
