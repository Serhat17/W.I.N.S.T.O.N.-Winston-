"""
Tests for the web search fallback logic in server.py and main.py.
Verifies that _needs_web_search() correctly detects when a web search
should be injected even if the LLM didn't emit one.
"""

import pytest
from unittest.mock import MagicMock, patch
from winston.skills.web_search import WebSearchSkill
from winston.skills.base import SkillResult


class FallbackHarness:
    """
    Minimal harness that mirrors the _needs_web_search logic from server.py
    so we can test it in isolation without starting the full server.
    """

    _WEB_SEARCH_TRIGGERS = [
        "weather", "temperature", "forecast", "rain", "sunny", "snow",
        "news", "latest", "current", "today", "right now", "what is happening",
        "search", "look up", "find", "google", "who is", "what is", "price of",
        "stock", "score", "result", "when does", "how much",
    ]

    _LLM_GAVE_UP = [
        "i couldn't find", "i can't find", "i don't have access",
        "i'm not able to", "i am not able", "i don't know the current",
        "i cannot access", "real-time", "up-to-date information",
        "would you like me to search", "would you like me to look",
        "let me search", "let me look",
        "can't perform web search", "cannot perform web search",
    ]

    def __init__(self, has_web_search=True):
        self.skills = {}
        if has_web_search:
            self.skills["web_search"] = WebSearchSkill()

    def _needs_web_search(self, user_input: str, llm_response: str) -> bool:
        if "web_search" not in self.skills:
            return False
        low_input = user_input.lower()
        low_resp = llm_response.lower()
        has_trigger = any(kw in low_input for kw in self._WEB_SEARCH_TRIGGERS)
        llm_gave_up = any(ph in low_resp for ph in self._LLM_GAVE_UP)
        return has_trigger or llm_gave_up


@pytest.fixture
def harness():
    return FallbackHarness(has_web_search=True)


@pytest.fixture
def harness_no_skill():
    return FallbackHarness(has_web_search=False)


# ── Trigger keyword tests ──

def test_fallback_triggers_on_search_keyword(harness):
    assert harness._needs_web_search(
        "search for cheap flights to Istanbul",
        "Sure, let me help you with that.",
    )


def test_fallback_triggers_on_find_keyword(harness):
    assert harness._needs_web_search(
        "find me a good restaurant nearby",
        "I'd be happy to help!",
    )


def test_fallback_triggers_on_weather(harness):
    assert harness._needs_web_search(
        "what's the weather in Berlin?",
        "Let me check that for you.",
    )


def test_fallback_triggers_on_news(harness):
    assert harness._needs_web_search(
        "what's the latest news?",
        "Here's what I know.",
    )


def test_fallback_triggers_on_price(harness):
    assert harness._needs_web_search(
        "what is the price of Bitcoin?",
        "I'll look that up.",
    )


def test_fallback_triggers_on_who_is(harness):
    assert harness._needs_web_search(
        "who is the president of France?",
        "Let me find out.",
    )


# ── LLM gave up tests ──

def test_fallback_triggers_on_llm_cant_search(harness):
    assert harness._needs_web_search(
        "hello",
        "I'm sorry, but I can't perform web searches at the moment.",
    )


def test_fallback_triggers_on_llm_no_access(harness):
    assert harness._needs_web_search(
        "hello",
        "I don't have access to the internet right now.",
    )


def test_fallback_triggers_on_llm_realtime(harness):
    assert harness._needs_web_search(
        "hello",
        "I don't have real-time data available.",
    )


def test_fallback_triggers_on_llm_would_you_like(harness):
    assert harness._needs_web_search(
        "hello",
        "Would you like me to search for that information?",
    )


def test_fallback_triggers_on_llm_unable(harness):
    assert harness._needs_web_search(
        "hello",
        "I'm not able to access web data currently.",
    )


# ── No-trigger tests ──

def test_no_fallback_on_greeting(harness):
    assert not harness._needs_web_search(
        "hello how are you",
        "I'm doing great, thanks for asking!",
    )


def test_no_fallback_on_simple_question(harness):
    assert not harness._needs_web_search(
        "tell me a joke",
        "Why did the chicken cross the road?",
    )


def test_no_fallback_on_note_taking(harness):
    assert not harness._needs_web_search(
        "take a note: buy milk",
        "I've saved that note for you.",
    )


# ── Skill-not-registered test ──

def test_no_fallback_when_web_search_not_registered(harness_no_skill):
    """If web_search skill is not registered, never trigger fallback."""
    assert not harness_no_skill._needs_web_search(
        "search for cheap flights",
        "I can't find that information.",
    )


# ── Real scenario from the bug ──

def test_fallback_dusseldorf_istanbul_flight(harness):
    """The exact scenario that triggered the bug report."""
    assert harness._needs_web_search(
        "Hey Winston are you able to find me a Cheap Flight from Düsseldorf to Istanbul in April? Thourgh Web Search",
        "I'm sorry, it looks like we don't have the capability to search for flights directly at the moment.",
    )
