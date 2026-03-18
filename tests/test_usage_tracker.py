"""
Tests for token usage tracking.
"""

import json
import time
import pytest
from pathlib import Path
from winston.core.usage_tracker import UsageTracker, SessionUsage


class TestUsageTracker:
    """Tests for UsageTracker."""

    def test_record_increments_session(self, tmp_path):
        """Recording usage should increment session counters."""
        tracker = UsageTracker(data_dir=str(tmp_path))
        tracker.record("openai", "gpt-4o", {"input": 100, "output": 50})
        summary = tracker.get_session_summary()
        assert summary["input_tokens"] == 100
        assert summary["output_tokens"] == 50
        assert summary["total_tokens"] == 150
        assert summary["requests"] == 1

    def test_record_multiple(self, tmp_path):
        """Multiple records should accumulate."""
        tracker = UsageTracker(data_dir=str(tmp_path))
        tracker.record("openai", "gpt-4o", {"input": 100, "output": 50})
        tracker.record("openai", "gpt-4o", {"input": 200, "output": 100})
        summary = tracker.get_session_summary()
        assert summary["input_tokens"] == 300
        assert summary["output_tokens"] == 150
        assert summary["requests"] == 2

    def test_record_none_usage(self, tmp_path):
        """Recording None usage should be a no-op."""
        tracker = UsageTracker(data_dir=str(tmp_path))
        tracker.record("openai", "gpt-4o", None)
        summary = tracker.get_session_summary()
        assert summary["requests"] == 0

    def test_daily_accumulation(self, tmp_path):
        """Usage should accumulate in daily buckets."""
        tracker = UsageTracker(data_dir=str(tmp_path))
        tracker.record("ollama", "qwen", {"input": 50, "output": 25})
        daily = tracker.get_daily_summary()
        today = time.strftime("%Y-%m-%d")
        assert today in daily
        assert daily[today]["input"] == 50
        assert daily[today]["requests"] == 1

    def test_save_and_load(self, tmp_path):
        """Usage should persist to disk and reload."""
        tracker1 = UsageTracker(data_dir=str(tmp_path))
        tracker1.record("openai", "gpt-4o", {"input": 100, "output": 50})
        tracker1.save()

        # Load into a new tracker
        tracker2 = UsageTracker(data_dir=str(tmp_path))
        daily = tracker2.get_daily_summary()
        today = time.strftime("%Y-%m-%d")
        assert today in daily
        assert daily[today]["input"] == 100

    def test_session_provider_model(self, tmp_path):
        """Session summary should reflect the last recorded provider/model."""
        tracker = UsageTracker(data_dir=str(tmp_path))
        tracker.record("openai", "gpt-4o", {"input": 10, "output": 5})
        tracker.record("anthropic", "claude-3", {"input": 20, "output": 10})
        summary = tracker.get_session_summary()
        assert summary["provider"] == "anthropic"
        assert summary["model"] == "claude-3"

    def test_daily_summary_limit(self, tmp_path):
        """get_daily_summary should respect the days limit."""
        tracker = UsageTracker(data_dir=str(tmp_path))
        # Manually insert old data
        tracker._daily["2020-01-01"] = {"input": 1, "output": 1, "requests": 1}
        tracker._daily["2020-01-02"] = {"input": 2, "output": 2, "requests": 1}
        tracker.record("ollama", "qwen", {"input": 10, "output": 5})
        daily = tracker.get_daily_summary(days=2)
        assert len(daily) <= 2

    def test_empty_load(self, tmp_path):
        """Loading from non-existent file should return empty dict."""
        tracker = UsageTracker(data_dir=str(tmp_path))
        assert tracker._daily == {}
