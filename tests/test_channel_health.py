"""
Tests for channel health monitoring.
"""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from winston.core.channel_health import (
    ChannelHealthSnapshot,
    HealthStatus,
    evaluate_health,
    ChannelHealthMonitor,
    STALE_THRESHOLD_S,
    STARTUP_GRACE_S,
    COOLDOWN_S,
    MAX_RESTARTS_PER_HOUR,
)


class TestEvaluateHealth:
    """Tests for health evaluation logic."""

    def test_stopped_channel(self):
        snap = ChannelHealthSnapshot(running=False)
        assert evaluate_health(snap) == HealthStatus.STOPPED

    def test_startup_grace_period(self):
        snap = ChannelHealthSnapshot(
            running=True,
            last_start_at=time.time(),
        )
        assert evaluate_health(snap) == HealthStatus.STARTUP

    def test_healthy_with_recent_event(self):
        snap = ChannelHealthSnapshot(
            running=True,
            last_start_at=time.time() - 300,
            last_event_at=time.time() - 60,
        )
        assert evaluate_health(snap) == HealthStatus.HEALTHY

    def test_stale_no_events(self):
        snap = ChannelHealthSnapshot(
            running=True,
            last_start_at=time.time() - 3600,
            last_event_at=time.time() - (STALE_THRESHOLD_S + 60),
        )
        assert evaluate_health(snap) == HealthStatus.STALE

    def test_healthy_no_event_yet(self):
        """Running with no events but past startup grace: healthy (no stale threshold)."""
        snap = ChannelHealthSnapshot(
            running=True,
            last_start_at=time.time() - 300,
            last_event_at=None,
        )
        assert evaluate_health(snap) == HealthStatus.HEALTHY


class TestChannelHealthMonitor:
    """Tests for the ChannelHealthMonitor."""

    def _make_channel(self, running=True, last_event=None, last_start=None):
        ch = MagicMock()
        ch.is_running.return_value = running
        ch.last_event_at = last_event
        ch.last_start_at = last_start
        ch.stop = AsyncMock()
        ch.start = AsyncMock()
        return ch

    def _make_cm(self, channels_dict):
        cm = MagicMock()
        cm._channels = channels_dict
        return cm

    @pytest.mark.asyncio
    async def test_restart_stale_channel(self):
        """Should restart a stale channel."""
        ch = self._make_channel(
            running=True,
            last_event=time.time() - (STALE_THRESHOLD_S + 120),
            last_start=time.time() - 7200,
        )
        cm = self._make_cm({"telegram": ch})
        monitor = ChannelHealthMonitor(cm, check_interval_s=1)
        await monitor._check_all()
        ch.stop.assert_awaited_once()
        ch.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_restart_healthy(self):
        """Should not restart a healthy channel."""
        ch = self._make_channel(
            running=True,
            last_event=time.time() - 60,
            last_start=time.time() - 3600,
        )
        cm = self._make_cm({"telegram": ch})
        monitor = ChannelHealthMonitor(cm, check_interval_s=1)
        await monitor._check_all()
        ch.stop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_respects_cooldown(self):
        """Should not restart if within cooldown period."""
        ch = self._make_channel(
            running=True,
            last_event=time.time() - (STALE_THRESHOLD_S + 120),
            last_start=time.time() - 7200,
        )
        cm = self._make_cm({"telegram": ch})
        monitor = ChannelHealthMonitor(cm, check_interval_s=1)
        # First restart
        await monitor._check_all()
        ch.stop.reset_mock()
        ch.start.reset_mock()
        # Second restart should be blocked by cooldown
        await monitor._check_all()
        ch.stop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_hourly_limit(self):
        """Should respect MAX_RESTARTS_PER_HOUR."""
        ch = self._make_channel(
            running=False,
            last_start=time.time() - 7200,
        )
        cm = self._make_cm({"telegram": ch})
        monitor = ChannelHealthMonitor(cm, check_interval_s=1)
        # Fill up the restart log
        monitor._restart_log["telegram"] = [time.time() - 60] * MAX_RESTARTS_PER_HOUR
        monitor._last_restart["telegram"] = 0  # Don't block via cooldown
        await monitor._check_all()
        ch.stop.assert_not_awaited()  # Should be blocked by hourly limit

    def test_get_status(self):
        """get_status should return channel health info."""
        ch = self._make_channel(
            running=True,
            last_event=time.time() - 60,
            last_start=time.time() - 3600,
        )
        cm = self._make_cm({"telegram": ch})
        monitor = ChannelHealthMonitor(cm)
        status = monitor.get_status()
        assert "telegram" in status
        assert status["telegram"]["status"] == "healthy"
        assert status["telegram"]["running"] is True

    @pytest.mark.asyncio
    async def test_restart_stopped_channel(self):
        """Should restart a stopped channel."""
        ch = self._make_channel(running=False)
        cm = self._make_cm({"discord": ch})
        monitor = ChannelHealthMonitor(cm, check_interval_s=1)
        await monitor._check_all()
        ch.stop.assert_awaited_once()
        ch.start.assert_called_once()
