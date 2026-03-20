"""
Scheduler system for W.I.N.S.T.O.N.
APScheduler-based cron jobs, heartbeats, and webhook-triggered tasks.
Results are broadcast to active channels.
"""

import asyncio
import json
import logging
import secrets
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("winston.scheduler")


@dataclass
class ScheduledTask:
    """Represents a scheduled task."""
    id: str
    name: str
    description: str = ""
    task_type: str = "interval"  # "cron", "interval"
    schedule: dict = field(default_factory=dict)
    action: dict = field(default_factory=dict)
    enabled: bool = False
    created: str = ""
    last_run: Optional[str] = None
    last_result: Optional[str] = None
    deliver_to: list[str] = field(default_factory=list)


class Scheduler:
    """
    APScheduler-based task scheduling system.

    Supports:
    - Cron jobs (exact times: "every day at 8:00")
    - Interval tasks (recurring: "every 30 minutes")
    - Manual triggers (via API or chat)
    - Result broadcasting to channels
    """

    SCHEDULES_FILE = "~/.winston/schedules.json"

    def __init__(self, skills: dict = None, routines=None,
                 brain=None, channel_manager=None):
        self.skills = skills or {}
        self.routines = routines
        self.brain = brain
        self.channel_manager = channel_manager
        self._schedules_file = Path(self.SCHEDULES_FILE).expanduser()
        self._schedules_file.parent.mkdir(parents=True, exist_ok=True)
        self.scheduler = None
        self._tasks: dict[str, ScheduledTask] = {}

        self._load_schedules()
        self._register_builtin_tasks()

    def _register_builtin_tasks(self):
        """Register default scheduled tasks (disabled by default)."""
        builtins = {
            "morning_briefing": ScheduledTask(
                id="morning_briefing",
                name="Morning Briefing",
                description="Daily morning summary with calendar, news, and weather",
                task_type="cron",
                schedule={"hour": 8, "minute": 0},
                action={"type": "routine", "routine_id": "good_morning"},
                enabled=True,
                deliver_to=["telegram", "discord"],
            ),
            "evening_summary": ScheduledTask(
                id="evening_summary",
                name="Evening Summary",
                description="End of day summary with tomorrow's events",
                task_type="cron",
                schedule={"hour": 21, "minute": 0},
                action={"type": "routine", "routine_id": "good_night"},
                enabled=True,
                deliver_to=["telegram", "discord"],
            ),
            "heartbeat": ScheduledTask(
                id="heartbeat",
                name="Heartbeat",
                description="Periodic system health check",
                task_type="interval",
                schedule={"minutes": 30},
                action={"type": "routine", "routine_id": "system_check"},
                enabled=True,
                deliver_to=[],  # Silent — just logs
            ),
            "email_check": ScheduledTask(
                id="email_check",
                name="Email Check",
                description="Check for new emails periodically",
                task_type="interval",
                schedule={"minutes": 15},
                action={"type": "skill", "skill": "email", "parameters": {"action": "read", "count": "3"}},
                enabled=False,  # Needs email config first
                deliver_to=["telegram"],
            ),
            "price_monitor_check": ScheduledTask(
                id="price_monitor_check",
                name="Price Monitor Check",
                description="Check all watched prices for drops",
                task_type="interval",
                schedule={"minutes": 60},
                action={"type": "monitor"},
                enabled=False,  # Auto-enabled when first watch is added
                deliver_to=["telegram"],
            ),
        }

        for task_id, task in builtins.items():
            if task_id not in self._tasks:
                self._tasks[task_id] = task

    def _load_schedules(self):
        """Load persisted schedules from disk."""
        if self._schedules_file.exists():
            try:
                data = json.loads(self._schedules_file.read_text())
                for task_data in data.get("tasks", []):
                    task = ScheduledTask(**task_data)
                    self._tasks[task.id] = task
                logger.info(f"Loaded {len(self._tasks)} scheduled tasks")
            except Exception as e:
                logger.warning(f"Failed to load schedules: {e}")

    def _save_schedules(self):
        """Persist schedules to disk."""
        data = {"tasks": [asdict(t) for t in self._tasks.values()]}
        self._schedules_file.write_text(json.dumps(data, indent=2, default=str))

    async def start(self):
        """Start the scheduler and register all enabled tasks."""
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
        except ImportError:
            logger.warning(
                "APScheduler not installed. Scheduler disabled. "
                "Install with: pip install 'APScheduler>=3.10.0'"
            )
            return

        self.scheduler = AsyncIOScheduler(
            job_defaults={
                # Allow up to 15 minutes late — covers event-loop stalls from
                # blocking LLM / network calls and macOS wake-from-sleep gaps.
                "misfire_grace_time": 900,
                "coalesce": True,
                "max_instances": 1,
            },
        )

        for task_id, task in self._tasks.items():
            if task.enabled:
                self._add_job(task)

        self.scheduler.start()
        enabled_count = sum(1 for t in self._tasks.values() if t.enabled)
        logger.info(f"Scheduler started: {enabled_count} enabled / {len(self._tasks)} total tasks")

        # Fire missed cron jobs on startup.  Cron jobs only trigger at their
        # exact time — if Winston wasn't running then, the job silently
        # disappears until tomorrow.  Interval jobs don't have this problem
        # because they fire relative to "now".  So after startup we check
        # every enabled cron task and fire it immediately if it hasn't run
        # in the last 24 hours (or has never run at all).
        await self._catch_up_missed_cron_jobs()

    async def _catch_up_missed_cron_jobs(self):
        """Fire cron jobs that missed their window (e.g. Winston wasn't running at 8 AM)."""
        now = datetime.now()
        for task_id, task in self._tasks.items():
            if not task.enabled or task.task_type != "cron":
                continue
            if not task.deliver_to:
                continue  # skip silent jobs like heartbeat

            needs_catchup = False
            if task.last_run is None:
                needs_catchup = True
            else:
                try:
                    last = datetime.fromisoformat(task.last_run)
                    hours_since = (now - last).total_seconds() / 3600
                    if hours_since > 20:  # missed by more than ~20 hours
                        needs_catchup = True
                except (ValueError, TypeError):
                    needs_catchup = True

            if needs_catchup:
                logger.info(
                    f"Catching up missed cron job: {task.name} "
                    f"(last_run={task.last_run})"
                )
                # Small delay so channels have time to connect first
                asyncio.get_event_loop().call_later(
                    30, lambda tid=task_id: asyncio.ensure_future(self._execute_task(tid))
                )

    async def stop(self):
        """Graceful shutdown."""
        if self.scheduler:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")

    def _add_job(self, task: ScheduledTask):
        """Add a task to APScheduler."""
        if not self.scheduler:
            return

        try:
            from apscheduler.triggers.cron import CronTrigger
            from apscheduler.triggers.interval import IntervalTrigger
        except ImportError:
            return

        if task.task_type == "cron":
            trigger = CronTrigger(
                hour=task.schedule.get("hour", 0),
                minute=task.schedule.get("minute", 0),
                day_of_week=task.schedule.get("day_of_week", "*"),
            )
        elif task.task_type == "interval":
            trigger = IntervalTrigger(
                minutes=task.schedule.get("minutes", 30),
            )
        else:
            logger.warning(f"Unknown task type: {task.task_type}")
            return

        self.scheduler.add_job(
            self._execute_task,
            trigger=trigger,
            id=task.id,
            args=[task.id],
            replace_existing=True,
        )
        logger.info(f"Scheduled job: {task.name} ({task.task_type})")

    async def _execute_task(self, task_id: str):
        """Execute a scheduled task and deliver results."""
        task = self._tasks.get(task_id)
        if not task:
            return

        logger.info(f"Executing scheduled task: {task.name}")
        task.last_run = datetime.now().isoformat()

        result_text = ""
        action = task.action

        try:
            if action.get("type") == "routine" and self.routines:
                routine_id = action.get("routine_id", "")
                # Run synchronous routine in a thread to avoid blocking the
                # event loop (which would stall Telegram + APScheduler timers).
                results = await asyncio.to_thread(
                    self.routines.execute_routine, routine_id
                )
                results_text = "\n".join(
                    f"- {r.get('message', '')}" for r in results if r
                )

                # Use LLM to summarize if brain is available
                if self.brain and results_text:
                    result_text = await asyncio.to_thread(
                        self.brain.think,
                        f"Summarize these scheduled task results naturally and concisely:\n{results_text}",
                        None,  # conversation_history
                        (
                            "You are summarizing automated task results. "
                            "Be concise and natural. No JSON blocks."
                        ),
                    )
                    # Strip any skill blocks the LLM might emit
                    if '"skill"' in result_text:
                        result_text = self.brain.strip_skill_blocks(result_text)
                else:
                    result_text = results_text or "Routine completed (no results)"

            elif action.get("type") == "monitor":
                # Delegate to the monitor engine if available
                if hasattr(self, "monitor_engine") and self.monitor_engine:
                    alerts = await self.monitor_engine.run_check_cycle()
                    if alerts:
                        result_text = f"Price monitor: {len(alerts)} alert(s) triggered"
                    else:
                        result_text = "Price monitor: no alerts this cycle"
                    # Monitor engine handles its own broadcasting, skip channel delivery
                    task.last_result = result_text[:500]
                    self._save_schedules()
                    return result_text
                elif "price_monitor" in self.skills:
                    result = self.skills["price_monitor"].execute(action="check_all")
                    result_text = result.message
                else:
                    result_text = "Price monitor not available"

            elif action.get("type") == "skill":
                skill_name = action.get("skill", "")
                params = action.get("parameters", {})
                if skill_name in self.skills:
                    result = await asyncio.to_thread(
                        self.skills[skill_name].execute, **params
                    )
                    result_text = result.message
                else:
                    result_text = f"Skill '{skill_name}' not available"

        except Exception as e:
            result_text = f"Task failed: {e}"
            logger.error(
                f"Scheduled task {task.name} failed: {e}\n"
                f"{traceback.format_exc()}"
            )

        task.last_result = result_text[:500] if result_text else "No result"
        self._save_schedules()

        # Deliver to channels
        if task.deliver_to and self.channel_manager and result_text:
            header = f"**{task.name}**\n"
            try:
                await self.channel_manager.broadcast(header + result_text)
            except Exception as e:
                logger.error(f"Failed to broadcast task result: {e}")

        return result_text

    # ── CRUD for tasks ──

    def create_task(self, name: str, task_type: str = "interval",
                    schedule: dict = None, action: dict = None,
                    deliver_to: list = None, description: str = "") -> str:
        """Create a new scheduled task. Returns task ID."""
        task_id = f"custom_{secrets.token_hex(4)}"
        task = ScheduledTask(
            id=task_id,
            name=name,
            description=description,
            task_type=task_type,
            schedule=schedule or {"minutes": 60},
            action=action or {},
            enabled=True,
            created=datetime.now().isoformat(),
            deliver_to=deliver_to or [],
        )
        self._tasks[task_id] = task
        if self.scheduler:
            self._add_job(task)
        self._save_schedules()
        logger.info(f"Created scheduled task: {name} ({task_id})")
        return task_id

    def update_task(self, task_id: str, schedule: dict = None, enabled: bool = None, action: dict = None) -> bool:
        """Update an existing scheduled task."""
        task = self._tasks.get(task_id)
        if not task:
            return False
            
        if schedule is not None:
            task.schedule = schedule
        if enabled is not None:
            task.enabled = enabled
        if action is not None:
            task.action = action
            
        if self.scheduler:
            if task.enabled:
                self._add_job(task)
            else:
                try:
                    self.scheduler.remove_job(task_id)
                except Exception:
                    pass
                    
        self._save_schedules()
        logger.info(f"Updated scheduled task: {task.name} ({task_id})")
        return True

    def delete_task(self, task_id: str) -> bool:
        """Delete a scheduled task."""
        if task_id in self._tasks:
            del self._tasks[task_id]
            if self.scheduler:
                try:
                    self.scheduler.remove_job(task_id)
                except Exception:
                    pass
            self._save_schedules()
            logger.info(f"Deleted scheduled task: {task_id}")
            return True
        return False

    def enable_task(self, task_id: str) -> bool:
        """Enable a scheduled task."""
        task = self._tasks.get(task_id)
        if task:
            task.enabled = True
            if self.scheduler:
                self._add_job(task)
            self._save_schedules()
            return True
        return False

    def disable_task(self, task_id: str) -> bool:
        """Disable a scheduled task."""
        task = self._tasks.get(task_id)
        if task:
            task.enabled = False
            if self.scheduler:
                try:
                    self.scheduler.remove_job(task_id)
                except Exception:
                    pass
            self._save_schedules()
            return True
        return False

    def list_tasks(self) -> list[dict]:
        """List all scheduled tasks."""
        return [
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "type": t.task_type,
                "schedule": t.schedule,
                "enabled": t.enabled,
                "last_run": t.last_run,
                "last_result": t.last_result,
                "deliver_to": t.deliver_to,
            }
            for t in self._tasks.values()
        ]

    def get_task(self, task_id: str) -> Optional[dict]:
        """Get a task by ID."""
        task = self._tasks.get(task_id)
        if task:
            return asdict(task)
        return None

    async def trigger_task(self, task_id: str) -> str:
        """Manually trigger a task (for webhooks and chat commands)."""
        result = await self._execute_task(task_id)
        return result or "Task not found or produced no result"
