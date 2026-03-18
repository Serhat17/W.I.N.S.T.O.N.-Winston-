"""
In-memory content cache with TTL for web fetches.

Avoids re-fetching the same URL within a short window.
Used by WebFetchSkill and optionally by scraper utilities.
"""

import logging
import time
from typing import Optional

logger = logging.getLogger("winston.utils.web_cache")

# Cache storage: url -> (content, timestamp)
_cache: dict[str, tuple[str, float]] = {}

# Defaults
TTL_SECONDS = 900  # 15 minutes
MAX_ENTRIES = 100


def cache_get(url: str) -> Optional[str]:
    """Return cached content for *url* if it exists and hasn't expired."""
    entry = _cache.get(url)
    if entry is None:
        return None

    content, ts = entry
    if time.time() - ts > TTL_SECONDS:
        # Expired — remove and return miss
        _cache.pop(url, None)
        logger.debug(f"Cache expired for {url}")
        return None

    logger.debug(f"Cache hit for {url}")
    return content


def cache_set(url: str, content: str) -> None:
    """Store *content* for *url* with the current timestamp."""
    # Evict oldest entries when cache is full
    if len(_cache) >= MAX_ENTRIES:
        _evict_oldest()

    _cache[url] = (content, time.time())
    logger.debug(f"Cached content for {url} ({len(content)} chars)")


def cache_clear() -> None:
    """Drop all cached entries."""
    _cache.clear()
    logger.debug("Cache cleared")


def cache_stats() -> dict:
    """Return basic cache statistics."""
    now = time.time()
    valid = sum(1 for _, ts in _cache.values() if now - ts <= TTL_SECONDS)
    return {"total": len(_cache), "valid": valid, "max": MAX_ENTRIES, "ttl_seconds": TTL_SECONDS}


def _evict_oldest() -> None:
    """Remove the oldest entry (by timestamp) to make room."""
    if not _cache:
        return
    oldest_url = min(_cache, key=lambda u: _cache[u][1])
    _cache.pop(oldest_url, None)
    logger.debug(f"Evicted oldest cache entry: {oldest_url}")
