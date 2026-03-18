"""
Shared fixtures for W.I.N.S.T.O.N. test suite.
"""

import pytest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

from winston.skills.base import BaseSkill, SkillResult
from winston.skills.web_search import WebSearchSkill
from winston.skills.web_fetch_skill import WebFetchSkill
from winston.core.safety import SafetyGuard, RiskLevel, ActionRequest


@pytest.fixture
def web_search_skill():
    """A fresh WebSearchSkill instance."""
    return WebSearchSkill()


@pytest.fixture
def web_fetch_skill():
    """A fresh WebFetchSkill instance."""
    return WebFetchSkill()


@pytest.fixture
def mock_brain():
    """A mocked Brain that returns canned responses."""
    brain = MagicMock()
    brain.think.return_value = "I'll help you with that."
    brain.parse_skill_calls.return_value = []
    brain.strip_skill_blocks.side_effect = lambda x: x
    brain.register_skills = MagicMock()
    return brain


@pytest.fixture
def mock_memory():
    """A mocked Memory."""
    memory = MagicMock()
    memory.get_context_messages.return_value = []
    return memory


@pytest.fixture
def safety_guard():
    """A real SafetyGuard instance for testing risk classification."""
    return SafetyGuard(require_confirmation=False)


@pytest.fixture
def all_skills():
    """Dict of all skill instances (with mocked configs for those that need them)."""
    skills = {}
    skills["web_search"] = WebSearchSkill()

    # Add a simple mock skill for testing pipeline
    mock_skill = MagicMock(spec=BaseSkill)
    mock_skill.name = "mock_skill"
    mock_skill.description = "A mock skill for testing"
    mock_skill.parameters = {"input": "test input"}
    mock_skill.execute.return_value = SkillResult(success=True, message="Mock result")
    skills["mock_skill"] = mock_skill

    return skills
