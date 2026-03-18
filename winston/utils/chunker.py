"""
Markdown-aware text chunking for messaging channels.

Splits long messages respecting:
- Code fences (```...```) — never breaks inside them; if forced to split,
  closes the fence in the current chunk and reopens it in the next.
- Paragraph boundaries — prefers splitting at blank lines.
- Sentence boundaries — falls back to period/newline breaks.
- Per-channel limits (Telegram 4096, Discord 2000, etc.)

Inspired by OpenClaw's auto-reply/chunk.ts.
"""

import re
import logging
from typing import Optional

logger = logging.getLogger("winston.utils.chunker")

# Per-channel character limits
CHANNEL_LIMITS = {
    "telegram": 4096,
    "discord": 2000,
    "whatsapp": 4096,
    "default": 4096,
}

# Regex to find fenced code blocks (``` or ~~~)
_FENCE_RE = re.compile(r"^(?P<indent>\s*)(?P<marker>`{3,}|~{3,})(?P<lang>\S*)\s*$", re.MULTILINE)


def chunk_message(text: str, max_len: int = 4096, channel: str = "default") -> list[str]:
    """
    Split *text* into chunks that fit within *max_len*, preserving Markdown structure.

    Args:
        text: The full message text.
        max_len: Maximum characters per chunk.  If 0, uses the channel default.
        channel: Channel name for looking up default limits.

    Returns:
        List of text chunks, each ≤ max_len characters.
    """
    if not max_len:
        max_len = CHANNEL_LIMITS.get(channel, CHANNEL_LIMITS["default"])

    if len(text) <= max_len:
        return [text]

    # Find all code fence boundaries
    fences = _find_fence_spans(text)

    chunks: list[str] = []
    pos = 0
    reopen_prefix = ""

    while pos < len(text):
        remaining = len(text) - pos
        budget = max_len - len(reopen_prefix)

        if remaining <= budget:
            # Everything fits in one final chunk
            chunks.append(reopen_prefix + text[pos:])
            break

        window_end = pos + budget

        # Find a safe break point
        break_at = _find_break_point(text, pos, window_end, fences)

        raw_chunk = text[pos:break_at]

        # Check if we're splitting inside a code fence
        fence_to_close = _fence_at_position(break_at, fences)

        if fence_to_close:
            # Close the fence in this chunk, reopen in next
            close_line = f"\n{fence_to_close['marker']}"
            raw_chunk = raw_chunk + close_line
            reopen_prefix = f"{fence_to_close['marker']}{fence_to_close['lang']}\n"
        else:
            reopen_prefix = ""

        chunk_text = (reopen_prefix if chunks and _was_in_fence(pos, fences) else "") + raw_chunk
        # On first iteration or when not in a fence, just use raw
        if not chunks or not _was_in_fence(pos, fences):
            chunk_text = raw_chunk

        # If we already had a reopen prefix from a previous fence split, prepend it
        if chunks and reopen_prefix and not fence_to_close:
            pass  # reopen_prefix was already consumed

        chunks.append(chunk_text.rstrip())
        pos = break_at

        # Skip leading whitespace at the start of the next chunk
        while pos < len(text) and text[pos] in ("\n", " ") and not reopen_prefix:
            pos += 1

    return [c for c in chunks if c.strip()]


def _find_fence_spans(text: str) -> list[dict]:
    """Find all code fence open/close spans in the text."""
    spans = []
    open_fence = None

    for match in _FENCE_RE.finditer(text):
        marker = match.group("marker")
        lang = match.group("lang") or ""

        if open_fence is None:
            # Opening fence
            open_fence = {
                "start": match.start(),
                "marker": marker,
                "lang": lang,
            }
        elif marker[:1] == open_fence["marker"][:1] and len(marker) >= len(open_fence["marker"]):
            # Closing fence (same type, at least as many chars)
            spans.append({
                "start": open_fence["start"],
                "end": match.end(),
                "marker": open_fence["marker"],
                "lang": open_fence["lang"],
            })
            open_fence = None

    # Unclosed fence — extends to end of text
    if open_fence:
        spans.append({
            "start": open_fence["start"],
            "end": len(text),
            "marker": open_fence["marker"],
            "lang": open_fence["lang"],
        })

    return spans


def _find_break_point(text: str, start: int, end: int, fences: list[dict]) -> int:
    """Find the best break point between *start* and *end*."""
    # Don't break inside a code fence if we can avoid it
    for fence in fences:
        if fence["start"] < end <= fence["end"] and start < fence["start"]:
            # The window ends inside a fence — break before the fence
            candidate = fence["start"]
            if candidate > start:
                return candidate

    # Strategy 1: Break at a blank line (paragraph boundary)
    search_zone = text[start:end]
    blank_line = search_zone.rfind("\n\n")
    if blank_line != -1 and blank_line > len(search_zone) * 0.3:
        return start + blank_line + 2  # After the blank line

    # Strategy 2: Break at a single newline
    newline = search_zone.rfind("\n")
    if newline != -1 and newline > len(search_zone) * 0.3:
        return start + newline + 1

    # Strategy 3: Break at a space
    space = search_zone.rfind(" ")
    if space != -1 and space > len(search_zone) * 0.5:
        return start + space + 1

    # Last resort: hard break at limit
    return end


def _fence_at_position(pos: int, fences: list[dict]) -> Optional[dict]:
    """Return the fence span that contains *pos*, or None."""
    for fence in fences:
        if fence["start"] < pos < fence["end"]:
            return fence
    return None


def _was_in_fence(pos: int, fences: list[dict]) -> bool:
    """Return True if *pos* is inside a code fence."""
    return _fence_at_position(pos, fences) is not None
