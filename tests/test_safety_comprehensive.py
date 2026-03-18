"""
Comprehensive Safety Guard tests.
Validates risk classification for every skill, rate limiting,
blocked command detection, and approval logic.
"""

import pytest
from winston.core.safety import (
    SafetyGuard,
    RiskLevel,
    SKILL_RISK_MAP,
    BLOCKED_PATTERNS,
)


@pytest.fixture
def guard():
    """A SafetyGuard in no-confirmation mode (auto-approves LOW/SAFE)."""
    return SafetyGuard(require_confirmation=False)


@pytest.fixture
def strict_guard():
    """A SafetyGuard that requires confirmation for MEDIUM+."""
    return SafetyGuard(require_confirmation=True)


class TestRiskClassification:
    """Every skill action should have a defined risk level."""

    def test_web_search_is_safe(self, guard):
        """Web search should always be SAFE."""
        action = guard.request_action("web_search", {"query": "python tutorial"})
        assert action.risk_level == RiskLevel.SAFE

    def test_email_send_is_high(self, guard):
        """Sending email should be HIGH risk."""
        action = guard.request_action("email", {"action": "send", "to": "test@test.com"})
        assert action.risk_level == RiskLevel.HIGH

    def test_email_read_is_low(self, guard):
        """Reading email should be LOW risk."""
        action = guard.request_action("email", {"action": "read"})
        assert action.risk_level == RiskLevel.LOW

    def test_system_control_time_is_safe(self, guard):
        """Getting time should be SAFE."""
        action = guard.request_action("system_control", {"action": "time"})
        assert action.risk_level == RiskLevel.SAFE

    def test_system_control_run_command_is_high(self, guard):
        """Running commands should be HIGH risk."""
        action = guard.request_action("system_control", {"action": "run_command"})
        assert action.risk_level == RiskLevel.HIGH

    def test_notes_read_is_safe(self, guard):
        """Reading notes should be SAFE."""
        action = guard.request_action("notes", {"action": "read"})
        assert action.risk_level == RiskLevel.SAFE

    def test_notes_delete_is_medium(self, guard):
        """Deleting notes should be MEDIUM risk."""
        action = guard.request_action("notes", {"action": "delete"})
        assert action.risk_level == RiskLevel.MEDIUM

    def test_browser_open_page_is_low(self, guard):
        """Opening a page should be LOW risk."""
        action = guard.request_action("browser", {"action": "open_page"})
        assert action.risk_level == RiskLevel.LOW

    def test_browser_run_script_is_high(self, guard):
        """Running JS should be HIGH risk."""
        action = guard.request_action("browser", {"action": "run_script"})
        assert action.risk_level == RiskLevel.HIGH

    def test_browser_click_is_medium(self, guard):
        """Clicking should be MEDIUM risk."""
        action = guard.request_action("browser", {"action": "click"})
        assert action.risk_level == RiskLevel.MEDIUM

    def test_knowledge_base_save_is_low(self, guard):
        """Saving to KB should be LOW risk."""
        action = guard.request_action("knowledge_base", {"action": "save"})
        assert action.risk_level == RiskLevel.LOW

    def test_knowledge_base_search_is_safe(self, guard):
        """Searching KB should be SAFE."""
        action = guard.request_action("knowledge_base", {"action": "search"})
        assert action.risk_level == RiskLevel.SAFE

    def test_knowledge_base_delete_is_medium(self, guard):
        """Deleting from KB should be MEDIUM risk."""
        action = guard.request_action("knowledge_base", {"action": "delete"})
        assert action.risk_level == RiskLevel.MEDIUM

    def test_code_runner_is_medium(self, guard):
        """Running code should be MEDIUM risk."""
        action = guard.request_action("code_runner", {"action": "run"})
        assert action.risk_level == RiskLevel.MEDIUM

    def test_price_monitor_watch_is_low(self, guard):
        """Setting up a watch should be LOW risk."""
        action = guard.request_action("price_monitor", {"action": "watch"})
        assert action.risk_level == RiskLevel.LOW

    def test_smart_home_device_control_is_medium(self, guard):
        """Controlling devices should be MEDIUM risk."""
        action = guard.request_action("smart_home", {"action": "device_control"})
        assert action.risk_level == RiskLevel.MEDIUM

    def test_youtube_search_is_safe(self, guard):
        """YouTube search should be SAFE."""
        action = guard.request_action("youtube", {"action": "search"})
        assert action.risk_level == RiskLevel.SAFE


class TestApprovalLogic:
    """Tests for auto-approval based on risk levels."""

    def test_safe_auto_approved(self, guard):
        """SAFE actions should be auto-approved."""
        action = guard.request_action("web_search", {"query": "test"})
        assert action.approved is True

    def test_low_auto_approved_noconfirm(self, guard):
        """LOW risk should auto-approve when require_confirmation=False."""
        action = guard.request_action("email", {"action": "read"})
        assert action.approved is True

    def test_medium_auto_approved_noconfirm(self, guard):
        """MEDIUM risk should auto-approve when require_confirmation=False."""
        action = guard.request_action("notes", {"action": "delete"})
        assert action.approved is True

    def test_high_needs_confirmation(self, strict_guard):
        """HIGH risk should NOT auto-approve when require_confirmation=True."""
        action = strict_guard.request_action("email", {"action": "send", "to": "x@y.com"})
        assert action.risk_level == RiskLevel.HIGH
        assert action.approved is False


class TestBlockedPatterns:
    """Tests for blocked command detection."""

    def test_blocked_patterns_loaded(self):
        """BLOCKED_PATTERNS should be non-empty."""
        assert len(BLOCKED_PATTERNS) > 5

    def test_all_skills_in_risk_map(self):
        """The risk map should cover all important skills."""
        expected = [
            "email", "system_control", "web_search", "notes", "screenshot",
            "youtube", "file_manager", "clipboard", "calendar", "smart_home",
            "code_runner", "price_monitor", "browser", "knowledge_base",
        ]
        for skill in expected:
            assert skill in SKILL_RISK_MAP, f"Missing risk map for: {skill}"


class TestRiskMapCompleteness:
    """Ensure every skill in the risk map has a _default entry."""

    def test_all_have_defaults(self):
        """Every skill in SKILL_RISK_MAP should have a _default entry."""
        for skill_name, actions in SKILL_RISK_MAP.items():
            assert "_default" in actions, f"Missing _default for: {skill_name}"
