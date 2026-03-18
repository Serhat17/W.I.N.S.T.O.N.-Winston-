"""
Tests for the Scheduler system.
Validates task CRUD, built-in registration, enable/disable logic.
"""

import pytest
from winston.core.scheduler import Scheduler, ScheduledTask


@pytest.fixture
def scheduler(tmp_path):
    """A Scheduler with temp storage, no APScheduler running."""
    sched = Scheduler()
    sched._schedules_file = tmp_path / "schedules.json"
    return sched


class TestBuiltinTasks:
    """Tests for built-in task registration."""

    def test_builtins_registered(self, scheduler):
        """Default tasks should be registered."""
        tasks = scheduler.list_tasks()
        ids = [t["id"] for t in tasks]
        assert "morning_briefing" in ids
        assert "evening_summary" in ids
        assert "heartbeat" in ids
        assert "email_check" in ids
        assert "price_monitor_check" in ids

    def test_morning_briefing_enabled(self, scheduler):
        """Morning briefing should be enabled by default."""
        task = scheduler.get_task("morning_briefing")
        assert task is not None
        assert task["enabled"] is True

    def test_email_check_disabled(self, scheduler):
        """Email check should be disabled by default (needs config)."""
        task = scheduler.get_task("email_check")
        assert task is not None
        assert task["enabled"] is False


class TestTaskCRUD:
    """Tests for task create/read/update/delete."""

    def test_create_task(self, scheduler):
        """Creating a task should return a valid ID."""
        task_id = scheduler.create_task(
            name="My Task",
            task_type="interval",
            schedule={"minutes": 15},
            action={"type": "skill", "skill": "web_search", "parameters": {"query": "test"}},
            description="Test task",
        )
        assert task_id is not None
        assert task_id.startswith("custom_")

    def test_created_task_in_list(self, scheduler):
        """Created task should appear in the task list."""
        task_id = scheduler.create_task(name="Listed Task")
        tasks = scheduler.list_tasks()
        ids = [t["id"] for t in tasks]
        assert task_id in ids

    def test_get_task(self, scheduler):
        """get_task should return task details."""
        task_id = scheduler.create_task(
            name="Get Me",
            description="A task to retrieve",
        )
        task = scheduler.get_task(task_id)
        assert task is not None
        assert task["name"] == "Get Me"
        assert task["description"] == "A task to retrieve"

    def test_delete_task(self, scheduler):
        """Deleting a task should remove it."""
        task_id = scheduler.create_task(name="To Delete")
        result = scheduler.delete_task(task_id)
        assert result is True

        task = scheduler.get_task(task_id)
        assert task is None

    def test_delete_nonexistent(self, scheduler):
        """Deleting a non-existent task should return False."""
        result = scheduler.delete_task("nonexistent_id")
        assert result is False


class TestTaskEnableDisable:
    """Tests for enabling/disabling tasks."""

    def test_disable_task(self, scheduler):
        """Disabling a task should set enabled=False."""
        result = scheduler.disable_task("morning_briefing")
        assert result is True
        task = scheduler.get_task("morning_briefing")
        assert task["enabled"] is False

    def test_enable_task(self, scheduler):
        """Enabling a disabled task should set enabled=True."""
        scheduler.disable_task("morning_briefing")
        result = scheduler.enable_task("morning_briefing")
        assert result is True
        task = scheduler.get_task("morning_briefing")
        assert task["enabled"] is True

    def test_enable_nonexistent(self, scheduler):
        """Enabling a non-existent task should return False."""
        result = scheduler.enable_task("nonexistent")
        assert result is False

    def test_update_schedule(self, scheduler):
        """Updating a task's schedule should persist."""
        task_id = scheduler.create_task(
            name="Update Me",
            schedule={"minutes": 30},
        )
        result = scheduler.update_task(task_id, schedule={"minutes": 10})
        assert result is True

        task = scheduler.get_task(task_id)
        assert task["schedule"]["minutes"] == 10
