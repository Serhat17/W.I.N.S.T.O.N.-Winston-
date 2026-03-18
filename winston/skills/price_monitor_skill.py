"""
Price Monitor Skill — watch product/flight/hotel prices and alert on drops.

Persists watched items to ~/.winston/price_watches.json.
Integrates with the scheduler for periodic background checks and
the channel manager to push Telegram/Discord alerts.
"""

import json
import logging
import secrets
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from winston.skills.base import BaseSkill, SkillResult

logger = logging.getLogger("winston.skills.price_monitor")

WATCHES_FILE = "~/.winston/price_watches.json"
MAX_HISTORY = 50


@dataclass
class WatchedItem:
    """A single price-tracked item."""
    id: str
    name: str
    category: str                  # "product", "flight", "hotel"
    url: str = ""                  # Direct product URL (if available)
    search_params: dict = field(default_factory=dict)  # origin/dest/date for flights
    baseline_price: float = 0.0    # Price when first added
    current_price: float = 0.0     # Last checked price
    lowest_price: float = 0.0      # Lowest seen so far
    currency: str = "EUR"
    threshold_percent: float = 5.0  # Alert when price drops by this %
    target_price: float = 0.0      # Optional: alert when price <= target
    created: str = ""
    last_checked: str = ""
    price_history: list = field(default_factory=list)  # [{timestamp, price}]
    status: str = "active"         # "active", "paused", "triggered"
    notify_channels: list = field(default_factory=lambda: ["telegram"])


