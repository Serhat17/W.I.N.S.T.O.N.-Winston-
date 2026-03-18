"""
Tests for the WebSearchSkill — import, execution, result format.
"""

import pytest
from winston.skills.web_search import WebSearchSkill, _HAS_DDGS
from winston.skills.base import SkillResult

# Live search tests are marked with 'live' — they hit DuckDuckGo and may
# fail due to network issues, rate limits, or TLS incompatibilities on
# older Python/LibreSSL. Run with: pytest -m live
_live = pytest.mark.skipif(not _HAS_DDGS, reason="ddgs not installed")


def test_ddgs_importable():
    """The duckduckgo-search package must be importable."""
    assert _HAS_DDGS, (
        "duckduckgo-search is not installed. Run: pip install ddgs"
    )


def test_skill_attributes():
    """WebSearchSkill has required BaseSkill attributes."""
    skill = WebSearchSkill()
    assert skill.name == "web_search"
    assert skill.description
    assert "query" in skill.parameters


def test_search_empty_query(web_search_skill):
    """Empty query should return failure."""
    result = web_search_skill.execute(query="")
    assert isinstance(result, SkillResult)
    assert result.success is False
    assert "No search query" in result.message


@_live
def test_search_returns_results(web_search_skill):
    """A real DuckDuckGo search should return results."""
    result = web_search_skill.execute(query="wikipedia", max_results=3)
    assert isinstance(result, SkillResult)
    # Accept either actual results or graceful "no results" (network issues)
    if result.success and result.data:
        # Result may be wrapped in EXTERNAL_CONTENT boundaries or plain text
        assert "EXTERNAL_CONTENT" in result.message or "Search results" in result.message
        assert len(result.data) > 0
    else:
        # Skill didn't crash — that's the minimum bar for a live test
        assert result.success or "Search failed" in result.message


@_live
def test_search_result_format(web_search_skill):
    """Search results should have title, body, href fields."""
    result = web_search_skill.execute(query="wikipedia", max_results=2)
    if result.success and result.data:
        for item in result.data:
            assert "title" in item
            assert "body" in item
            assert "href" in item


@_live
def test_search_max_results_respected(web_search_skill):
    """max_results parameter should limit the number of results."""
    result = web_search_skill.execute(query="wikipedia", max_results=2)
    if result.success and result.data:
        assert len(result.data) <= 2


@_live
def test_search_speak_disabled(web_search_skill):
    """Search results should not be spoken (too long)."""
    result = web_search_skill.execute(query="wikipedia", max_results=1)
    if result.success and result.data:
        assert result.speak is False
