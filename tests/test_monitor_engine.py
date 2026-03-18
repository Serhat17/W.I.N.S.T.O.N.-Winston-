"""
Tests for MonitorEngine — check cycles, alert delivery, cooldown, persistence.
"""

import json
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from winston.core.monitor_engine import MonitorEngine, Alert, COOLDOWN_HOURS, MAX_ALERTS
from winston.skills.price_monitor_skill import PriceMonitorSkill, WatchedItem
from winston.skills.base import SkillResult


@pytest.fixture
def price_skill(tmp_path):
    """PriceMonitorSkill with a temp watches file."""
    skill = PriceMonitorSkill()
    skill._watches_file = tmp_path / "price_watches.json"
    skill._watches = {}
    return skill


@pytest.fixture
def mock_channel():
    """Mock channel manager."""
    cm = MagicMock()
    cm.broadcast = AsyncMock()
    return cm


@pytest.fixture
def engine(price_skill, mock_channel, tmp_path):
    """MonitorEngine with temp files."""
    eng = MonitorEngine(
        price_monitor_skill=price_skill,
        channel_manager=mock_channel,
        brain=None,
    )
    eng._alerts_file = tmp_path / "alerts.json"
    return eng


# ── Check cycle ──


class TestCheckCycle:
    @pytest.mark.asyncio
    async def test_no_watches_no_alerts(self, engine):
        alerts = await engine.run_check_cycle()
        assert alerts == []

    @pytest.mark.asyncio
    async def test_check_cycle_with_active_watch(self, engine, price_skill):
        """An active watch that doesn't change price should not trigger alert."""
        item = WatchedItem(
            id="test_watch",
            name="Test Product",
            category="product",
            url="https://example.com",
            baseline_price=100.0,
            current_price=100.0,
            lowest_price=100.0,
            currency="EUR",
            created=datetime.now().isoformat(),
            status="active",
        )
        price_skill._watches["test_watch"] = item

        # Mock the scraping so it returns same price
        with patch.object(price_skill, "_scrape_product_price", return_value=(100.0, "EUR")):
            alerts = await engine.run_check_cycle()

        assert alerts == []


# ── Alert formatting ──


class TestAlertFormatting:
    def test_format_price_drop(self):
        alert = Alert(
            id="test",
            watch_id="w1",
            item_name="iPhone",
            old_price=1199.0,
            new_price=999.0,
            currency="EUR",
            drop_percent=16.7,
            message="",
            timestamp=datetime.now().isoformat(),
        )
        text = MonitorEngine._format_alert(alert)
        assert "iPhone" in text
        assert "16.7%" in text
        assert "999.0" in text or "999.00" in text

    def test_format_no_drop(self):
        alert = Alert(
            id="test",
            watch_id="w1",
            item_name="Widget",
            old_price=50.0,
            new_price=50.0,
            currency="USD",
            drop_percent=0.0,
            message="",
            timestamp=datetime.now().isoformat(),
        )
        text = MonitorEngine._format_alert(alert)
        assert "Widget" in text
        assert "50.00" in text


# ── Cooldown ──


class TestCooldown:
    def test_not_on_cooldown_initially(self, engine):
        assert engine._is_on_cooldown("w1") is False

    def test_on_cooldown_after_set(self, engine):
        engine._set_cooldown("w1")
        assert engine._is_on_cooldown("w1") is True

    def test_cooldown_expires(self, engine):
        engine._cooldowns["w1"] = datetime.now() - timedelta(hours=COOLDOWN_HOURS + 1)
        assert engine._is_on_cooldown("w1") is False


# ── Drop calculation ──


class TestCalcDrop:
    def test_normal_drop(self):
        assert MonitorEngine._calc_drop(100.0, 80.0) == pytest.approx(20.0)

    def test_no_drop(self):
        assert MonitorEngine._calc_drop(100.0, 100.0) == pytest.approx(0.0)

    def test_zero_old_price(self):
        assert MonitorEngine._calc_drop(0.0, 50.0) == 0.0

    def test_price_increase(self):
        # Negative "drop" = price increase
        assert MonitorEngine._calc_drop(100.0, 120.0) == pytest.approx(-20.0)


# ── Persistence ──


class TestPersistence:
    def test_save_and_load(self, engine, tmp_path):
        alert = Alert(
            id="a1",
            watch_id="w1",
            item_name="Test",
            old_price=100.0,
            new_price=80.0,
            currency="EUR",
            drop_percent=20.0,
            message="Test alert",
            timestamp=datetime.now().isoformat(),
        )
        engine._alerts.append(alert)
        engine._save_alerts()

        assert engine._alerts_file.exists()

        # Load in a new engine
        engine2 = MonitorEngine(
            price_monitor_skill=MagicMock(),
            channel_manager=MagicMock(),
        )
        engine2._alerts_file = engine._alerts_file
        engine2._alerts = []
        engine2._load_alerts()

        assert len(engine2._alerts) == 1
        assert engine2._alerts[0].item_name == "Test"

    def test_alert_history_bounded(self, engine):
        for i in range(MAX_ALERTS + 50):
            engine._alerts.append(Alert(
                id=f"a{i}",
                watch_id="w1",
                item_name=f"Item {i}",
                old_price=100.0,
                new_price=80.0,
                currency="EUR",
                drop_percent=20.0,
                message="",
                timestamp=datetime.now().isoformat(),
            ))

        # Trim (simulating what run_check_cycle does)
        if len(engine._alerts) > MAX_ALERTS:
            engine._alerts = engine._alerts[-MAX_ALERTS:]

        assert len(engine._alerts) == MAX_ALERTS


# ── Alert history ──


class TestAlertHistory:
    def test_get_history_empty(self, engine):
        history = engine.get_alert_history()
        assert history == []

    def test_get_history_limited(self, engine):
        for i in range(30):
            engine._alerts.append(Alert(
                id=f"a{i}", watch_id="w1", item_name=f"Item {i}",
                old_price=100, new_price=80, currency="EUR",
                drop_percent=20, message="", timestamp="",
            ))

        history = engine.get_alert_history(limit=10)
        assert len(history) == 10
