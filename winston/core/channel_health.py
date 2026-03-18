"""
Channel health monitoring with periodic checks and auto-restart.
Inspired by OpenClaw's channel-health-monitor approach.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger("winston.channel_health")

STALE_THRESHOLD_S = 30 * 60   # 30 minutes
STARTUP_GRACE_S = 120          # 2 minutes after start
MAX_RESTARTS_PER_HOUR = 10
COOLDOWN_S = 10 * 60           # Min 10 min between restarts


class HealthStatus(Enum):
    HEALTHY = "healthy"
    STALE = "stale"
    STOPPED = "stopped"
    STARTUP = "startup"


@dataclass
class ChannelHealthSnapshot:
    running: bool = False
    last_event_at: Optional[float] = None
    last_start_at: Optional[float] = None
    reconnect_attempts: int = 0


def evaluate_health(snapshot: ChannelHealthSnapshot) -> HealthStatus:
    """Evaluate the health status of a channel."""
    if not snapshot.running:
        return HealthStatus.STOPPED
    if snapshot.last_start_at and (time.time() - snapshot.last_start_at) < STARTUP_GRACE_S:
        return HealthStatus.STARTUP
    if snapshot.last_event_at and (time.time() - snapshot.last_event_at) > STALE_THRESHOLD_S:
        return HealthStatus.STALE
    return HealthStatus.HEALTHY


class ChannelHealthMonitor:
    """Periodic health checks + auto-restart for channels."""

    def __init__(self, channel_manager, check_interval_s: int = 300):
        self._cm = channel_manager
        self._interval = check_interval_s
        self._restart_log: dict = {}  # channel -> [restart_timestamps]
        self._last_restart: dict = {}  # channel -> timestamp
        self._running = False

    async def start(self):
        """Start the health monitor loop."""
        self._running = True
        await asyncio.sleep(60)  # Startup grace period
        while self._running:
            try:
                await self._check_all()
            except Exception as e:
                logger.error(f"Health check error: {e}")
            await asyncio.sleep(self._interval)

    async def stop(self):
        """Stop the health monitor."""
        self._running = False

    async def _check_all(self):
        """Check health of all registered channels."""
        for name, channel in self._cm._channels.items():
            snapshot = self._get_snapshot(name, channel)
            status = evaluate_health(snapshot)
            if status in (HealthStatus.STALE, HealthStatus.STOPPED):
                await self._try_restart(name, channel, status.value)

    def _get_snapshot(self, name: str, channel) -> ChannelHealthSnapshot:
        """Build a health snapshot for a channel."""
        return ChannelHealthSnapshot(
            running=channel.is_running(),
            last_event_at=getattr(channel, "last_event_at", None),
            last_start_at=getattr(channel, "last_start_at", None),
            reconnect_attempts=len(self._restart_log.get(name, [])),
        )

    async def _try_restart(self, name: str, channel, reason: str):
        """Attempt to restart a channel with rate limiting."""
        now = time.time()
        # Cooldown check
        if now - self._last_restart.get(name, 0) < COOLDOWN_S:
            return
        # Hourly limit check
        hour_ago = now - 3600
        log = self._restart_log.setdefault(name, [])
        log[:] = [t for t in log if t > hour_ago]
        if len(log) >= MAX_RESTARTS_PER_HOUR:
            logger.warning(f"Channel {name}: hit restart limit ({MAX_RESTARTS_PER_HOUR}/hour)")
            return
        # Execute restart
        logger.info(f"Restarting channel {name} (reason: {reason})")
        try:
            await channel.stop()
            await asyncio.sleep(2)
            asyncio.create_task(channel.start())
            log.append(now)
            self._last_restart[name] = now
        except Exception as e:
            logger.error(f"Failed to restart channel {name}: {e}")

    def get_status(self) -> dict:
        """Get health status of all channels."""
        result = {}
        for name, channel in self._cm._channels.items():
            snapshot = self._get_snapshot(name, channel)
            status = evaluate_health(snapshot)
            result[name] = {
                "status": status.value,
                "running": snapshot.running,
                "last_event_at": snapshot.last_event_at,
                "last_start_at": snapshot.last_start_at,
                "restart_count": len(self._restart_log.get(name, [])),
            }
        return result
