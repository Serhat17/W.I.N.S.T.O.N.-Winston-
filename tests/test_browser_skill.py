"""
Tests for the Browser Automation Skill.
Tests the sync Playwright-based browser skill with properly mocked browser objects.
Verifies the full navigate+action flow that users actually trigger.
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path
from winston.skills.browser_skill import BrowserSkill


@pytest.fixture
def browser_skill():
    """A fresh BrowserSkill instance."""
    return BrowserSkill()


@pytest.fixture
def skill_with_page(browser_skill, tmp_path):
    """Browser skill with a fully mocked sync Playwright page."""
    mock_page = MagicMock()
    mock_page.title.return_value = "Apple"
    # Start at about:blank so smart navigation doesn't skip goto
    mock_page.url = "about:blank"
    mock_page.evaluate.return_value = "Welcome to Apple"
    mock_page.goto.return_value = None
    mock_page.click.return_value = None
    mock_page.fill.return_value = None
    mock_page.query_selector.return_value = MagicMock(inner_text=MagicMock(return_value="Element text"))
    mock_page.query_selector_all.return_value = []  # No iframes for overlay dismissal

    # Make get_by_role/get_by_text return a locator with count=0 (no cookie banners)
    empty_locator = MagicMock()
    empty_locator.count.return_value = 0
    mock_page.get_by_role.return_value = empty_locator
    mock_page.get_by_text.return_value = empty_locator

    # Mock screenshot to actually write a PNG file
    def fake_screenshot(path=None, full_page=False):
        if path:
            Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    mock_page.screenshot.side_effect = fake_screenshot

    browser_skill._page = mock_page
    browser_skill._browser = MagicMock()
    browser_skill._playwright = MagicMock()
    browser_skill._screenshots_dir = tmp_path
    return browser_skill


class TestBrowserSkillMetadata:
    """Skill metadata tests."""

    def test_skill_name(self, browser_skill):
        assert browser_skill.name == "browser"

    def test_skill_has_description(self, browser_skill):
        assert "web page" in browser_skill.description.lower()

    def test_unknown_action_fails(self, browser_skill):
        result = browser_skill.execute(action="teleport")
        assert not result.success
        assert "Unknown" in result.message


class TestNoPageOpen:
    """All actions should fail gracefully when no page/browser is open and no URL is given."""

    def test_click_no_page_no_url(self, browser_skill):
        result = browser_skill.execute(action="click", selector="#btn")
        assert not result.success
        assert "No page is open" in result.message

    def test_fill_no_page_no_url(self, browser_skill):
        result = browser_skill.execute(action="fill", selector="#input", value="test")
        assert not result.success
        assert "No page is open" in result.message

    def test_extract_text_no_page_no_url(self, browser_skill):
        result = browser_skill.execute(action="extract_text")
        assert not result.success
        assert "No page is open" in result.message

    def test_screenshot_no_page_no_url(self, browser_skill):
        result = browser_skill.execute(action="screenshot_page")
        assert not result.success
        assert "No page is open" in result.message

    def test_run_script_no_page_no_url(self, browser_skill):
        result = browser_skill.execute(action="run_script", script="return 1")
        assert not result.success
        assert "No page is open" in result.message


class TestInputValidation:
    """Parameter validation tests."""

    def test_open_page_no_url(self, browser_skill):
        result = browser_skill.execute(action="open_page")
        assert not result.success
        assert "No URL" in result.message

    def test_click_no_selector(self, browser_skill):
        result = browser_skill.execute(action="click")
        assert not result.success
        assert "No CSS selector" in result.message

    def test_fill_no_selector(self, browser_skill):
        result = browser_skill.execute(action="fill", value="test")
        assert not result.success
        assert "No CSS selector" in result.message

    def test_fill_no_value(self, skill_with_page):
        result = skill_with_page.execute(action="fill", selector="#input")
        assert not result.success
        assert "No value" in result.message

    def test_run_script_no_script(self, browser_skill):
        result = browser_skill.execute(action="run_script")
        assert not result.success
        assert "No JavaScript" in result.message


class TestSmartDefaults:
    """Test that the skill picks the right default action."""

    def test_url_defaults_to_screenshot(self, skill_with_page):
        """When url is provided without action, should default to screenshot_page."""
        result = skill_with_page.execute(url="apple.com")
        assert result.success
        assert "Screenshot" in result.message
        skill_with_page._page.screenshot.assert_called_once()

    def test_no_url_defaults_to_open_page(self, browser_skill):
        """When nothing is provided, action defaults to open_page which needs a URL."""
        result = browser_skill.execute()
        assert not result.success
        assert "No URL" in result.message


class TestFullFlowWithMockedPage:
    """End-to-end flow tests with a mocked Playwright page.
    These test the actual user scenarios."""

    def test_navigate_and_screenshot(self, skill_with_page):
        """'Go to apple.com and take a screenshot' — the core user flow."""
        result = skill_with_page.execute(action="screenshot_page", url="apple.com")
        assert result.success
        assert "Apple" in result.message
        assert "Screenshot" in result.message
        # Verify navigation happened
        skill_with_page._page.goto.assert_called_once()
        # Verify screenshot happened
        skill_with_page._page.screenshot.assert_called_once()
        # Verify file was created
        assert result.data["path"] is not None
        assert Path(result.data["path"]).exists()

    def test_navigate_and_extract_text(self, skill_with_page):
        """'Go to apple.com and read the text'."""
        result = skill_with_page.execute(action="extract_text", url="apple.com")
        assert result.success
        assert "Welcome to Apple" in result.message
        skill_with_page._page.goto.assert_called_once()

    def test_navigate_and_click(self, skill_with_page):
        """'Go to apple.com and click the buy button'."""
        result = skill_with_page.execute(action="click", url="apple.com", selector="#buy-btn")
        assert result.success
        skill_with_page._page.goto.assert_called_once()
        skill_with_page._page.click.assert_called_once()

    def test_navigate_and_fill(self, skill_with_page):
        """'Go to google.com and search for X'."""
        result = skill_with_page.execute(
            action="fill", url="google.com", selector="#search", value="python"
        )
        assert result.success
        skill_with_page._page.goto.assert_called_once()
        skill_with_page._page.fill.assert_called_once()

    def test_navigate_and_run_script(self, skill_with_page):
        """Run JS on a page by URL."""
        skill_with_page._page.evaluate.return_value = 42
        result = skill_with_page.execute(
            action="run_script", url="example.com", script="return 42"
        )
        assert result.success
        assert "42" in result.message

    def test_open_page_shows_content(self, skill_with_page):
        """open_page should return page title and text."""
        result = skill_with_page.execute(action="open_page", url="apple.com")
        assert result.success
        assert "Apple" in result.message

    def test_url_gets_https_prefix(self, skill_with_page):
        """URLs without http prefix should get https:// added."""
        skill_with_page.execute(action="screenshot_page", url="apple.com")
        call_args = skill_with_page._page.goto.call_args
        assert call_args[0][0] == "https://apple.com"

    def test_url_with_https_unchanged(self, skill_with_page):
        """URLs with https:// should not be double-prefixed."""
        skill_with_page.execute(action="screenshot_page", url="https://apple.com")
        call_args = skill_with_page._page.goto.call_args
        assert call_args[0][0] == "https://apple.com"

    def test_sequential_pages(self, skill_with_page):
        """Multiple page navigations should work (browser reuse)."""
        r1 = skill_with_page.execute(action="screenshot_page", url="apple.com")
        assert r1.success

        # Simulate that we're now on apple.com after first navigation
        skill_with_page._page.url = "https://apple.com"
        skill_with_page._page.title.return_value = "Google"
        r2 = skill_with_page.execute(action="screenshot_page", url="google.com")
        assert r2.success
        assert "Google" in r2.message


class TestBrowserSafety:
    """Safety risk classification tests."""

    def test_browser_risk_map_exists(self):
        from winston.core.safety import SKILL_RISK_MAP, RiskLevel
        assert "browser" in SKILL_RISK_MAP

    def test_open_page_is_low(self):
        from winston.core.safety import SKILL_RISK_MAP, RiskLevel
        assert SKILL_RISK_MAP["browser"]["open_page"] == RiskLevel.LOW

    def test_click_is_medium(self):
        from winston.core.safety import SKILL_RISK_MAP, RiskLevel
        assert SKILL_RISK_MAP["browser"]["click"] == RiskLevel.MEDIUM

    def test_run_script_is_high(self):
        from winston.core.safety import SKILL_RISK_MAP, RiskLevel
        assert SKILL_RISK_MAP["browser"]["run_script"] == RiskLevel.HIGH

    def test_screenshot_is_low(self):
        from winston.core.safety import SKILL_RISK_MAP, RiskLevel
        assert SKILL_RISK_MAP["browser"]["screenshot_page"] == RiskLevel.LOW
