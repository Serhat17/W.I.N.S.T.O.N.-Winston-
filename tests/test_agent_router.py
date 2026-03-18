"""
Tests for the Multi-Agent Router system.
Validates agent creation, switching, skill filtering, and channel auto-detection.
"""

import pytest
from unittest.mock import MagicMock
from winston.core.agent_router import AgentRouter, AgentProfile, BUILTIN_AGENTS


class TestAgentProfile:
    """Tests for AgentProfile dataclass."""

    def test_default_profile_allows_all_skills(self):
        """An agent with no whitelist or blacklist allows all skills."""
        agent = AgentProfile(name="test")
        all_skills = {"a": MagicMock(), "b": MagicMock(), "c": MagicMock()}
        filtered = agent.filter_skills(all_skills)
        assert set(filtered.keys()) == {"a", "b", "c"}

    def test_whitelist_filters_skills(self):
        """allowed_skills whitelist should only keep listed skills."""
        agent = AgentProfile(name="test", allowed_skills=["a", "c"])
        all_skills = {"a": MagicMock(), "b": MagicMock(), "c": MagicMock()}
        filtered = agent.filter_skills(all_skills)
        assert set(filtered.keys()) == {"a", "c"}

    def test_blacklist_filters_skills(self):
        """blocked_skills should remove listed skills."""
        agent = AgentProfile(name="test", blocked_skills=["b"])
        all_skills = {"a": MagicMock(), "b": MagicMock(), "c": MagicMock()}
        filtered = agent.filter_skills(all_skills)
        assert set(filtered.keys()) == {"a", "c"}

    def test_whitelist_takes_priority_over_blacklist(self):
        """If allowed_skills is set, blocked_skills is ignored."""
        agent = AgentProfile(
            name="test",
            allowed_skills=["a"],
            blocked_skills=["c"],
        )
        all_skills = {"a": MagicMock(), "b": MagicMock(), "c": MagicMock()}
        filtered = agent.filter_skills(all_skills)
        # Whitelist mode — only "a" passes, "c" is not in whitelist
        assert set(filtered.keys()) == {"a"}


class TestAgentRouter:
    """Tests for AgentRouter routing logic."""

    def test_builtin_agents_exist(self):
        """home, work, coding should be available by default."""
        router = AgentRouter()
        names = [a["name"] for a in router.list_agents()]
        assert "home" in names
        assert "work" in names
        assert "coding" in names

    def test_default_agent_is_home(self):
        """Default active agent should be 'home'."""
        router = AgentRouter()
        assert router.active_agent_name == "home"

    def test_switch_agent(self):
        """Switching agents should change the active profile."""
        router = AgentRouter()
        result = router.switch_agent("work")
        assert result is not None
        assert result.name == "work"
        assert router.active_agent_name == "work"

    def test_switch_invalid_agent_returns_none(self):
        """Switching to a non-existent agent should return None."""
        router = AgentRouter()
        result = router.switch_agent("nonexistent")
        assert result is None
        assert router.active_agent_name == "home"

    def test_session_agent_override(self):
        """Session-specific agent overrides the global default."""
        router = AgentRouter()
        router.switch_agent("work", session_id="session_123")

        # Global is still home
        agent_default = router.get_agent()
        assert agent_default.name == "home"

        # But session_123 gets work
        agent_session = router.get_agent(session_id="session_123")
        assert agent_session.name == "work"

    def test_channel_auto_detection(self):
        """Telegram channel should auto-map to the home agent."""
        router = AgentRouter()
        agent = router.get_agent(channel="telegram")
        assert agent.name == "home"

    def test_detect_switch_command(self):
        """Natural language switch commands should be detected."""
        router = AgentRouter()
        assert router.detect_switch_command("switch to work mode") == "work"
        assert router.detect_switch_command("use coding mode") == "coding"
        assert router.detect_switch_command("home mode") == "home"
        assert router.detect_switch_command("hello there") is None

    def test_work_agent_blocks_youtube(self):
        """Work agent should block youtube skill."""
        router = AgentRouter()
        work = router.get_agent()
        router.switch_agent("work")
        work = router.get_agent()

        all_skills = {
            "web_search": MagicMock(),
            "youtube": MagicMock(),
            "email": MagicMock(),
            "smart_home": MagicMock(),
        }

        filtered = work.filter_skills(all_skills)
        assert "youtube" not in filtered
        assert "smart_home" not in filtered
        assert "web_search" in filtered
        assert "email" in filtered

    def test_coding_agent_whitelist(self):
        """Coding agent should only allow specific skills."""
        router = AgentRouter()
        router.switch_agent("coding")
        coding = router.get_agent()

        all_skills = {
            "web_search": MagicMock(),
            "code_runner": MagicMock(),
            "youtube": MagicMock(),
            "email": MagicMock(),
        }

        filtered = coding.filter_skills(all_skills)
        assert "web_search" in filtered
        assert "code_runner" in filtered
        assert "youtube" not in filtered
        assert "email" not in filtered
