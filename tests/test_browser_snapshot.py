"""
Tests for the Browser Snapshot + Ref system.
Tests ARIA snapshot parsing, ref resolution, new ref-based actions,
error translation, and backward compatibility.
"""

import pytest
from unittest.mock import MagicMock, PropertyMock, patch
from pathlib import Path
from winston.skills.browser_skill import BrowserSkill


# ── Sample ARIA snapshots for testing ──

SIMPLE_SNAPSHOT = """\
- navigation "Main":
  - link "Home"
  - link "Products"
  - link "About"
- heading "Welcome" [level=1]
- textbox "Search"
- button "Submit"
- paragraph: Some descriptive text here
- img "Hero banner"
- list:
  - listitem:
    - link "Item 1"
  - listitem:
    - link "Item 2"\
"""

DUPLICATE_SNAPSHOT = """\
- list "Products":
  - button "Add to cart"
  - button "Add to cart"
  - button "Add to cart"
- link "Home"
- link "Home"\
"""

MINIMAL_SNAPSHOT = """\
- heading "Title" [level=1]
- paragraph: Just text, no interactive elements\
"""

COMBOBOX_SNAPSHOT = """\
- combobox "Nationality":
  - option "Select Nationality" [selected]
  - option "Afghanistan"
  - option "Aland Islands"
  - option "Albania"
  - option "Algeria"
  - option "American Samoa"
  - option "Andorra"
  - option "Angola"
  - option "Antigua and Barbuda"
  - option "Argentina"
  - option "Armenia"
  - option "Australia"
  - option "Austria"
  - option "Azerbaijan"
  - option "Bahamas"
  - option "Bahrain"
  - option "Bangladesh"
  - option "Barbados"
  - option "Belarus"
  - option "Belgium"\
"""

COMPACT_SNAPSHOT = """\
- document:
  - navigation "Main":
    - link "Home"
  - group:
    - rowgroup:
      - row:
        - button "Submit"
    - listitem:
      - link "Details"
    - separator
    - paragraph: Important text here
    - generic:
      - textbox "Email"
    - presentation:
      - img "Logo"
  - banner "Top":
    - link "Brand"
  - main:
    - heading "Content" [level=1]
  - contentinfo "Footer":
    - link "Privacy"\
"""

URL_SNAPSHOT = """\
- navigation "Nav":
  - link "Home" /url: https://example.com/
  - listitem:
    - link "About" /url: https://example.com/about\
"""


@pytest.fixture
def browser_skill():
    """A fresh BrowserSkill instance."""
    return BrowserSkill()


@pytest.fixture
def skill_with_page(browser_skill, tmp_path):
    """Browser skill with a fully mocked sync Playwright page."""
    mock_page = MagicMock()
    mock_page.title.return_value = "Test Page"
    mock_page.url = "https://example.com"
    mock_page.evaluate.return_value = "Page content"
    mock_page.goto.return_value = None
    mock_page.query_selector_all.return_value = []

    # Make get_by_role/get_by_text return a locator with count=0 (no cookie banners)
    empty_locator = MagicMock()
    empty_locator.count.return_value = 0
    mock_page.get_by_role.return_value = empty_locator
    mock_page.get_by_text.return_value = empty_locator

    browser_skill._page = mock_page
    browser_skill._browser = MagicMock()
    browser_skill._playwright = MagicMock()
    browser_skill._screenshots_dir = tmp_path
    return browser_skill


# ── Parsing Tests ──

