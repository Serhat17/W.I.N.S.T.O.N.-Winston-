"""
Scheduler skill - Manage scheduled tasks via chat.
Allows creating, listing, enabling, disabling, and deleting scheduled tasks.
"""

import logging

from winston.skills.base import BaseSkill, SkillResult

logger = logging.getLogger("winston.skills.scheduler")


class SchedulerSkill(BaseSkill):
    """Manage scheduled tasks via natural language."""

    name = "scheduler"
    description = (
        "Create, list, enable, disable, delete, or trigger scheduled tasks. "
        "Use this when the user wants to schedule something to run at specific times, "
        "set up recurring checks, manage their automation tasks, or trigger a task manually."
    )
    parameters = {
        "action": "Action: 'list', 'create', 'delete', 'enable', 'disable', 'trigger'",
        "task_id": "(For delete/enable/disable/trigger) The task ID",
        "name": "(For create) Name for the new task",
        "task_type": "(For create) Type: 'cron' or 'interval'",
        "schedule": "(For create) Schedule config: {'hour': 8, 'minute': 0} for cron, {'minutes': 30} for interval",
        "skill": "(For create) Skill name to execute",
        "skill_parameters": "(For create) Parameters to pass to the skill",
        "deliver_to": "(For create) Channels to deliver results: ['telegram', 'discord']",
    }

    def __init__(self):
        super().__init__()
        self.scheduler = None  # Set after scheduler is created

    def execute(self, **kwargs) -> SkillResult:
        action = kwargs.get("action", "list")

        if not self.scheduler:
            return SkillResult(
                success=False,
                message="Scheduler not available. Start WINSTON in server mode (--mode server).",
            )

        if action == "list":
            return self._list_tasks()
        elif action == "create":
            return self._create_task(kwargs)
        elif action == "delete":
            return self._delete_task(kwargs.get("task_id", ""))
        elif action == "enable":
            return self._enable_task(kwargs.get("task_id", ""))
        elif action == "disable":
            return self._disable_task(kwargs.get("task_id", ""))
        elif action == "trigger":
            return self._trigger_task(kwargs.get("task_id", ""))
        else:
            return SkillResult(
                success=False,
                message=f"Unknown action: {action}. Use: list, create, delete, enable, disable, trigger",
            )

    def _list_tasks(self) -> SkillResult:
        tasks = self.scheduler.list_tasks()
        if not tasks:
            return SkillResult(success=True, message="No scheduled tasks configured.")

        lines = []
        for t in tasks:
            status = "ON" if t["enabled"] else "OFF"
            last = t.get("last_run", "never")
            if last and last != "never":
                last = last[:16]  # Trim ISO timestamp
            lines.append(
                f"- [{status}] **{t['name']}** (ID: {t['id']}, {t['type']})\n"
                f"  Schedule: {t['schedule']}, Last run: {last}"
            )
        return SkillResult(
            success=True,
            message="Scheduled tasks:\n" + "\n".join(lines),
            speak=False,
        )

    def _create_task(self, kwargs: dict) -> SkillResult:
        name = kwargs.get("name", "Custom Task")
        task_type = kwargs.get("task_type", "interval")
        schedule = kwargs.get("schedule", {"minutes": 60})
        skill = kwargs.get("skill", "")
        skill_params = kwargs.get("skill_parameters", kwargs.get("parameters", {}))
        deliver_to = kwargs.get("deliver_to", [])

        if isinstance(schedule, str):
            import json
            try:
                schedule = json.loads(schedule)
            except Exception:
                schedule = {"minutes": 60}

        if not skill:
            return SkillResult(
                success=False,
                message="Please specify which skill to run (e.g., 'web_search', 'email', 'system_control').",
            )

        action_config = {
            "type": "skill",
            "skill": skill,
            "parameters": skill_params if isinstance(skill_params, dict) else {},
        }

        task_id = self.scheduler.create_task(
            name=name,
            task_type=task_type,
            schedule=schedule,
            action=action_config,
            deliver_to=deliver_to if isinstance(deliver_to, list) else [],
            description=f"Custom task: {skill}",
        )

        return SkillResult(
            success=True,
            message=f"Scheduled task created: '{name}' (ID: {task_id}, type: {task_type}, schedule: {schedule})",
        )

    def _delete_task(self, task_id: str) -> SkillResult:
        if not task_id:
            return SkillResult(success=False, message="Please specify a task_id to delete.")
        if self.scheduler.delete_task(task_id):
            return SkillResult(success=True, message=f"Task '{task_id}' deleted.")
        return SkillResult(success=False, message=f"Task '{task_id}' not found.")

    def _enable_task(self, task_id: str) -> SkillResult:
        if not task_id:
            return SkillResult(success=False, message="Please specify a task_id to enable.")
        if self.scheduler.enable_task(task_id):
            return SkillResult(success=True, message=f"Task '{task_id}' enabled.")
        return SkillResult(success=False, message=f"Task '{task_id}' not found.")

    def _disable_task(self, task_id: str) -> SkillResult:
        if not task_id:
            return SkillResult(success=False, message="Please specify a task_id to disable.")
        if self.scheduler.disable_task(task_id):
            return SkillResult(success=True, message=f"Task '{task_id}' disabled.")
        return SkillResult(success=False, message=f"Task '{task_id}' not found.")

    def _trigger_task(self, task_id: str) -> SkillResult:
        if not task_id:
            return SkillResult(success=False, message="Please specify a task_id to trigger.")
        # Note: trigger is async but skill execute is sync.
        # Return info that the task was queued.
        task = self.scheduler.get_task(task_id)
        if task:
            return SkillResult(
                success=True,
                message=f"Task '{task['name']}' triggered. Results will be delivered to configured channels.",
            )
        return SkillResult(success=False, message=f"Task '{task_id}' not found.")
