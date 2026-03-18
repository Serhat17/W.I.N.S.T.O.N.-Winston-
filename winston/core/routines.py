"""
Automation & Routines system.
Create, manage, and execute multi-step workflows.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("winston.routines")


# Built-in routine templates
BUILTIN_ROUTINES = {
    "good_morning": {
        "name": "Good Morning",
        "description": "Daily morning briefing",
        "trigger": "good morning",
        "steps": [
            {"skill": "system_control", "parameters": {"action": "time"}, "label": "Current time"},
            {"skill": "calendar", "parameters": {"action": "briefing"}, "label": "Today's schedule"},
            {"skill": "web_search", "parameters": {"query": "top news today", "max_results": 3}, "label": "Today's news"},
        ],
    },
    "good_night": {
        "name": "Good Night",
        "description": "End of day summary",
        "trigger": "good night",
        "steps": [
            {"skill": "calendar", "parameters": {"action": "list_events"}, "label": "Tomorrow's events"},
            {"skill": "calendar", "parameters": {"action": "list_reminders"}, "label": "Pending reminders"},
            {"skill": "notes", "parameters": {"action": "list"}, "label": "Recent notes"},
        ],
    },
    "system_check": {
        "name": "System Check",
        "description": "Full system status report",
        "trigger": "system check",
        "steps": [
            {"skill": "system_control", "parameters": {"action": "system_info"}, "label": "System info"},
            {"skill": "system_control", "parameters": {"action": "time"}, "label": "Current time"},
        ],
    },
}


class RoutineManager:
    """Manage and execute automation routines."""

    def __init__(self, skills: dict = None, storage_dir: str = "~/.winston"):
        self.skills = skills or {}
        self.storage_dir = Path(storage_dir).expanduser()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._routines_file = self.storage_dir / "routines.json"
        self._routines = self._load_routines()

    def _load_routines(self) -> dict:
        """Load custom routines from disk, merge with builtins."""
        custom = {}
        if self._routines_file.exists():
            try:
                custom = json.loads(self._routines_file.read_text())
            except Exception:
                custom = {}

        # Merge: custom overrides builtins
        merged = {**BUILTIN_ROUTINES, **custom}
        return merged

    def _save_routines(self):
        """Save custom (non-builtin) routines to disk."""
        custom = {
            k: v for k, v in self._routines.items()
            if k not in BUILTIN_ROUTINES
        }
        self._routines_file.write_text(json.dumps(custom, indent=2))

    def create_routine(self, routine_id: str, name: str, description: str,
                       steps: list[dict], trigger: str = "") -> dict:
        """Create a new custom routine."""
        routine = {
            "name": name,
            "description": description,
            "trigger": trigger or name.lower(),
            "steps": steps,
            "created": datetime.now().isoformat(),
            "custom": True,
        }
        self._routines[routine_id] = routine
        self._save_routines()
        return routine

    def update_routine(self, routine_id: str, steps: list[dict] = None) -> bool:
        """Update an existing routine's steps. Also works for overriding builtin routines."""
        if routine_id not in self._routines:
            return False
            
        # If modifying a builtin, copy it to custom first
        if routine_id in BUILTIN_ROUTINES and "custom" not in self._routines[routine_id]:
            self._routines[routine_id] = dict(BUILTIN_ROUTINES[routine_id])
            self._routines[routine_id]["custom"] = True
            
        if steps is not None:
            self._routines[routine_id]["steps"] = steps
            
        self._save_routines()
        return True

    def delete_routine(self, routine_id: str) -> bool:
        """Delete a custom routine (can't delete builtins)."""
        if routine_id in BUILTIN_ROUTINES:
            return False
        if routine_id in self._routines:
            del self._routines[routine_id]
            self._save_routines()
            return True
        return False

    def list_routines(self) -> list[dict]:
        """List all available routines."""
        result = []
        for rid, routine in self._routines.items():
            result.append({
                "id": rid,
                "name": routine["name"],
                "description": routine["description"],
                "trigger": routine.get("trigger", ""),
                "steps": len(routine.get("steps", [])),
                "builtin": rid in BUILTIN_ROUTINES,
            })
        return result

    def get_routine(self, routine_id: str) -> Optional[dict]:
        """Get a routine by ID."""
        return self._routines.get(routine_id)

    def match_routine(self, user_input: str) -> Optional[str]:
        """Check if user input matches a routine trigger. Returns routine ID or None."""
        input_lower = user_input.lower().strip()
        for rid, routine in self._routines.items():
            trigger = routine.get("trigger", "").lower()
            if trigger and trigger in input_lower:
                return rid
        return None

    def execute_routine(self, routine_id: str, skills: dict = None) -> list[dict]:
        """
        Execute all steps of a routine.
        Returns list of {label, skill, success, message} dicts.
        """
        skills = skills or self.skills
        routine = self._routines.get(routine_id)
        if not routine:
            return [{"label": "Error", "success": False, "message": f"Routine '{routine_id}' not found"}]

        results = []
        for step in routine.get("steps", []):
            skill_name = step.get("skill", "")
            params = step.get("parameters", {})
            label = step.get("label", skill_name)

            if skill_name not in skills:
                results.append({
                    "label": label,
                    "skill": skill_name,
                    "success": False,
                    "message": f"Skill '{skill_name}' not available",
                })
                continue

            try:
                result = skills[skill_name].execute(**params)
                results.append({
                    "label": label,
                    "skill": skill_name,
                    "success": result.success,
                    "message": result.message,
                })
            except Exception as e:
                results.append({
                    "label": label,
                    "skill": skill_name,
                    "success": False,
                    "message": str(e),
                })

        return results