class TestAriaSnapshotParsing:
    """Test _parse_aria_snapshot correctly assigns refs to interactive elements."""

    def test_interactive_elements_get_refs(self, browser_skill):
        enhanced, refs = browser_skill._parse_aria_snapshot(SIMPLE_SNAPSHOT)

        # All interactive elements should have refs
        assert "[ref=" in enhanced
        # links: Home, Products, About, Item 1, Item 2 (5)
        # textbox: Search (1)
        # button: Submit (1)
        # heading with name: Welcome (1) — content role with name
        # img with name: Hero banner (1) — content role with name
        # cell is NOT a content role (too noisy on real pages)
        assert len(refs) == 9

    def test_structural_elements_skipped(self, browser_skill):
        enhanced, refs = browser_skill._parse_aria_snapshot(SIMPLE_SNAPSHOT)

        # paragraph, navigation, list, listitem should NOT get refs
        for ref_info in refs.values():
            assert ref_info["role"] not in ("paragraph", "navigation", "list", "listitem")

    def test_content_roles_need_name(self, browser_skill):
        """heading and img only get refs when they have a name."""
        enhanced, refs = browser_skill._parse_aria_snapshot(SIMPLE_SNAPSHOT)

        # heading "Welcome" should get a ref
        heading_refs = [r for r in refs.values() if r["role"] == "heading"]
        assert len(heading_refs) == 1
        assert heading_refs[0]["name"] == "Welcome"

        # img "Hero banner" should get a ref
        img_refs = [r for r in refs.values() if r["role"] == "img"]
        assert len(img_refs) == 1
        assert img_refs[0]["name"] == "Hero banner"

    def test_duplicate_elements_get_nth(self, browser_skill):
        """Duplicate role+name pairs should get nth indices."""
        enhanced, refs = browser_skill._parse_aria_snapshot(DUPLICATE_SNAPSHOT)

        # 3x "Add to cart" buttons + 2x "Home" links = 5 refs
        button_refs = [r for r in refs.values() if r["role"] == "button" and r.get("name") == "Add to cart"]
        assert len(button_refs) == 3
        assert button_refs[0]["nth"] == 0
        assert button_refs[1]["nth"] == 1
        assert button_refs[2]["nth"] == 2

        link_refs = [r for r in refs.values() if r["role"] == "link" and r.get("name") == "Home"]
        assert len(link_refs) == 2
        assert link_refs[0]["nth"] == 0
        assert link_refs[1]["nth"] == 1

    def test_unique_elements_no_nth(self, browser_skill):
        """Unique role+name pairs should NOT have nth."""
        enhanced, refs = browser_skill._parse_aria_snapshot(SIMPLE_SNAPSHOT)

        submit_refs = [r for r in refs.values() if r["role"] == "button" and r.get("name") == "Submit"]
        assert len(submit_refs) == 1
        assert "nth" not in submit_refs[0]

    def test_ref_ids_sequential(self, browser_skill):
        """Ref IDs should be sequential: e0, e1, e2, ..."""
        enhanced, refs = browser_skill._parse_aria_snapshot(SIMPLE_SNAPSHOT)

        ref_ids = sorted(refs.keys(), key=lambda x: int(x[1:]))
        for i, ref_id in enumerate(ref_ids):
            assert ref_id == f"e{i}"

    def test_enhanced_text_contains_refs(self, browser_skill):
        """Enhanced text should contain [ref=eN] markers inline."""
        enhanced, refs = browser_skill._parse_aria_snapshot(SIMPLE_SNAPSHOT)

        for ref_id in refs:
            assert f"[ref={ref_id}]" in enhanced

    def test_minimal_snapshot_content_role_only(self, browser_skill):
        """A page with only a heading and paragraph — only heading gets a ref."""
        enhanced, refs = browser_skill._parse_aria_snapshot(MINIMAL_SNAPSHOT)
        assert len(refs) == 1
        assert list(refs.values())[0]["role"] == "heading"

    def test_empty_snapshot(self, browser_skill):
        """Empty input produces no refs."""
        enhanced, refs = browser_skill._parse_aria_snapshot("")
        assert len(refs) == 0
        assert enhanced == ""


# ── Options Collapse Tests ──

