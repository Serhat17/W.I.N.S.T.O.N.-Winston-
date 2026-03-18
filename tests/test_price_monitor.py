"""
Tests for PriceMonitorSkill — CRUD operations, persistence, price checks.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from winston.skills.price_monitor_skill import PriceMonitorSkill, WatchedItem, WATCHES_FILE
from winston.skills.base import SkillResult


@pytest.fixture
def price_skill(tmp_path):
    """PriceMonitorSkill with a temp watches file."""
    skill = PriceMonitorSkill()
    skill._watches_file = tmp_path / "price_watches.json"
    skill._watches = {}
    return skill


# ── Watch / Unwatch CRUD ──


class TestWatch:
    def test_watch_product(self, price_skill):
        result = price_skill.execute(
            action="watch",
            name="iPhone 16 Pro",
            category="product",
            url="https://example.com/iphone",
            threshold=10,
        )
        assert result.success is True
        assert "iPhone 16 Pro" in result.message
        assert len(price_skill._watches) == 1
        assert result.data["watch_id"].startswith("watch_")

    def test_watch_flight(self, price_skill):
        result = price_skill.execute(
            action="watch",
            name="DUS to IST April",
            category="flight",
            origin="DUS",
            destination="IST",
            date="2025-04-15",
        )
        assert result.success is True
        assert "DUS to IST" in result.message

    def test_watch_hotel(self, price_skill):
        result = price_skill.execute(
            action="watch",
            name="Istanbul Hotel",
            category="hotel",
            destination="IST",
        )
        assert result.success is True

    def test_watch_no_name_or_url_fails(self, price_skill):
        result = price_skill.execute(action="watch")
        assert result.success is False
        assert "name or URL" in result.message

    def test_watch_flight_needs_airports(self, price_skill):
        result = price_skill.execute(
            action="watch", name="Flight", category="flight"
        )
        assert result.success is False
        assert "origin" in result.message.lower()

    def test_unwatch(self, price_skill):
        # First add a watch
        r1 = price_skill.execute(action="watch", name="Test Item", category="product", url="https://example.com")
        watch_id = r1.data["watch_id"]

        # Then remove it
        r2 = price_skill.execute(action="unwatch", watch_id=watch_id)
        assert r2.success is True
        assert watch_id not in price_skill._watches

    def test_unwatch_nonexistent(self, price_skill):
        result = price_skill.execute(action="unwatch", watch_id="nonexistent")
        assert result.success is False

    def test_unwatch_no_id(self, price_skill):
        result = price_skill.execute(action="unwatch")
        assert result.success is False


# ── List / Status ──


class TestListStatus:
    def test_list_empty(self, price_skill):
        result = price_skill.execute(action="list")
        assert result.success is True
        assert "No active" in result.message

    def test_list_with_watches(self, price_skill):
        price_skill.execute(action="watch", name="Item 1", category="product", url="https://a.com")
        price_skill.execute(action="watch", name="Item 2", category="product", url="https://b.com")
        result = price_skill.execute(action="list")
        assert result.success is True
        assert "Item 1" in result.message
        assert "Item 2" in result.message

    def test_status(self, price_skill):
        price_skill.execute(action="watch", name="Widget", category="product", url="https://c.com")
        result = price_skill.execute(action="status")
        assert result.success is True
        assert "Total watches: 1" in result.message

    def test_status_empty(self, price_skill):
        result = price_skill.execute(action="status")
        assert result.success is True
        assert "Total watches: 0" in result.message


# ── Check ──


class TestCheck:
    def test_check_nonexistent(self, price_skill):
        result = price_skill.execute(action="check", watch_id="nope")
        assert result.success is False

    def test_check_no_id(self, price_skill):
        result = price_skill.execute(action="check")
        assert result.success is False

    def test_check_all_empty(self, price_skill):
        result = price_skill.execute(action="check_all")
        assert result.success is True
        assert "No watches" in result.message


# ── Persistence ──


class TestPersistence:
    def test_save_and_load(self, price_skill, tmp_path):
        # Add a watch
        price_skill.execute(action="watch", name="Persistent Item", category="product", url="https://d.com")
        assert price_skill._watches_file.exists()

        # Create new skill instance pointing to same file
        skill2 = PriceMonitorSkill()
        skill2._watches_file = price_skill._watches_file
        skill2._watches = {}
        skill2._load_watches()

        assert len(skill2._watches) == 1
        item = list(skill2._watches.values())[0]
        assert item.name == "Persistent Item"

    def test_file_format(self, price_skill):
        price_skill.execute(action="watch", name="Format Test", category="product", url="https://e.com")
        data = json.loads(price_skill._watches_file.read_text())
        assert "watches" in data
        assert len(data["watches"]) == 1
        assert data["watches"][0]["name"] == "Format Test"


# ── Price history bounds ──


class TestPriceHistory:
    def test_history_bounded(self, price_skill):
        """Price history should never exceed MAX_HISTORY entries."""
        from unittest.mock import patch
        from winston.skills.price_monitor_skill import MAX_HISTORY

        price_skill.execute(action="watch", name="History Test", category="product", url="https://example.com")
        item = list(price_skill._watches.values())[0]

        # Simulate many price checks
        for i in range(MAX_HISTORY + 20):
            item.price_history.append({"timestamp": f"2025-01-01T{i:02d}:00:00", "price": 100 + i})

        # Mock the scraper to return a price (so _check_item doesn't return early)
        with patch.object(price_skill, "_scrape_product_price", return_value=(99.0, "EUR")):
            price_skill._check_item(item)

        assert len(item.price_history) <= MAX_HISTORY


# ── Unknown action ──


class TestUnknownAction:
    def test_unknown_action(self, price_skill):
        result = price_skill.execute(action="explode")
        assert result.success is False
        assert "Unknown action" in result.message


# ── Skill attributes ──


class TestSkillAttributes:
    def test_name(self):
        skill = PriceMonitorSkill()
        assert skill.name == "price_monitor"

    def test_description(self):
        skill = PriceMonitorSkill()
        assert skill.description
        assert "price" in skill.description.lower()

    def test_parameters(self):
        skill = PriceMonitorSkill()
        assert "action" in skill.parameters
        assert "name" in skill.parameters
        assert "url" in skill.parameters
