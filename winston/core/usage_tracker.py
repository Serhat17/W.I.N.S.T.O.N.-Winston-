"""
Token usage tracking across providers per session + daily persistence.
Inspired by OpenClaw's usage tracking approach.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("winston.usage")


@dataclass
class SessionUsage:
    provider: str = "ollama"
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    requests: int = 0
    started_at: float = field(default_factory=time.time)


class UsageTracker:
    """Tracks token usage across providers per session + persists daily totals."""

    def __init__(self, data_dir: str = "~/.winston"):
        self._data_dir = Path(data_dir).expanduser()
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._usage_file = self._data_dir / "usage.json"
        self._session = SessionUsage()
        self._daily = self._load()

    def record(self, provider: str, model: str, usage: Optional[dict]):
        """Record a single LLM call's token usage."""
        if not usage:
            return
        self._session.provider = provider
        self._session.model = model
        self._session.input_tokens += usage.get("input", 0)
        self._session.output_tokens += usage.get("output", 0)
        self._session.requests += 1
        # Accumulate daily
        today = time.strftime("%Y-%m-%d")
        if today not in self._daily:
            self._daily[today] = {"input": 0, "output": 0, "requests": 0}
        self._daily[today]["input"] += usage.get("input", 0)
        self._daily[today]["output"] += usage.get("output", 0)
        self._daily[today]["requests"] += 1

    def get_session_summary(self) -> dict:
        """Return current session usage summary."""
        return {
            "input_tokens": self._session.input_tokens,
            "output_tokens": self._session.output_tokens,
            "total_tokens": self._session.input_tokens + self._session.output_tokens,
            "requests": self._session.requests,
            "provider": self._session.provider,
            "model": self._session.model,
        }

    def get_daily_summary(self, days: int = 7) -> dict:
        """Return the last N days of usage."""
        sorted_days = sorted(self._daily.keys(), reverse=True)[:days]
        return {d: self._daily[d] for d in sorted_days}

    def save(self):
        """Persist daily usage to disk."""
        try:
            self._usage_file.write_text(json.dumps(self._daily, indent=2))
        except Exception as e:
            logger.warning(f"Failed to save usage data: {e}")

    def _load(self) -> dict:
        if self._usage_file.exists():
            try:
                return json.loads(self._usage_file.read_text())
            except (json.JSONDecodeError, OSError):
                return {}
        return {}