class TestOptionsCollapse:
    """Test that combobox/listbox options are collapsed into summaries."""

    def test_options_collapsed_in_combobox(self, browser_skill):
        """Combobox with 20 options → only first 5 shown inline + count."""
        enhanced, refs = browser_skill._parse_aria_snapshot(COMBOBOX_SNAPSHOT)

        # The combobox itself should get a ref
        combobox_refs = [r for r in refs.values() if r["role"] == "combobox"]
        assert len(combobox_refs) == 1

        # No option refs should exist
        option_refs = [r for r in refs.values() if r["role"] == "option"]
        assert len(option_refs) == 0

        # Should show first 5 options inline
        assert "Select Nationality" in enhanced
        assert "Afghanistan" in enhanced
        assert "Aland Islands" in enhanced
        assert "Albania" in enhanced
        assert "Algeria" in enhanced

        # Should show remaining count
        assert "(+15 more)" in enhanced

        # The 6th option should NOT appear as its own line
        assert "\n" not in enhanced or "American Samoa" not in enhanced.split("[options:")[0]

    def test_options_not_individually_reffed(self, browser_skill):
        """No option elements should get individual refs."""
        enhanced, refs = browser_skill._parse_aria_snapshot(COMBOBOX_SNAPSHOT)

        for ref_info in refs.values():
            assert ref_info["role"] != "option"

    def test_combobox_with_few_options(self, browser_skill):
        """Combobox with <= 5 options shows all without +N more."""
        snapshot = """\
- combobox "Color":
  - option "Red"
  - option "Blue"
  - option "Green"\
"""
        enhanced, refs = browser_skill._parse_aria_snapshot(snapshot)

        assert "[options: Red, Blue, Green]" in enhanced
        assert "more)" not in enhanced

    def test_listbox_options_also_collapsed(self, browser_skill):
        """Listbox gets the same collapse treatment as combobox."""
        snapshot = """\
- listbox "Items":
  - option "Apple"
  - option "Banana"
  - option "Cherry"
  - option "Date"
  - option "Elderberry"
  - option "Fig"
  - option "Grape"\
"""
        enhanced, refs = browser_skill._parse_aria_snapshot(snapshot)

        listbox_refs = [r for r in refs.values() if r["role"] == "listbox"]
        assert len(listbox_refs) == 1
        assert "[options:" in enhanced
        assert "(+2 more)" in enhanced

    def test_options_summary_on_ref_line(self, browser_skill):
        """The [options: ...] summary appears on the same line as [ref=eN]."""
        enhanced, refs = browser_skill._parse_aria_snapshot(COMBOBOX_SNAPSHOT)

        for line in enhanced.split("\n"):
            if "[ref=" in line and "combobox" in line:
                assert "[options:" in line


# ── Compact Mode Tests ──

class TestCompactMode:
    """Test that structural noise roles are removed in compact mode."""

    def test_compact_removes_noise(self, browser_skill):
        """document, rowgroup, row, listitem, group, separator, generic, presentation are removed."""
        enhanced, refs = browser_skill._parse_aria_snapshot(COMPACT_SNAPSHOT)

        lines = enhanced.split("\n")
        line_texts = [l.strip() for l in lines]

        # These noise-only lines should be gone
        assert not any(l.startswith("- document") for l in line_texts)
        assert not any(l.startswith("- rowgroup") for l in line_texts)
        assert not any(l.startswith("- row:") for l in line_texts)
        assert not any(l.startswith("- listitem:") for l in line_texts)
        assert not any(l.startswith("- group:") for l in line_texts)
        assert not any(l.startswith("- separator") for l in line_texts)
        assert not any(l.startswith("- generic:") for l in line_texts)
        assert not any(l.startswith("- presentation:") for l in line_texts)

    def test_compact_keeps_landmarks(self, browser_skill):
        """navigation, banner, main, contentinfo are kept (landmarks help LLM orient)."""
        enhanced, refs = browser_skill._parse_aria_snapshot(COMPACT_SNAPSHOT)

        assert 'navigation "Main"' in enhanced
        assert 'banner "Top"' in enhanced
        assert "main:" in enhanced or 'main:' in enhanced
        assert 'contentinfo "Footer"' in enhanced

    def test_compact_keeps_text_lines(self, browser_skill):
        """Lines with text content after : are kept even for noise roles."""
        enhanced, refs = browser_skill._parse_aria_snapshot(COMPACT_SNAPSHOT)

        assert "Important text here" in enhanced

    def test_compact_keeps_url_lines(self, browser_skill):
        """Lines with /url: are always kept."""
        enhanced, refs = browser_skill._parse_aria_snapshot(URL_SNAPSHOT)

        assert "/url: https://example.com/" in enhanced
        assert "/url: https://example.com/about" in enhanced

    def test_compact_keeps_named_elements(self, browser_skill):
        """Named elements (even if in noise roles) are kept."""
        snapshot = '- group "Settings":\n  - button "Save"'
        enhanced, refs = browser_skill._parse_aria_snapshot(snapshot)

        assert 'group "Settings"' in enhanced

    def test_compact_keeps_ref_lines(self, browser_skill):
        """All lines with [ref=] markers are always kept."""
        enhanced, refs = browser_skill._parse_aria_snapshot(COMPACT_SNAPSHOT)

        # Every ref should appear in the output
        for ref_id in refs:
            assert f"[ref={ref_id}]" in enhanced


