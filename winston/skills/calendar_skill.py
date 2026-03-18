"""
Calendar & Reminders Skill - Create events, set reminders, get daily briefing.
Uses macOS Calendar.app integration via osascript + file-based reminders with timers.
"""

import json
import logging
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from winston.skills.base import BaseSkill, SkillResult

logger = logging.getLogger("winston.skills.calendar")


class CalendarSkill(BaseSkill):
    """Manage calendar events and reminders."""

    name = "calendar"
    description = (
        "Create calendar events, set timed reminders, list upcoming events, "
        "manage to-do items in Reminders.app, and get a daily briefing. "
        "Use this when the user asks to schedule something, set a reminder, "
        "check their calendar, list their to-dos, add a task, or asks what's coming up."
    )
    parameters = {
        "action": "Action: 'create_event', 'list_events', 'remind', 'list_reminders', 'briefing', 'list_todos', 'add_todo', 'complete_todo'",
        "title": "(create_event/remind/add_todo) Title of event, reminder, or to-do",
        "date": "(create_event) Date in YYYY-MM-DD format",
        "time": "(create_event/remind) Time in HH:MM format (24h)",
        "duration": "(create_event) Duration in minutes (default: 60)",
        "minutes": "(remind) Minutes from now for the reminder (default: 30)",
        "note": "(create_event/remind/add_todo) Additional notes",
        "list_name": "(list_todos/add_todo/complete_todo) Reminders list name (default: first list)",
        "todo_name": "(complete_todo) Exact name of the to-do to mark complete",
    }

    REMINDERS_FILE = Path.home() / ".winston" / "reminders.json"

    def __init__(self, config=None):
        super().__init__(config)
        self.REMINDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._active_timers: dict[str, threading.Timer] = {}
        # Callback to send reminder notifications to messaging channels (e.g. Telegram)
        # Signature: _notify_fn(message: str) -> None  (sync, thread-safe)
        self._notify_fn = None
        self._load_active_reminders()

    def execute(self, **kwargs) -> SkillResult:
        action = kwargs.get("action", "list_events")

        actions = {
            "create_event": lambda: self._create_event(
                kwargs.get("title", ""),
                kwargs.get("date", ""),
                kwargs.get("time", ""),
                int(kwargs.get("duration", 60)),
                kwargs.get("note", ""),
            ),
            "list_events": lambda: self._list_events(int(kwargs.get("days", 7))),
            "remind": lambda: self._set_reminder(
                kwargs.get("title", ""),
                int(kwargs.get("minutes", 30)),
                kwargs.get("note", ""),
                kwargs.get("time", ""),
            ),
            "list_reminders": self._list_reminders,
            "briefing": self._daily_briefing,
            "list_todos": lambda: self._list_todos(kwargs.get("list_name", "")),
            "add_todo": lambda: self._add_todo(
                kwargs.get("title", ""),
                kwargs.get("note", ""),
                kwargs.get("list_name", ""),
            ),
            "complete_todo": lambda: self._complete_todo(
                kwargs.get("todo_name", ""),
                kwargs.get("list_name", ""),
            ),
        }

        handler = actions.get(action)
        if handler:
            return handler()
        return SkillResult(success=False, message=f"Unknown calendar action: {action}")

    def _create_event(self, title: str, date: str, time_str: str,
                      duration: int = 60, note: str = "") -> SkillResult:
        """Create a calendar event using macOS Calendar.app."""
        if not title:
            return SkillResult(success=False, message="No event title provided.")

        # Default to today if no date
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")
        if not time_str:
            time_str = "09:00"

        try:
            # Parse the date/time
            event_dt = datetime.strptime(f"{date} {time_str}", "%Y-%m-%d %H:%M")
            end_dt = event_dt + timedelta(minutes=duration)

            # Use AppleScript date arithmetic to avoid locale-dependent date parsing
            now = datetime.now()
            start_offset = max(0, int((event_dt - now).total_seconds()))
            end_offset = max(0, int((end_dt - now).total_seconds()))

            # AppleScript to create calendar event
            script = f'''
            tell application "Calendar"
                tell calendar "Home"
                    set startDate to (current date) + {start_offset}
                    set endDate to (current date) + {end_offset}
                    set newEvent to make new event with properties {{summary:"{title}", start date:startDate, end date:endDate}}
                    if "{note}" is not "" then
                        set description of newEvent to "{note}"
                    end if
                end tell
            end tell
            '''

            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                # If Calendar.app fails, save as a local event file
                return self._save_local_event(title, event_dt, end_dt, note)

            return SkillResult(
                success=True,
                message=f"Calendar event created: '{title}' on {event_dt.strftime('%B %d at %I:%M %p')} ({duration} min).",
            )
        except ValueError as e:
            return SkillResult(success=False, message=f"Invalid date/time format: {e}")
        except Exception as e:
            return self._save_local_event(title, event_dt, end_dt, note)

    def _save_local_event(self, title: str, start: datetime,
                          end: datetime, note: str) -> SkillResult:
        """Save event locally when Calendar.app isn't available."""
        events_file = Path.home() / ".winston" / "events.json"
        events = []
        if events_file.exists():
            events = json.loads(events_file.read_text())

        events.append({
            "title": title,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "note": note,
            "created": datetime.now().isoformat(),
        })

        events_file.write_text(json.dumps(events, indent=2))
        return SkillResult(
            success=True,
            message=f"Event saved locally: '{title}' on {start.strftime('%B %d at %I:%M %p')}.",
        )

    def _list_events(self, days: int = 7) -> SkillResult:
        """List upcoming calendar events."""
        try:
            # Try macOS Calendar first
            start_date = datetime.now().strftime("%B %d, %Y")
            end_date = (datetime.now() + timedelta(days=days)).strftime("%B %d, %Y")

            script = f'''
            set output to ""
            tell application "Calendar"
                repeat with c in calendars
                    set calName to name of c
                    set eventList to (every event of c whose start date >= (date "{start_date}") and start date <= (date "{end_date}"))
                    repeat with e in eventList
                        set eventStart to start date of e
                        set eventTitle to summary of e
                        set output to output & calName & " | " & eventTitle & " | " & (eventStart as string) & linefeed
                    end repeat
                end repeat
            end tell
            return output
            '''

            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=15,
            )

            events_text = result.stdout.strip()
            if events_text:
                response = f"Upcoming events (next {days} days):\n\n"
                for line in events_text.split("\n"):
                    if line.strip():
                        parts = line.split(" | ")
                        if len(parts) >= 3:
                            response += f"  [{parts[0].strip()}] {parts[1].strip()} - {parts[2].strip()}\n"
                        else:
                            response += f"  {line}\n"
                return SkillResult(success=True, message=response, speak=False)
            else:
                # Check local events
                return self._list_local_events(days)

        except Exception as e:
            return self._list_local_events(days)

    def _list_local_events(self, days: int) -> SkillResult:
        """List locally saved events."""
        events_file = Path.home() / ".winston" / "events.json"
        if not events_file.exists():
            return SkillResult(success=True, message=f"No upcoming events in the next {days} days.")

        events = json.loads(events_file.read_text())
        now = datetime.now()
        cutoff = now + timedelta(days=days)

        upcoming = [
            e for e in events
            if now <= datetime.fromisoformat(e["start"]) <= cutoff
        ]

        if not upcoming:
            return SkillResult(success=True, message=f"No upcoming events in the next {days} days.")

        upcoming.sort(key=lambda e: e["start"])
        response = f"Upcoming events (next {days} days):\n\n"
        for e in upcoming:
            dt = datetime.fromisoformat(e["start"])
            response += f"  {dt.strftime('%b %d %I:%M %p')} - {e['title']}\n"

        return SkillResult(success=True, message=response, speak=False)

    def _set_reminder(self, title: str, minutes: int = 30, note: str = "",
                      time_str: str = "") -> SkillResult:
        """Set a timed reminder — writes to macOS Reminders.app with a due date."""
        if not title:
            return SkillResult(success=False, message="No reminder title provided.")

        now = datetime.now()

        # If an exact time was given (e.g. "12:45"), calculate minutes from now
        if time_str:
            try:
                target = datetime.strptime(time_str, "%H:%M").replace(
                    year=now.year, month=now.month, day=now.day
                )
                if target < now:
                    target += timedelta(days=1)
                remind_at = target
                minutes = max(1, int((target - now).total_seconds() / 60))
            except ValueError:
                remind_at = now + timedelta(minutes=minutes)
        else:
            remind_at = now + timedelta(minutes=minutes)

        # Write to macOS Reminders.app with a due date
        # Use AppleScript's "current date" arithmetic to avoid locale-dependent date parsing
        note_line = f'set body of newReminder to "{note}"' if note else ""
        seconds_from_now = max(60, int((remind_at - now).total_seconds()))
        script = f'''
tell application "Reminders"
    set dueDate to (current date) + {seconds_from_now}
    set newReminder to make new reminder at end of default list
    set name of newReminder to "{title}"
    set due date of newReminder to dueDate
    set remind me date of newReminder to dueDate
    {note_line}
end tell
'''
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                logger.warning(f"Could not write reminder to Reminders.app: {result.stderr.strip()}")
        except Exception as e:
            logger.warning(f"Reminders.app write failed: {e}")

        # Also keep internal record so WINSTON can fire a notification
        reminder_id = f"rem_{int(time.time())}"
        reminders = self._load_reminders()
        reminders.append({
            "id": reminder_id,
            "title": title,
            "note": note,
            "remind_at": remind_at.isoformat(),
            "created": now.isoformat(),
            "fired": False,
        })
        self._save_reminders(reminders)
        self._schedule_notification(reminder_id, title, note, minutes * 60)

        return SkillResult(
            success=True,
            message=f"Reminder added: '{title}' at {remind_at.strftime('%I:%M %p')} — saved to your Reminders app.",
        )

    def _schedule_notification(self, reminder_id: str, title: str,
                               note: str, delay_seconds: float):
        """Schedule a notification for the reminder (macOS + Telegram)."""
        def fire():
            try:
                # macOS notification
                notif_text = note or "Time's up!"
                script = (
                    f'display notification "{notif_text}" '
                    f'with title "W.I.N.S.T.O.N. Reminder" '
                    f'subtitle "{title}" '
                    f'sound name "Glass"'
                )
                subprocess.run(["osascript", "-e", script], timeout=5)

                # Send to messaging channels (Telegram etc.)
                if self._notify_fn:
                    detail = f' — {note}' if note else ''
                    self._notify_fn(f"⏰ <b>Reminder:</b> {title}{detail}")

                # Mark as fired
                reminders = self._load_reminders()
                for r in reminders:
                    if r["id"] == reminder_id:
                        r["fired"] = True
                self._save_reminders(reminders)

                logger.info(f"Reminder fired: {title}")
            except Exception as e:
                logger.error(f"Reminder notification error: {e}")

        timer = threading.Timer(delay_seconds, fire)
        timer.daemon = True
        timer.start()
        self._active_timers[reminder_id] = timer

    def _list_reminders(self) -> SkillResult:
        """List all reminders."""
        reminders = self._load_reminders()
        active = [r for r in reminders if not r.get("fired", False)]

        if not active:
            return SkillResult(success=True, message="No active reminders.")

        response = f"Active Reminders ({len(active)}):\n\n"
        for r in active:
            dt = datetime.fromisoformat(r["remind_at"])
            remaining = dt - datetime.now()
            if remaining.total_seconds() > 0:
                mins = int(remaining.total_seconds() / 60)
                response += f"  {dt.strftime('%I:%M %p')} - {r['title']} (in {mins} min)\n"
            else:
                response += f"  {dt.strftime('%I:%M %p')} - {r['title']} (overdue)\n"

        return SkillResult(success=True, message=response, speak=False)

    def _list_todos(self, list_name: str = "") -> SkillResult:
        """List incomplete to-dos from macOS Reminders.app."""
        try:
            if list_name:
                script = f'''
            set output to ""
            tell application "Reminders"
                set theList to list "{list_name}"
                repeat with r in (every reminder of theList whose completed is false)
                    set output to output & name of r & linefeed
                end repeat
            end tell
            return output
            '''
            else:
                script = '''
            set output to ""
            tell application "Reminders"
                repeat with l in every list
                    repeat with r in (every reminder of l whose completed is false)
                        set output to output & (name of l) & " | " & (name of r) & linefeed
                    end repeat
                end repeat
            end tell
            return output
            '''

            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=15,
            )
            text = result.stdout.strip()
            if not text:
                return SkillResult(success=True, message="No open to-dos found in Reminders.")

            response = "Open To-Dos:\n\n"
            for line in text.splitlines():
                if " | " in line:
                    lst, item = line.split(" | ", 1)
                    response += f"  [{lst.strip()}] {item.strip()}\n"
                else:
                    response += f"  • {line.strip()}\n"
            return SkillResult(success=True, message=response, speak=False)

        except Exception as e:
            return SkillResult(success=False, message=f"Could not read Reminders: {e}")

    def _add_todo(self, title: str, note: str = "", list_name: str = "") -> SkillResult:
        """Add a new to-do to macOS Reminders.app."""
        if not title:
            return SkillResult(success=False, message="No to-do title provided.")
        try:
            list_clause = f'list "{list_name}"' if list_name else 'default list'
            note_line = f'set body of newReminder to "{note}"' if note else ''
            script = f'''
            tell application "Reminders"
                set newReminder to make new reminder at end of {list_clause}
                set name of newReminder to "{title}"
                {note_line}
            end tell
            '''
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return SkillResult(success=False, message=f"Failed to add to-do: {result.stderr.strip()}")
            return SkillResult(success=True, message=f"To-do added: '{title}'")
        except Exception as e:
            return SkillResult(success=False, message=f"Could not add to-do: {e}")

    def _complete_todo(self, todo_name: str, list_name: str = "") -> SkillResult:
        """Mark a to-do as complete in macOS Reminders.app."""
        if not todo_name:
            return SkillResult(success=False, message="No to-do name provided.")
        try:
            list_clause = f'list "{list_name}"' if list_name else 'default list'
            script = f'''
            tell application "Reminders"
                set r to first reminder of {list_clause} whose name is "{todo_name}"
                set completed of r to true
            end tell
            '''
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return SkillResult(success=False, message=f"Could not complete to-do: {result.stderr.strip()}")
            return SkillResult(success=True, message=f"Marked complete: '{todo_name}'")
        except Exception as e:
            return SkillResult(success=False, message=f"Could not complete to-do: {e}")

    def _daily_briefing(self) -> SkillResult:
        """Get a daily briefing with events, to-dos, and reminders."""
        now = datetime.now()
        briefing = f"Daily Briefing - {now.strftime('%A, %B %d, %Y')}\n"
        briefing += "=" * 40 + "\n\n"

        # Today's calendar events — query today only (midnight to midnight)
        today_str = now.strftime("%B %d, %Y")
        tomorrow_str = (now + timedelta(days=1)).strftime("%B %d, %Y")
        script = f'''
set output to ""
tell application "Calendar"
    repeat with c in calendars
        set calName to name of c
        set eventList to (every event of c whose start date >= (date "{today_str}") and start date < (date "{tomorrow_str}"))
        repeat with e in eventList
            set output to output & calName & " | " & (summary of e) & " | " & ((start date of e) as string) & linefeed
        end repeat
    end repeat
end tell
return output
'''
        try:
            cal_run = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=15,
            )
            events_text = cal_run.stdout.strip()
        except Exception:
            events_text = ""

        if events_text:
            briefing += "TODAY'S CALENDAR:\n"
            for line in events_text.splitlines():
                parts = line.split(" | ")
                if len(parts) >= 3:
                    briefing += f"  • {parts[1].strip()} at {parts[2].strip()}\n"
                else:
                    briefing += f"  • {line.strip()}\n"
            briefing += "\n"
        else:
            briefing += "TODAY'S CALENDAR: Nothing on your calendar today.\n\n"

        # Open to-dos from Reminders.app
        todos_result = self._list_todos()
        if todos_result.success and "No open" not in todos_result.message:
            briefing += todos_result.message + "\n"
        else:
            briefing += "OPEN TO-DOS: You're all caught up — no open to-dos.\n\n"

        # Active WINSTON reminders
        reminders_result = self._list_reminders()
        if reminders_result.success and "No active" not in reminders_result.message:
            briefing += "WINSTON REMINDERS:\n" + reminders_result.message + "\n"

        briefing += f"\nCurrent time: {now.strftime('%I:%M %p')}\n"
        return SkillResult(success=True, message=briefing, speak=False)

    def _load_reminders(self) -> list:
        """Load reminders from file."""
        if self.REMINDERS_FILE.exists():
            try:
                return json.loads(self.REMINDERS_FILE.read_text())
            except Exception:
                return []
        return []

    def _save_reminders(self, reminders: list):
        """Save reminders to file."""
        self.REMINDERS_FILE.write_text(json.dumps(reminders, indent=2))

    def _load_active_reminders(self):
        """On startup, re-schedule any reminders that haven't fired yet."""
        reminders = self._load_reminders()
        now = datetime.now()
        for r in reminders:
            if r.get("fired", False):
                continue
            remind_at = datetime.fromisoformat(r["remind_at"])
            remaining = (remind_at - now).total_seconds()
            if remaining > 0:
                self._schedule_notification(
                    r["id"], r["title"], r.get("note", ""), remaining
                )
                logger.info(f"Re-scheduled reminder: {r['title']} in {remaining/60:.0f} min")
