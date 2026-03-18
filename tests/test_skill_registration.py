"""
Tests for skill registration — verifies all skills can be imported,
have the correct interface, and their dependencies are available.
"""

import importlib
import pytest
from winston.skills.base import BaseSkill, SkillResult


# All skill modules and their expected class names
SKILL_MODULES = [
    ("winston.skills.web_search", "WebSearchSkill"),
    ("winston.skills.email_skill", "EmailSkill"),
    ("winston.skills.system_control", "SystemControlSkill"),
    ("winston.skills.notes_skill", "NotesSkill"),
    ("winston.skills.screenshot_skill", "ScreenshotSkill"),
    ("winston.skills.youtube_skill", "YouTubeSkill"),
    ("winston.skills.file_manager_skill", "FileManagerSkill"),
    ("winston.skills.clipboard_skill", "ClipboardSkill"),
    ("winston.skills.calendar_skill", "CalendarSkill"),
    ("winston.skills.smart_home_skill", "SmartHomeSkill"),
    ("winston.skills.code_runner_skill", "CodeRunnerSkill"),
    ("winston.skills.scheduler_skill", "SchedulerSkill"),
    ("winston.skills.audio_analysis_skill", "AudioAnalysisSkill"),
    ("winston.skills.travel_skill", "TravelSkill"),
    ("winston.skills.google_calendar_skill", "GoogleCalendarSkill"),
    ("winston.skills.price_monitor_skill", "PriceMonitorSkill"),
]


@pytest.mark.parametrize("module_path,class_name", SKILL_MODULES)
def test_skill_importable(module_path, class_name):
    """Each skill module should be importable without errors."""
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    assert issubclass(cls, BaseSkill), f"{class_name} must inherit from BaseSkill"


@pytest.mark.parametrize("module_path,class_name", SKILL_MODULES)
def test_skill_has_required_attributes(module_path, class_name):
    """Each skill class must define name, description, parameters, and execute."""
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)

    assert hasattr(cls, "name") and isinstance(cls.name, str) and cls.name != "base_skill", \
        f"{class_name}.name must be a non-default string"
    assert hasattr(cls, "description") and isinstance(cls.description, str), \
        f"{class_name}.description must be a string"
    assert hasattr(cls, "parameters") and isinstance(cls.parameters, dict), \
        f"{class_name}.parameters must be a dict"
    assert hasattr(cls, "execute") and callable(getattr(cls, "execute")), \
        f"{class_name}.execute must be callable"


def test_no_duplicate_skill_names():
    """All skills must have unique names."""
    names = []
    for module_path, class_name in SKILL_MODULES:
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        names.append(cls.name)

    duplicates = [n for n in names if names.count(n) > 1]
    assert not duplicates, f"Duplicate skill names found: {set(duplicates)}"


def test_web_search_dependency_available():
    """The duckduckgo-search dependency must be importable for web_search."""
    from winston.skills.web_search import _HAS_DDGS
    assert _HAS_DDGS, (
        "duckduckgo-search is NOT installed. web_search skill will silently fail. "
        "Fix: pip install duckduckgo-search"
    )