# ── Ref Resolution Tests ──

class TestRefResolution:
    """Test _resolve_ref returns correct Playwright locators."""

    def test_resolve_simple_ref(self, skill_with_page):
        """Resolving a ref with role+name calls get_by_role correctly."""
        skill_with_page._refs = {"e0": {"role": "button", "name": "Submit"}}

        mock_locator = MagicMock()
        skill_with_page._page.get_by_role.return_value = mock_locator

        result = skill_with_page._resolve_ref("e0")

        skill_with_page._page.get_by_role.assert_called_with("button", name="Submit", exact=True)
        assert result == mock_locator

    def test_resolve_ref_with_nth(self, skill_with_page):
        """Resolving a duplicate ref calls .nth() on the locator."""
        skill_with_page._refs = {"e1": {"role": "button", "name": "Add to cart", "nth": 2}}

        mock_locator = MagicMock()
        mock_nth_locator = MagicMock()
        mock_locator.nth.return_value = mock_nth_locator
        skill_with_page._page.get_by_role.return_value = mock_locator

        result = skill_with_page._resolve_ref("e1")

        skill_with_page._page.get_by_role.assert_called_with("button", name="Add to cart", exact=True)
        mock_locator.nth.assert_called_with(2)
        assert result == mock_nth_locator

    def test_resolve_ref_no_name(self, skill_with_page):
        """Resolving a ref without a name doesn't pass name kwarg."""
        skill_with_page._refs = {"e0": {"role": "textbox", "name": None}}

        mock_locator = MagicMock()
        skill_with_page._page.get_by_role.return_value = mock_locator

        result = skill_with_page._resolve_ref("e0")

        skill_with_page._page.get_by_role.assert_called_with("textbox")
        assert result == mock_locator

    def test_resolve_unknown_ref_raises(self, skill_with_page):
        """Resolving an unknown ref raises ValueError."""
        skill_with_page._refs = {}

        with pytest.raises(ValueError, match="Unknown ref"):
            skill_with_page._resolve_ref("e99")


# ── Snapshot Method Tests ──

class TestSnapshotMethod:
    """Test the snapshot() method integration."""

    def test_snapshot_returns_enhanced_text(self, skill_with_page):
        skill_with_page._page.locator.return_value.aria_snapshot.return_value = SIMPLE_SNAPSHOT

        result = skill_with_page.snapshot()

        assert "Page: Test Page" in result
        assert "URL: https://example.com" in result
        assert "[ref=" in result
        assert len(skill_with_page._refs) > 0

    def test_snapshot_truncates_long_output(self, skill_with_page):
        skill_with_page._page.locator.return_value.aria_snapshot.return_value = SIMPLE_SNAPSHOT

        result = skill_with_page.snapshot(max_chars=100)

        assert len(result) <= 120  # 100 + "... (truncated)" suffix
        assert "truncated" in result

    def test_snapshot_no_page(self, browser_skill):
        result = browser_skill.snapshot()
        assert result == "No page is open."

    def test_snapshot_falls_back_on_error(self, skill_with_page):
        """If aria_snapshot fails, falls back to extract_page_structure."""
        skill_with_page._page.locator.side_effect = Exception("Not supported")
        skill_with_page._page.evaluate.return_value = {
            "links": [{"text": "Home", "href": "/", "selector": "a"}],
            "buttons": [], "inputs": [], "selects": []
        }

        result = skill_with_page.snapshot()
        # Should fall back gracefully (extract_page_structure returns a string)
        assert isinstance(result, str)


# ── Action Tests ──

