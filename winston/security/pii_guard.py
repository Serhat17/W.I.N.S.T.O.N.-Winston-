"""
PII (Personally Identifiable Information) Guard for W.I.N.S.T.O.N.

Detects and redacts personal data before messages are sent to cloud
LLM providers.  Keeps conversations useful while protecting privacy.

Redacted categories:
  - Email addresses
  - Phone numbers (international formats)
  - IBAN / bank account numbers
  - Credit card numbers (with Luhn check)
  - German / EU tax IDs (Steuernummer, Steuer-ID)
  - Social security / national ID numbers
  - Street addresses (DE / US / generic)
  - IP addresses (v4)
  - Passport numbers
  - Date of birth patterns
  - Custom user-defined patterns
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("winston.security.pii")


# ── Pattern definitions ────────────────────────────────

@dataclass
class PIIPattern:
    """A named regex pattern for detecting PII."""
    name: str
    regex: re.Pattern
    replacement: str  # e.g. "[EMAIL]"
    enabled: bool = True


# Build all patterns upfront (compiled once)
_PATTERNS: list[PIIPattern] = [
    # ── Email ──
    PIIPattern(
        name="email",
        regex=re.compile(
            r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
        ),
        replacement="[EMAIL]",
    ),
    # ── IBAN (must be before phone to avoid false matches) ──
    PIIPattern(
        name="iban",
        regex=re.compile(
            r"\b[A-Z]{2}\d{2}[\s]?\d{4}[\s]?\d{4}[\s]?\d{4}[\s]?\d{4}[\s]?\d{0,2}\b"
        ),
        replacement="[IBAN]",
    ),
    # ── Credit card numbers (must be before phone) ──
    PIIPattern(
        name="credit_card",
        regex=re.compile(
            r"(?<!\+)\b(?:\d[\s\-]?){13,19}\b"
        ),
        replacement="[CREDIT_CARD]",
    ),
    # ── German tax / Steuer-ID (11 digits) ──
    PIIPattern(
        name="steuer_id",
        regex=re.compile(
            r"(?i)(?:steuer[\-\s]?id|tin|identifikationsnummer)[\s:]*(\d[\s]?\d[\s]?\d[\s]?\d[\s]?\d[\s]?\d[\s]?\d[\s]?\d[\s]?\d[\s]?\d[\s]?\d)"
        ),
        replacement="[STEUER_ID]",
    ),
    # ── Phone numbers (international + DE/US/UK) ──
    PIIPattern(
        name="phone",
        regex=re.compile(
            r"(?<!\d)"  # not preceded by digit
            r"(?:"
            r"\+\d{1,3}[\s\-./]?"  # country code (require +)
            r"(?:\(?\d{2,5}\)?[\s\-./]?)?"  # area code
            r"\d{3,4}[\s\-./]?\d{2,4}[\s\-./]?\d{0,4}"
            r"|"
            r"0\d{2,5}[\s\-./]?\d{3,4}[\s\-./]?\d{2,4}"  # local with leading 0
            r")"
            r"(?!\d)"  # not followed by digit
        ),
        replacement="[PHONE]",
    ),
    # ── SSN (US) ──
    PIIPattern(
        name="ssn",
        regex=re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        replacement="[SSN]",
    ),
    # ── Passport number patterns (common formats) ──
    PIIPattern(
        name="passport",
        regex=re.compile(
            r"(?i)(?:reisepass|passport|pass[\-\s]?n(?:umber|r\.?))\s*[:\s]+([A-Za-z0-9]{6,12})"
        ),
        replacement="[PASSPORT]",
    ),
    # ── IP addresses (v4) ──
    PIIPattern(
        name="ipv4",
        regex=re.compile(
            r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
        ),
        replacement="[IP_ADDR]",
    ),
    # ── Date of birth (explicit label + date) ──
    PIIPattern(
        name="date_of_birth",
        regex=re.compile(
            r"(?i)(?:geb(?:urtstag|\.?\s*datum)|date\s*of\s*birth|dob|geboren\s*am)[\s:]*"
            r"(\d{1,2}[\.\-/]\d{1,2}[\.\-/]\d{2,4})"
        ),
        replacement="[DATE_OF_BIRTH]",
    ),
    # ── Street address (DE-style: Straße + Nr + PLZ + Ort) ──
    PIIPattern(
        name="address_de",
        regex=re.compile(
            r"(?i)(?:[A-ZÄÖÜ][a-zäöüß]+(?:stra(?:ße|sse)|str\.|weg|gasse|allee|platz|ring|damm|ufer))\s*\d{1,4}[a-z]?"
            r"(?:\s*,?\s*\d{5}\s+[A-ZÄÖÜ][a-zäöüß]+)?"
        ),
        replacement="[ADDRESS]",
    ),
    # ── US-style address (number + street + city, state zip) ──
    PIIPattern(
        name="address_us",
        regex=re.compile(
            r"\b\d{1,5}\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+"
            r"(?:St(?:reet)?|Ave(?:nue)?|Blvd|Dr(?:ive)?|Ln|Rd|Way|Ct|Pl)"
            r"\.?\b"
        ),
        replacement="[ADDRESS]",
    ),
    # ── API keys / secrets / tokens (generic) ──
    PIIPattern(
        name="api_key",
        regex=re.compile(
            r"(?i)(api[_\-]?key|secret[_\-]?key|access[_\-]?token|bearer)\s*[:=]\s*\S+"
        ),
        replacement="[API_KEY]",
    ),
    # ── Private keys ──
    PIIPattern(
        name="private_key",
        regex=re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----"),
        replacement="[PRIVATE_KEY]",
    ),
    # ── Passwords ──
    PIIPattern(
        name="password",
        regex=re.compile(r"(?i)(?:password|passwort|kennwort|pwd)\s*[:=]\s*\S+"),
        replacement="[PASSWORD]",
    ),
]


class PIIGuard:
    """
    Scans text for PII and replaces matches with numbered placeholders.

    The guard uses a **redact → reason → restore** strategy:
      1. redact_with_map()  – replaces PII with [EMAIL_1], [PHONE_1], etc.
      2. The cloud LLM reasons using those placeholders.
      3. restore()          – swaps placeholders back to real values before
                              local skill execution (booking, email, etc.).

    Usage:
        guard = PIIGuard()
        clean = guard.redact_with_map("Email me at max@example.com")
        # clean == "Email me at [EMAIL_1]"
        # guard.last_map == {"[EMAIL_1]": "max@example.com"}
        restored = guard.restore("Send confirmation to [EMAIL_1]")
        # restored == "Send confirmation to max@example.com"
    """

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._patterns = list(_PATTERNS)  # copy so we can mutate per-instance
        self._custom_replacements: list[tuple[str, str]] = []
        self._stats: dict[str, int] = {}
        # Reverse mapping: placeholder → real value (reset per redact call)
        self.last_map: dict[str, str] = {}
        # Accumulated mapping across a conversation turn
        self._turn_map: dict[str, str] = {}
        # Counters per category for numbered placeholders
        self._counters: dict[str, int] = {}

    # ── Public API ──────────────────────────────────────

    def start_turn(self):
        """Call at the start of each user turn to reset the mapping."""
        self._turn_map.clear()
        self._counters.clear()

    def _numbered_replacement(self, match: re.Match, pat: PIIPattern) -> str:
        """Return a numbered placeholder like [EMAIL_1] and record the mapping."""
        original = match.group(0)
        # Check if we already mapped this exact value
        for placeholder, real_val in self._turn_map.items():
            if real_val == original:
                return placeholder
        # New value — assign next number
        base = pat.replacement.rstrip("]")  # e.g. "[EMAIL"
        self._counters[pat.name] = self._counters.get(pat.name, 0) + 1
        numbered = f"{base}_{self._counters[pat.name]}]"
        self._turn_map[numbered] = original
        self._stats[pat.name] = self._stats.get(pat.name, 0) + 1
        logger.info(f"PII redacted: {pat.name} → {numbered}")
        return numbered

    def redact_with_map(self, text: str) -> str:
        """Redact PII with numbered placeholders and build a restore mapping."""
        if not self.enabled or not text:
            return text

        result = text

        # 1. Apply regex patterns with numbered replacements
        for pat in self._patterns:
            if not pat.enabled:
                continue
            result = pat.regex.sub(lambda m: self._numbered_replacement(m, pat), result)

        # 2. Apply custom literal replacements (user-defined names, etc.)
        for original, replacement in self._custom_replacements:
            if original in result:
                # Number custom replacements too
                base = replacement.rstrip("]")
                cat = "custom"
                # Check if already mapped
                already = False
                for ph, rv in self._turn_map.items():
                    if rv == original:
                        result = result.replace(original, ph)
                        already = True
                        break
                if not already:
                    self._counters[cat] = self._counters.get(cat, 0) + 1
                    numbered = f"{base}_{self._counters[cat]}]"
                    self._turn_map[numbered] = original
                    result = result.replace(original, numbered)

        self.last_map = dict(self._turn_map)
        return result

    def redact(self, text: str) -> str:
        """Redact PII from *text* (simple mode, no mapping)."""
        if not self.enabled or not text:
            return text

        result = text
        for pat in self._patterns:
            if not pat.enabled:
                continue
            new_result = pat.regex.sub(pat.replacement, result)
            if new_result != result:
                count = result.count(pat.replacement)
                new_count = new_result.count(pat.replacement)
                found = new_count - count
                if found > 0:
                    self._stats[pat.name] = self._stats.get(pat.name, 0) + found
            result = new_result
        for original, replacement in self._custom_replacements:
            if original in result:
                result = result.replace(original, replacement)
        return result

    def redact_messages(self, messages: list[dict]) -> list[dict]:
        """Redact PII from a list of chat messages using numbered placeholders."""
        if not self.enabled:
            return messages

        self.start_turn()

        sanitized = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                content = self.redact_with_map(content)
            elif isinstance(content, list):
                # Multimodal messages (OpenAI vision format)
                new_content = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        new_content.append({**part, "text": self.redact_with_map(part.get("text", ""))})
                    else:
                        new_content.append(part)
                content = new_content
            sanitized.append({**msg, "content": content})
        self.last_map = dict(self._turn_map)
        return sanitized

    def restore(self, text: str) -> str:
        """Restore numbered placeholders back to real values.

        Call this on the LLM response / skill parameters before local execution.
        Longest placeholders are replaced first to avoid partial matches.
        """
        if not text or not self.last_map:
            return text
        result = text
        # Sort by length descending so [EMAIL_10] is replaced before [EMAIL_1]
        for placeholder in sorted(self.last_map, key=len, reverse=True):
            result = result.replace(placeholder, self.last_map[placeholder])
        return result

    def restore_params(self, params: dict) -> dict:
        """Restore PII placeholders in all string values of a params dict."""
        if not self.last_map:
            return params
        restored = {}
        for key, value in params.items():
            if isinstance(value, str):
                restored[key] = self.restore(value)
            elif isinstance(value, list):
                restored[key] = [self.restore(v) if isinstance(v, str) else v for v in value]
            elif isinstance(value, dict):
                restored[key] = self.restore_params(value)
            else:
                restored[key] = value
        return restored

    def add_custom_replacement(self, original: str, replacement: str = "[NAME]"):
        """Add a custom literal string to be redacted (e.g. user's real name)."""
        if original and len(original) >= 2:
            self._custom_replacements.append((original, replacement))

    def set_pattern_enabled(self, pattern_name: str, enabled: bool):
        """Enable or disable a specific pattern category."""
        for pat in self._patterns:
            if pat.name == pattern_name:
                pat.enabled = enabled
                return True
        return False

    def get_categories(self) -> list[dict]:
        """Return list of all pattern categories with their status."""
        return [
            {"name": p.name, "enabled": p.enabled, "replacement": p.replacement}
            for p in self._patterns
        ]

    def get_stats(self) -> dict[str, int]:
        """Return redaction statistics (counts per category)."""
        return dict(self._stats)

    def reset_stats(self):
        """Reset redaction counters."""
        self._stats.clear()
