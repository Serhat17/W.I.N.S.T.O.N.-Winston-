"""
Tests for the RoutineManager.
Validates creation, execution, trigger matching, and deletion of routines.
"""

import pytest
from unittest.mock import MagicMock
from winston.core.routines import RoutineManager, BUILTIN_ROUTINES
from winston.skills.base import SkillResult


@pytest.fixture
def mock_skills():
    """Dict of mock skills for routine execution."""
    skills = {}
    for name in ["system_control", "calendar", "web_search", "notes"]:
        skill = MagicMock()
        skill.name = name
        skill.execute.return_value = SkillResult(
            success=True, message=f"{name} executed successfully"
        )
        skills[name] = skill
    return skills


@pytest.fixture
def routine_mgr(mock_skills, tmp_path):
    """A RoutineManager wired to mock skills and temp storage."""
    mgr = RoutineManager(skills=mock_skills, storage_dir=str(tmp_path))
    return mgr


class TestBuiltinRoutines:
    """Tests for built-in routines."""

    def test_builtins_loaded(self, routine_mgr):
        """Built-in routines should be available."""
        routines = routine_mgr.list_routines()
        names = [r["id"] for r in routines]
        assert "good_morning" in names
        assert "good_night" in names
        assert "system_check" in names

    def test_good_morning_has_steps(self, routine_mgr):
        """good_morning should have multiple steps."""
        routine = routine_mgr.get_routine("good_morning")
        assert routine is not None
        assert len(routine["steps"]) > 0


class TestRoutineExecution:
    """Tests for executing routines."""

    def test_execute_system_check(self, routine_mgr, mock_skills):
        """Executing system_check should call the relevant skills."""
        results = routine_mgr.execute_routine("system_check")
        assert len(results) > 0
        for result in results:
            assert result["success"] is True

    def test_execute_missing_skill(self, tmp_path):
        """Executing a routine with a missing skill should report failure."""
        mgr = RoutineManager(skills={}, storage_dir=str(tmp_path))
        results = mgr.execute_routine("system_check")
        for result in results:
            assert result["success"] is False
            assert "not available" in result["message"]

    def test_execute_nonexistent_routine(self, routine_mgr):
        """Executing a non-existent routine should return error."""
        results = routine_mgr.execute_routine("nonexistent")
        assert len(results) == 1
        assert results[0]["success"] is False


class TestCustomRoutines:
    """Tests for creating and managing custom routines."""

    def test_create_custom_routine(self, routine_mgr):
        """Creating a custom routine should add it to the list."""
        routine = routine_mgr.create_routine(
            routine_id="my_workflow",
            name="My Workflow",
            description="A test workflow",
            steps=[
                {"skill": "web_search", "parameters": {"query": "test"}, "label": "Search"},
            ],
        )
        assert routine is not None
        assert routine["name"] == "My Workflow"

        # Should appear in list
        routines = routine_mgr.list_routines()
        ids = [r["id"] for r in routines]
        assert "my_workflow" in ids

    def test_delete_custom_routine(self, routine_mgr):
        """Deleting a custom routine should remove it."""
        routine_mgr.create_routine(
            routine_id="temp",
            name="Temp Routine",
            description="Will be deleted",
            steps=[],
        )
        result = routine_mgr.delete_routine("temp")
        assert result is True

        routines = routine_mgr.list_routines()
        ids = [r["id"] for r in routines]
        assert "temp" not in ids

    def test_cannot_delete_builtin(self, routine_mgr):
        """Built-in routines should not be deletable."""
        result = routine_mgr.delete_routine("good_morning")
        assert result is False

    def test_execute_custom_routine(self, routine_mgr, mock_skills):
        """Custom routines should execute their steps."""
        routine_mgr.create_routine(
            routine_id="custom_search",
            name="Custom Search",
            description="Search twice",
            steps=[
                {"skill": "web_search", "parameters": {"query": "first"}, "label": "First search"},
                {"skill": "web_search", "parameters": {"query": "second"}, "label": "Second search"},
            ],
        )
        results = routine_mgr.execute_routine("custom_search")
        assert len(results) == 2
        assert all(r["success"] for r in results)


class TestTriggerMatching:
    """Tests for natural language trigger matching."""

    def test_trigger_good_morning(self, routine_mgr):
        """'good morning' should trigger the good_morning routine."""
        match = routine_mgr.match_routine("good morning")
        assert match == "good_morning"

    def test_trigger_good_night(self, routine_mgr):
        """'good night' should trigger the good_night routine."""
        match = routine_mgr.match_routine("good night")
        assert match == "good_night"

    def test_no_trigger_match(self, routine_mgr):
        """Unrelated input should not match any trigger."""
        match = routine_mgr.match_routine("what's the weather like?")
        assert match is None