class TestRefActions:
    """Test the new ref-based action handlers."""

    def test_click_ref_success(self, skill_with_page):
        skill_with_page._refs = {"e0": {"role": "button", "name": "Submit"}}
        mock_locator = MagicMock()
        skill_with_page._page.get_by_role.return_value = mock_locator

        result = skill_with_page.execute(action="click_ref", ref="e0")

        assert result.success
        assert "e0" in result.message
        mock_locator.click.assert_called_once_with(timeout=8000)

    def test_click_ref_no_ref(self, skill_with_page):
        result = skill_with_page.execute(action="click_ref")
        assert not result.success
        assert "No ref" in result.message

    def test_click_ref_unknown_ref(self, skill_with_page):
        skill_with_page._refs = {}
        result = skill_with_page.execute(action="click_ref", ref="e99")
        assert not result.success
        assert "Unknown ref" in result.message

    def test_type_ref_success(self, skill_with_page):
        skill_with_page._refs = {"e3": {"role": "textbox", "name": "Search"}}
        mock_locator = MagicMock()
        skill_with_page._page.get_by_role.return_value = mock_locator

        result = skill_with_page.execute(action="type_ref", ref="e3", value="hello")

        assert result.success
        mock_locator.fill.assert_called_once_with("hello", timeout=8000)
        mock_locator.press.assert_not_called()

    def test_type_ref_with_submit(self, skill_with_page):
        skill_with_page._refs = {"e3": {"role": "textbox", "name": "Search"}}
        mock_locator = MagicMock()
        skill_with_page._page.get_by_role.return_value = mock_locator

        result = skill_with_page.execute(action="type_ref", ref="e3", value="hello", submit=True)

        assert result.success
        assert "submitted" in result.message
        mock_locator.fill.assert_called_once()
        mock_locator.press.assert_called_once_with("Enter", timeout=5000)

    def test_type_ref_no_value(self, skill_with_page):
        result = skill_with_page.execute(action="type_ref", ref="e3")
        assert not result.success
        assert "No value" in result.message

    def test_select_ref_success(self, skill_with_page):
        skill_with_page._refs = {"e7": {"role": "combobox", "name": "Color"}}
        mock_locator = MagicMock()
        skill_with_page._page.get_by_role.return_value = mock_locator

        result = skill_with_page.execute(action="select_ref", ref="e7", value="Blue")

        assert result.success
        mock_locator.select_option.assert_called_once_with(label="Blue", timeout=8000)

    def test_select_ref_no_value(self, skill_with_page):
        result = skill_with_page.execute(action="select_ref", ref="e7")
        assert not result.success
        assert "No value" in result.message

    def test_hover_ref_success(self, skill_with_page):
        skill_with_page._refs = {"e2": {"role": "link", "name": "Menu"}}
        mock_locator = MagicMock()
        skill_with_page._page.get_by_role.return_value = mock_locator

        result = skill_with_page.execute(action="hover_ref", ref="e2")

        assert result.success
        mock_locator.hover.assert_called_once_with(timeout=8000)

    def test_hover_ref_no_ref(self, skill_with_page):
        result = skill_with_page.execute(action="hover_ref")
        assert not result.success
        assert "No ref" in result.message

    def test_scroll_down(self, skill_with_page):
        result = skill_with_page.execute(action="scroll", direction="down")
        assert result.success
        assert "down" in result.message
        skill_with_page._page.evaluate.assert_called_with("window.scrollBy(0, 600)")

    def test_scroll_up(self, skill_with_page):
        result = skill_with_page.execute(action="scroll", direction="up")
        assert result.success
        assert "up" in result.message
        skill_with_page._page.evaluate.assert_called_with("window.scrollBy(0, -600)")

    def test_snapshot_action(self, skill_with_page):
        skill_with_page._page.locator.return_value.aria_snapshot.return_value = SIMPLE_SNAPSHOT

        result = skill_with_page.execute(action="snapshot")

        assert result.success
        assert "[ref=" in result.message
        assert result.data["snapshot"] is not None


# ── Error Translation Tests ──

class TestErrorTranslation:
    """Test _to_ai_error translates Playwright errors to actionable messages."""

    def test_strict_mode_violation(self, browser_skill):
        err = Exception("Error: strict mode violation: get_by_role('button') resolved to 3 elements")
        msg = browser_skill._to_ai_error(err)
        assert "Multiple elements" in msg
        assert "snapshot" in msg.lower()

    def test_timeout_error(self, browser_skill):
        err = Exception("Timeout 8000ms exceeded")
        msg = browser_skill._to_ai_error(err)
        assert "not found or hidden" in msg

    def test_not_visible_error(self, browser_skill):
        err = Exception("Element is not visible")
        msg = browser_skill._to_ai_error(err)
        assert "not found or hidden" in msg

    def test_intercepts_pointer(self, browser_skill):
        err = Exception("Element intercepts pointer events")
        msg = browser_skill._to_ai_error(err)
        assert "overlay" in msg.lower()

    def test_detached_error(self, browser_skill):
        err = Exception("Element is detached from DOM")
        msg = browser_skill._to_ai_error(err)
        assert "gone" in msg.lower()

    def test_generic_error_with_ref(self, browser_skill):
        err = Exception("Something unexpected happened")
        msg = browser_skill._to_ai_error(err, ref="e5")
        assert "e5" in msg

    def test_generic_error_without_ref(self, browser_skill):
        err = Exception("Something unexpected happened")
        msg = browser_skill._to_ai_error(err)
        assert "Action failed" in msg


