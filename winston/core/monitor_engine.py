"""
Monitor Engine — orchestrates background price checks and alert delivery.

Runs as a scheduled task, iterating all active watches, checking prices,
comparing against thresholds, and broadcasting alerts via Telegram/Discord.
"""

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("winston.core.monitor_engine")

ALERTS_FILE = "~/.winston/alerts.json"
MAX_ALERTS = 200
COOLDOWN_HOURS = 4


@dataclass
class Alert:
    """A price alert that was generated."""
    id: str
    watch_id: str
    item_name: str
    old_price: float
    new_price: float
    currency: str
    drop_percent: float
    message: str
    timestamp: str
    delivered: bool = False


class MonitorEngine:
    """
    Background monitoring orchestration layer.
    Connects PriceMonitorSkill ↔ ChannelManager ↔ Brain.
    """

    def __init__(self, price_monitor_skill, channel_manager=None, brain=None):
        self.price_monitor = price_monitor_skill
        self.channel_manager = channel_manager
        self.brain = brain

        self._alerts_file = Path(ALERTS_FILE).expanduser()
        self._alerts_file.parent.mkdir(parents=True, exist_ok=True)
        self._alerts: list[Alert] = []
        self._cooldowns: dict[str, datetime] = {}  # watch_id -> last alert time
        self._load_alerts()

    async def run_check_cycle(self) -> list[Alert]:
        """
        Run a full price check cycle:
        1. Call price_monitor.check_all()
        2. Filter alerts through cooldown
        3. Format and broadcast via channels
        4. Persist alert history
        """
        logger.info("Monitor engine: starting check cycle")

        result = self.price_monitor.execute(action="check_all")

        if not result.data:
            logger.info("Monitor engine: no alerts this cycle")
            return []

        new_alerts = []
        for alert_data in result.data:
            if not alert_data.get("alert"):
                continue

            watch_id = alert_data.get("watch_id", "")

            # Check cooldown
            if self._is_on_cooldown(watch_id):
                logger.info(f"Alert for {watch_id} suppressed (cooldown)")
                continue

            # Get the watched item for details
            item = self.price_monitor._watches.get(watch_id)
            if not item:
                continue

            alert = Alert(
                id=f"alert_{watch_id}_{datetime.now().strftime('%Y%m%d%H%M')}",
                watch_id=watch_id,
                item_name=item.name,
                old_price=item.baseline_price,
                new_price=item.current_price,
                currency=item.currency,
                drop_percent=self._calc_drop(item.baseline_price, item.current_price),
                message=alert_data.get("message", ""),
                timestamp=datetime.now().isoformat(),
                delivered=False,
            )

            new_alerts.append(alert)
            self._set_cooldown(watch_id)

        # Broadcast alerts
        if new_alerts:
            await self._broadcast_alerts(new_alerts)

        # Persist
        self._alerts.extend(new_alerts)
        if len(self._alerts) > MAX_ALERTS:
            self._alerts = self._alerts[-MAX_ALERTS:]
        self._save_alerts()

        logger.info(f"Monitor engine: cycle complete, {len(new_alerts)} new alerts")
        return new_alerts

    async def _broadcast_alerts(self, alerts: list[Alert]):
        """Format and send alerts via the channel manager."""
        if not self.channel_manager:
            logger.warning("No channel_manager — cannot broadcast alerts")
            return

        for alert in alerts:
            message = self._format_alert(alert)

            # Optionally use LLM to make the message more natural
            if self.brain:
                try:
                    message = self.brain.think(
                        f"Rewrite this price alert notification in a friendly, concise way. "
                        f"Keep the key information (item, prices, percentage). "
                        f"Use emojis sparingly. Here's the raw alert:\n\n{message}",
                        system_override=(
                            "You are sending a notification. Be concise and natural. "
                            "No JSON blocks. Keep it under 3 lines."
                        ),
                    )
                except Exception as e:
                    logger.warning(f"LLM alert rewrite failed, using raw format: {e}")

            try:
                await self.channel_manager.broadcast(message)
                alert.delivered = True
            except Exception as e:
                logger.error(f"Failed to broadcast alert for {alert.item_name}: {e}")

    @staticmethod
    def _format_alert(alert: Alert) -> str:
        """Format an alert into a notification message."""
        lines = [f"**Price Alert: {alert.item_name}**"]

        if alert.drop_percent > 0:
            lines.append(
                f"Price dropped {alert.drop_percent:.1f}%: "
                f"{alert.old_price:.2f} {alert.currency} → "
                f"{alert.new_price:.2f} {alert.currency}"
            )
        else:
            lines.append(f"Current price: {alert.new_price:.2f} {alert.currency}")

        if alert.message:
            lines.append(alert.message)

        return "\n".join(lines)

    def _is_on_cooldown(self, watch_id: str) -> bool:
        """Check if a watch is in the cooldown period."""
        last_alert = self._cooldowns.get(watch_id)
        if last_alert is None:
            return False
        return datetime.now() - last_alert < timedelta(hours=COOLDOWN_HOURS)

    def _set_cooldown(self, watch_id: str):
        """Set cooldown for a watch."""
        self._cooldowns[watch_id] = datetime.now()

    @staticmethod
    def _calc_drop(old_price: float, new_price: float) -> float:
        """Calculate percentage drop."""
        if old_price <= 0:
            return 0.0
        return ((old_price - new_price) / old_price) * 100

    def get_alert_history(self, limit: int = 20) -> list[Alert]:
        """Return recent alert history."""
        return self._alerts[-limit:]

    # ── Persistence ──

    def _load_alerts(self):
        """Load alert history from disk."""
        if self._alerts_file.exists():
            try:
                data = json.loads(self._alerts_file.read_text())
                for a in data.get("alerts", []):
                    self._alerts.append(Alert(**a))
                logger.info(f"Loaded {len(self._alerts)} alert history entries")
            except Exception as e:
                logger.warning(f"Failed to load alerts: {e}")

    def _save_alerts(self):
        """Persist alert history to disk."""
        data = {"alerts": [asdict(a) for a in self._alerts]}
        self._alerts_file.write_text(json.dumps(data, indent=2, default=str))
