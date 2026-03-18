"""
Notes Skill - Create, read, and manage text notes/reminders.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from winston.skills.base import BaseSkill, SkillResult

logger = logging.getLogger("winston.skills.notes")

NOTES_DIR = Path("~/.winston/notes").expanduser()


class NotesSkill(BaseSkill):
    """Create and manage notes and reminders."""

    name = "notes"
    description = (
        "Create, read, list, and delete notes or reminders. "
        "Use this when the user wants to save a note, create a reminder, "
        "or manage their notes."
    )
    parameters = {
        "action": "Action: 'create', 'list', 'read', 'delete', 'search'",
        "title": "(create) Title of the note",
        "content": "(create) Content of the note",
        "note_id": "(read/delete) ID or title of the note",
        "query": "(search) Search query",
    }

    def __init__(self, config=None):
        super().__init__(config)
        NOTES_DIR.mkdir(parents=True, exist_ok=True)
        self._index_file = NOTES_DIR / "index.json"
        self._load_index()

    def _load_index(self):
        """Load notes index."""
        if self._index_file.exists():
            with open(self._index_file) as f:
                self._index = json.load(f)
        else:
            self._index = {"notes": []}

    def _save_index(self):
        """Save notes index."""
        with open(self._index_file, "w") as f:
            json.dump(self._index, f, indent=2)

    def execute(self, **kwargs) -> SkillResult:
        """Execute notes action."""
        action = kwargs.get("action", "list")

        actions = {
            "create": lambda: self._create_note(
                kwargs.get("title", ""), kwargs.get("content", "")
            ),
            "list": self._list_notes,
            "read": lambda: self._read_note(kwargs.get("note_id", "")),
            "delete": lambda: self._delete_note(kwargs.get("note_id", "")),
            "search": lambda: self._search_notes(kwargs.get("query", "")),
        }

        handler = actions.get(action)
        if handler:
            return handler()
        return SkillResult(success=False, message=f"Unknown notes action: {action}")

    def _create_note(self, title: str, content: str) -> SkillResult:
        """Create a new note."""
        if not title:
            title = f"Note {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        if not content:
            return SkillResult(success=False, message="No content provided for the note.")

        timestamp = datetime.now().isoformat()
        note_id = f"note_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Save note file
        note_file = NOTES_DIR / f"{note_id}.md"
        with open(note_file, "w") as f:
            f.write(f"# {title}\n\n")
            f.write(f"*Created: {timestamp}*\n\n")
            f.write(content)

        # Update index
        self._index["notes"].append({
            "id": note_id,
            "title": title,
            "created": timestamp,
            "file": str(note_file),
        })
        self._save_index()

        return SkillResult(
            success=True,
            message=f"Note '{title}' has been saved.",
        )

    def _list_notes(self) -> SkillResult:
        """List all notes."""
        if not self._index["notes"]:
            return SkillResult(success=True, message="You have no saved notes.")

        response = f"You have {len(self._index['notes'])} notes:\n\n"
        for i, note in enumerate(self._index["notes"], 1):
            response += f"{i}. {note['title']} (created: {note['created'][:10]})\n"

        return SkillResult(success=True, message=response, data=self._index["notes"])

    def _read_note(self, note_id: str) -> SkillResult:
        """Read a specific note."""
        # Find by ID or title
        note = self._find_note(note_id)
        if not note:
            return SkillResult(success=False, message=f"Note '{note_id}' not found.")

        note_file = Path(note["file"])
        if not note_file.exists():
            return SkillResult(success=False, message="Note file not found on disk.")

        content = note_file.read_text()
        return SkillResult(success=True, message=content, speak=False)

    def _delete_note(self, note_id: str) -> SkillResult:
        """Delete a note."""
        note = self._find_note(note_id)
        if not note:
            return SkillResult(success=False, message=f"Note '{note_id}' not found.")

        # Delete file
        note_file = Path(note["file"])
        note_file.unlink(missing_ok=True)

        # Remove from index
        self._index["notes"] = [n for n in self._index["notes"] if n["id"] != note["id"]]
        self._save_index()

        return SkillResult(success=True, message=f"Note '{note['title']}' has been deleted.")

    def _search_notes(self, query: str) -> SkillResult:
        """Search notes by title or content."""
        if not query:
            return SkillResult(success=False, message="No search query provided.")

        results = []
        for note in self._index["notes"]:
            # Search title
            if query.lower() in note["title"].lower():
                results.append(note)
                continue

            # Search content
            note_file = Path(note["file"])
            if note_file.exists():
                content = note_file.read_text()
                if query.lower() in content.lower():
                    results.append(note)

        if not results:
            return SkillResult(success=True, message=f"No notes matching '{query}'.")

        response = f"Found {len(results)} notes matching '{query}':\n\n"
        for i, note in enumerate(results, 1):
            response += f"{i}. {note['title']} (created: {note['created'][:10]})\n"

        return SkillResult(success=True, message=response, data=results)

    def _find_note(self, identifier: str):
        """Find a note by ID, title, or index number."""
        # Try as index number
        try:
            idx = int(identifier) - 1
            if 0 <= idx < len(self._index["notes"]):
                return self._index["notes"][idx]
        except ValueError:
            pass

        # Try as ID or title
        for note in self._index["notes"]:
            if note["id"] == identifier or note["title"].lower() == identifier.lower():
                return note
        return None