# ── Safety Risk Map Tests ──

class TestSnapshotSafetyRisks:
    """Test that new actions have correct risk levels in SKILL_RISK_MAP."""

    def test_snapshot_is_low(self):
        from winston.core.safety import SKILL_RISK_MAP, RiskLevel
        assert SKILL_RISK_MAP["browser"]["snapshot"] == RiskLevel.LOW

    def test_click_ref_is_medium(self):
        from winston.core.safety import SKILL_RISK_MAP, RiskLevel
        assert SKILL_RISK_MAP["browser"]["click_ref"] == RiskLevel.MEDIUM

    def test_type_ref_is_medium(self):
        from winston.core.safety import SKILL_RISK_MAP, RiskLevel
        assert SKILL_RISK_MAP["browser"]["type_ref"] == RiskLevel.MEDIUM

    def test_select_ref_is_medium(self):
        from winston.core.safety import SKILL_RISK_MAP, RiskLevel
        assert SKILL_RISK_MAP["browser"]["select_ref"] == RiskLevel.MEDIUM

    def test_hover_ref_is_low(self):
        from winston.core.safety import SKILL_RISK_MAP, RiskLevel
        assert SKILL_RISK_MAP["browser"]["hover_ref"] == RiskLevel.LOW

    def test_scroll_is_low(self):
        from winston.core.safety import SKILL_RISK_MAP, RiskLevel
        assert SKILL_RISK_MAP["browser"]["scroll"] == RiskLevel.LOW


# ── Backward Compatibility Tests ──

class TestBackwardCompatibility:
    """Ensure old actions still work alongside new ref-based ones."""

    def test_old_click_still_works(self, skill_with_page):
        result = skill_with_page.execute(action="click", selector="#buy-btn")
        assert result.success

    def test_old_click_text_still_works(self, skill_with_page):
        # Make get_by_role return a visible element
        mock_locator = MagicMock()
        mock_locator.count.return_value = 1
        mock_locator.first.is_visible.return_value = True
        skill_with_page._page.get_by_role.return_value = mock_locator

        result = skill_with_page.execute(action="click_text", text="Buy Now")
        assert result.success

    def test_old_fill_still_works(self, skill_with_page):
        result = skill_with_page.execute(action="fill", selector="#search", value="test")
        assert result.success

    def test_old_select_option_still_works(self, skill_with_page):
        result = skill_with_page.execute(action="select_option", selector="select#color", value="Red")
        assert result.success

    def test_old_extract_text_still_works(self, skill_with_page):
        result = skill_with_page.execute(action="extract_text")
        assert result.success

    def test_old_get_page_structure_still_works(self, skill_with_page):
        skill_with_page._page.evaluate.return_value = {
            "links": [], "buttons": [], "inputs": [], "selects": []
        }
        result = skill_with_page.execute(action="get_page_structure")
        assert result.success

    def test_old_screenshot_still_works(self, skill_with_page):
        def fake_screenshot(path=None, full_page=False):
            if path:
                Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        skill_with_page._page.screenshot.side_effect = fake_screenshot

        result = skill_with_page.execute(action="screenshot_page")
        assert result.success

    def test_refs_reset_on_new_snapshot(self, skill_with_page):
        """Taking a new snapshot should replace old refs."""
        skill_with_page._refs = {"e0": {"role": "button", "name": "Old"}}
        skill_with_page._page.locator.return_value.aria_snapshot.return_value = SIMPLE_SNAPSHOT

        skill_with_page.snapshot()

        # Old ref should be gone, new refs should exist
        assert "e0" in skill_with_page._refs  # e0 still exists but it's a new mapping
        assert skill_with_page._refs["e0"]["name"] != "Old"