class PriceMonitorSkill(BaseSkill):
    """Watch prices and get alerted when they drop."""

    name = "price_monitor"
    description = (
        "Monitor prices of products, flights, and hotels. "
        "Get notified when a price drops below a threshold or target. "
        "Use actions: watch, unwatch, list, check, check_all, status."
    )
    parameters = {
        "action": "Action: 'watch', 'unwatch', 'list', 'check', 'check_all', 'status'",
        "name": "(watch) Name/description of what to watch",
        "url": "(watch) Product URL to monitor (for products)",
        "category": "(watch) Type: 'product', 'flight', or 'hotel'",
        "origin": "(watch/flight) Origin airport code, e.g. 'DUS'",
        "destination": "(watch/flight/hotel) Destination code, e.g. 'IST'",
        "date": "(watch/flight) Travel date YYYY-MM-DD",
        "threshold": "(watch) Alert when price drops by this percent (default: 5)",
        "target_price": "(watch) Alert when price drops to this amount",
        "watch_id": "(unwatch/check) ID of the watch to act on",
    }

    def __init__(self, config=None):
        super().__init__(config)
        self._watches_file = Path(WATCHES_FILE).expanduser()
        self._watches_file.parent.mkdir(parents=True, exist_ok=True)
        self._watches: dict[str, WatchedItem] = {}
        self._load_watches()

        # Injected after construction (same pattern as SchedulerSkill)
        self.channel_manager = None
        self.scheduler = None

    def execute(self, **kwargs) -> SkillResult:
        """Route to the appropriate action handler."""
        action = kwargs.get("action", "list")

        handlers = {
            "watch": self._watch,
            "unwatch": self._unwatch,
            "list": self._list,
            "check": self._check,
            "check_all": self._check_all,
            "status": self._status,
        }

        handler = handlers.get(action)
        if not handler:
            return SkillResult(
                success=False,
                message=(
                    f"Unknown action: {action}. "
                    "Use: watch, unwatch, list, check, check_all, status."
                ),
            )

        return handler(**kwargs)

    # ── Actions ──

    def _watch(self, **kwargs) -> SkillResult:
        """Add a new price watch."""
        name = kwargs.get("name", "").strip()
        category = kwargs.get("category", "product").strip().lower()
        url = kwargs.get("url", "").strip()
        origin = kwargs.get("origin", "").strip().upper()
        destination = kwargs.get("destination", "").strip().upper()
        date = kwargs.get("date", "").strip()
        threshold = float(kwargs.get("threshold", 5.0))
        target_price = float(kwargs.get("target_price", 0))

        if not name and not url:
            return SkillResult(
                success=False,
                message="Please provide a name or URL for the item to watch.",
            )

        if category == "flight" and not (origin and destination):
            return SkillResult(
                success=False,
                message="Flight watches need 'origin' and 'destination' airport codes.",
            )

        watch_id = f"watch_{secrets.token_hex(4)}"
        now = datetime.now().isoformat()

        # Try to get current price
        initial_price = 0.0
        currency = "EUR"

        if url and category == "product":
            initial_price, currency = self._scrape_product_price(url)
        elif category == "flight":
            initial_price, currency = self._search_flight_price(origin, destination, date)
        elif category == "hotel":
            initial_price, currency = self._search_hotel_price(destination, date)

        item = WatchedItem(
            id=watch_id,
            name=name or url,
            category=category,
            url=url,
            search_params={
                "origin": origin,
                "destination": destination,
                "date": date,
            },
            baseline_price=initial_price,
            current_price=initial_price,
            lowest_price=initial_price,
            currency=currency,
            threshold_percent=threshold,
            target_price=target_price,
            created=now,
            last_checked=now,
            price_history=[{"timestamp": now, "price": initial_price}] if initial_price else [],
            status="active",
        )

        self._watches[watch_id] = item
        self._save_watches()
        self._ensure_scheduler_task()

        price_info = f" (current: {initial_price:.2f} {currency})" if initial_price else " (initial price not yet available)"

        return SkillResult(
            success=True,
            message=(
                f"Now watching: **{item.name}**{price_info}\n"
                f"Category: {category} | Threshold: {threshold}% drop"
                + (f" | Target: {target_price:.2f} {currency}" if target_price else "")
                + f"\nWatch ID: `{watch_id}`"
            ),
            data={"watch_id": watch_id, "item": asdict(item)},
        )

    def _unwatch(self, **kwargs) -> SkillResult:
        """Remove a price watch."""
        watch_id = kwargs.get("watch_id", "").strip()

        if not watch_id:
            return SkillResult(
                success=False,
                message="Please provide the watch_id to remove.",
            )

        if watch_id not in self._watches:
            return SkillResult(
                success=False,
                message=f"Watch '{watch_id}' not found.",
            )

        item = self._watches.pop(watch_id)
        self._save_watches()
        self._ensure_scheduler_task()

        return SkillResult(
            success=True,
            message=f"Stopped watching: **{item.name}** ({watch_id})",
        )

    def _list(self, **kwargs) -> SkillResult:
        """List all active price watches."""
        if not self._watches:
            return SkillResult(
                success=True,
                message="No active price watches. Use 'watch' to add one.",
            )

        lines = ["**Active Price Watches:**\n"]
        for item in self._watches.values():
            status_icon = {"active": "🟢", "paused": "⏸️", "triggered": "🔔"}.get(item.status, "❓")
            price_str = f"{item.current_price:.2f} {item.currency}" if item.current_price else "N/A"
            baseline_str = f"{item.baseline_price:.2f}" if item.baseline_price else "N/A"

            change = ""
            if item.baseline_price and item.current_price:
                pct = ((item.current_price - item.baseline_price) / item.baseline_price) * 100
                arrow = "📉" if pct < 0 else "📈" if pct > 0 else "➡️"
                change = f" {arrow} {pct:+.1f}%"

            lines.append(
                f"{status_icon} **{item.name}** [{item.category}]\n"
                f"   Current: {price_str}{change} (started at {baseline_str})\n"
                f"   Lowest: {item.lowest_price:.2f} | Last checked: {item.last_checked[:16]}\n"
                f"   ID: `{item.id}`\n"
            )

        return SkillResult(
            success=True,
            message="\n".join(lines),
            data=[asdict(w) for w in self._watches.values()],
            speak=False,
        )

    def _check(self, **kwargs) -> SkillResult:
        """Check a specific watch for price changes."""
        watch_id = kwargs.get("watch_id", "").strip()

        if not watch_id:
            return SkillResult(success=False, message="Provide a watch_id to check.")

        item = self._watches.get(watch_id)
        if not item:
            return SkillResult(success=False, message=f"Watch '{watch_id}' not found.")

        return self._check_item(item)

    def _check_all(self, **kwargs) -> SkillResult:
        """Check ALL active watches. Returns alerts for price drops."""
        if not self._watches:
            return SkillResult(success=True, message="No watches to check.")

        alerts = []
        checked = 0
        for item in list(self._watches.values()):
            if item.status != "active":
                continue
            result = self._check_item(item)
            checked += 1
            if result.data and result.data.get("alert"):
                alerts.append(result.data)

        self._save_watches()

        if alerts:
            lines = [f"**Price Alerts** ({len(alerts)}):\n"]
            for a in alerts:
                lines.append(a.get("message", ""))
            return SkillResult(
                success=True,
                message="\n".join(lines),
                data=alerts,
                speak=False,
            )

        return SkillResult(
            success=True,
            message=f"Checked {checked} watches. No price drops detected.",
        )

    def _status(self, **kwargs) -> SkillResult:
        """Show monitoring statistics."""
        total = len(self._watches)
        active = sum(1 for w in self._watches.values() if w.status == "active")
        triggered = sum(1 for w in self._watches.values() if w.status == "triggered")
        paused = sum(1 for w in self._watches.values() if w.status == "paused")

        categories = {}
        for w in self._watches.values():
            categories[w.category] = categories.get(w.category, 0) + 1

        cat_str = ", ".join(f"{k}: {v}" for k, v in categories.items()) if categories else "none"

        return SkillResult(
            success=True,
            message=(
                f"**Price Monitor Status**\n"
                f"Total watches: {total} (active: {active}, triggered: {triggered}, paused: {paused})\n"
                f"Categories: {cat_str}"
            ),
        )

    # ── Price checking ──

    def _check_item(self, item: WatchedItem) -> SkillResult:
        """Check a single item for price changes."""
        old_price = item.current_price
        new_price = 0.0
        currency = item.currency

        if item.url and item.category == "product":
            new_price, currency = self._scrape_product_price(item.url)
        elif item.category == "flight":
            p = item.search_params
            new_price, currency = self._search_flight_price(
                p.get("origin", ""), p.get("destination", ""), p.get("date", "")
            )
        elif item.category == "hotel":
            p = item.search_params
            new_price, currency = self._search_hotel_price(
                p.get("destination", ""), p.get("date", "")
            )

        now = datetime.now().isoformat()
        item.last_checked = now

        if new_price <= 0:
            return SkillResult(
                success=True,
                message=f"Could not get current price for {item.name}.",
                data={"alert": False, "watch_id": item.id},
            )

        item.current_price = new_price
        item.currency = currency

        if new_price < item.lowest_price or item.lowest_price <= 0:
            item.lowest_price = new_price

        # Record history (keep last MAX_HISTORY entries)
        item.price_history.append({"timestamp": now, "price": new_price})
        if len(item.price_history) > MAX_HISTORY:
            item.price_history = item.price_history[-MAX_HISTORY:]

        # Check for alert conditions
        alert = False
        alert_msg = ""

        if old_price > 0 and new_price < old_price:
            drop_pct = ((old_price - new_price) / old_price) * 100
            if drop_pct >= item.threshold_percent:
                alert = True
                alert_msg = (
                    f"📉 **Price Drop!** {item.name}\n"
                    f"   Was: {old_price:.2f} {currency} → Now: {new_price:.2f} {currency} "
                    f"(-{drop_pct:.1f}%)\n"
                )
                if item.url:
                    alert_msg += f"   Link: {item.url}\n"
                item.status = "triggered"

        if item.target_price > 0 and new_price <= item.target_price:
            alert = True
            alert_msg += (
                f"🎯 **Target Price Reached!** {item.name}\n"
                f"   Target: {item.target_price:.2f} {currency} | "
                f"Current: {new_price:.2f} {currency}\n"
            )
            item.status = "triggered"

        self._save_watches()

        return SkillResult(
            success=True,
            message=alert_msg or f"{item.name}: {new_price:.2f} {currency} (no change).",
            data={"alert": alert, "watch_id": item.id, "message": alert_msg},
        )

    # ── Price fetching helpers ──

    def _scrape_product_price(self, url: str) -> tuple[float, str]:
        """Scrape a product price from a URL. Returns (price, currency)."""
        try:
            from winston.utils.scraper import fetch_page, extract_product_price
            html = fetch_page(url)
            if html:
                price = extract_product_price(html, url=url)
                if price:
                    return price.amount, price.currency
        except Exception as e:
            logger.error(f"Error scraping {url}: {e}")
        return 0.0, "EUR"

    def _search_flight_price(self, origin: str, destination: str,
                             date: str) -> tuple[float, str]:
        """Search for a flight price. Returns (price, currency)."""
        try:
            from winston.utils.scraper import search_flight_prices
            results = search_flight_prices(origin, destination, date, max_results=3)
            if results:
                return results[0].amount, results[0].currency
        except Exception as e:
            logger.error(f"Error searching flights {origin}->{destination}: {e}")
        return 0.0, "EUR"

    def _search_hotel_price(self, destination: str, date: str) -> tuple[float, str]:
        """Search for a hotel price. Returns (price, currency)."""
        try:
            from winston.utils.scraper import search_hotel_prices
            results = search_hotel_prices(destination, date, max_results=3)
            if results:
                return results[0].amount, results[0].currency
        except Exception as e:
            logger.error(f"Error searching hotels in {destination}: {e}")
        return 0.0, "EUR"

    # ── Persistence ──

    def _load_watches(self):
        """Load watches from disk."""
        if self._watches_file.exists():
            try:
                data = json.loads(self._watches_file.read_text())
                for item_data in data.get("watches", []):
                    item = WatchedItem(**item_data)
                    self._watches[item.id] = item
                logger.info(f"Loaded {len(self._watches)} price watches")
            except Exception as e:
                logger.warning(f"Failed to load watches: {e}")

    def _save_watches(self):
        """Persist watches to disk."""
        data = {"watches": [asdict(w) for w in self._watches.values()]}
        self._watches_file.write_text(json.dumps(data, indent=2, default=str))

    # ── Scheduler integration ──

    def _ensure_scheduler_task(self):
        """Enable/disable the price_monitor_check scheduler task based on watch count."""
        if not self.scheduler:
            return

        has_active = any(w.status == "active" for w in self._watches.values())

        if has_active:
            # Ensure the task exists and is enabled
            task = self.scheduler._tasks.get("price_monitor_check")
            if task:
                if not task.enabled:
                    task.enabled = True
                    if self.scheduler.scheduler:
                        self.scheduler._add_job(task)
                    self.scheduler._save_schedules()
                    logger.info("Enabled price_monitor_check scheduler task")
        else:
            # No active watches — disable the task
            task = self.scheduler._tasks.get("price_monitor_check")
            if task and task.enabled:
                task.enabled = False
                if self.scheduler.scheduler:
                    try:
                        self.scheduler.scheduler.remove_job("price_monitor_check")
                    except Exception:
                        pass
                self.scheduler._save_schedules()
                logger.info("Disabled price_monitor_check scheduler task (no active watches)")
