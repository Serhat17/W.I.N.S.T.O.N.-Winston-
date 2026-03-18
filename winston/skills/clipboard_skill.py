"""
Clipboard Manager Skill - Smart clipboard with history and text transformation.
Uses macOS pbcopy/pbpaste for clipboard access.
"""

import json
import logging
import subprocess
from collections import deque
from datetime import datetime
from pathlib import Path

from winston.skills.base import BaseSkill, SkillResult

logger = logging.getLogger("winston.skills.clipboard")


class ClipboardSkill(BaseSkill):
    """Manage clipboard content and transform text."""

    name = "clipboard"
    description = (
        "Read, copy, and manage clipboard content. Save clipboard history, "
        "transform text (uppercase, lowercase, reverse, sort lines). "
        "Use this when the user asks about clipboard content, wants to copy text, "
        "or transform/manipulate text."
    )
    parameters = {
        "action": "Action: 'read' (get clipboard), 'copy' (set clipboard text), 'history' (show clipboard history), 'transform' (modify clipboard text)",
        "text": "(copy) Text to copy to clipboard",
        "transform_type": "(transform) Type: 'uppercase', 'lowercase', 'title', 'reverse', 'sort_lines', 'remove_duplicates', 'count_words', 'trim'",
    }

    # In-memory clipboard history (last 20 items)
    _history: deque = deque(maxlen=20)

    def execute(self, **kwargs) -> SkillResult:
        action = kwargs.get("action", "read")

        actions = {
            "read": self._read,
            "copy": lambda: self._copy(kwargs.get("text", "")),
            "history": self._show_history,
            "transform": lambda: self._transform(kwargs.get("transform_type", "uppercase")),
        }

        handler = actions.get(action)
        if handler:
            return handler()
        return SkillResult(success=False, message=f"Unknown clipboard action: {action}")

    def _read(self) -> SkillResult:
        """Read current clipboard content."""
        try:
            result = subprocess.run(
                ["pbpaste"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            content = result.stdout

            if not content:
                return SkillResult(success=True, message="Clipboard is empty.")

            # Save to history
            self._add_to_history(content)

            # Truncate for display
            display = content[:2000]
            if len(content) > 2000:
                display += f"\n\n... [{len(content)} characters total]"

            return SkillResult(
                success=True,
                message=f"Clipboard content:\n\n{display}",
                data={"content": content},
                speak=False,
            )
        except FileNotFoundError:
            return SkillResult(success=False, message="pbpaste not available (macOS only).")
        except Exception as e:
            return SkillResult(success=False, message=f"Clipboard read error: {e}")

    def _copy(self, text: str) -> SkillResult:
        """Copy text to clipboard."""
        if not text:
            return SkillResult(success=False, message="No text provided to copy.")

        try:
            proc = subprocess.Popen(
                ["pbcopy"],
                stdin=subprocess.PIPE,
                timeout=5,
            )
            proc.communicate(input=text.encode("utf-8"))

            self._add_to_history(text)
            preview = text[:100] + "..." if len(text) > 100 else text
            return SkillResult(
                success=True,
                message=f"Copied to clipboard: {preview}",
            )
        except FileNotFoundError:
            return SkillResult(success=False, message="pbcopy not available (macOS only).")
        except Exception as e:
            return SkillResult(success=False, message=f"Clipboard copy error: {e}")

    def _show_history(self) -> SkillResult:
        """Show clipboard history."""
        if not self._history:
            return SkillResult(success=True, message="No clipboard history yet.")

        response = f"Clipboard History ({len(self._history)} items):\n\n"
        for i, entry in enumerate(reversed(list(self._history)), 1):
            preview = entry["text"][:80].replace("\n", " ")
            if len(entry["text"]) > 80:
                preview += "..."
            response += f"{i}. [{entry['time']}] {preview}\n"

        return SkillResult(success=True, message=response, speak=False)

    def _transform(self, transform_type: str) -> SkillResult:
        """Transform current clipboard content."""
        # Read current clipboard
        read_result = self._read()
        if not read_result.success or not read_result.data:
            return SkillResult(success=False, message="Clipboard is empty, nothing to transform.")

        content = read_result.data["content"]

        transforms = {
            "uppercase": lambda t: t.upper(),
            "lowercase": lambda t: t.lower(),
            "title": lambda t: t.title(),
            "reverse": lambda t: t[::-1],
            "sort_lines": lambda t: "\n".join(sorted(t.split("\n"))),
            "remove_duplicates": lambda t: "\n".join(dict.fromkeys(t.split("\n"))),
            "count_words": lambda t: None,  # Special case
            "trim": lambda t: "\n".join(line.strip() for line in t.split("\n")),
        }

        if transform_type == "count_words":
            words = len(content.split())
            chars = len(content)
            lines = content.count("\n") + 1
            return SkillResult(
                success=True,
                message=f"Clipboard stats: {words} words, {chars} characters, {lines} lines.",
            )

        transformer = transforms.get(transform_type)
        if not transformer:
            return SkillResult(
                success=False,
                message=f"Unknown transform: {transform_type}. "
                        f"Available: {', '.join(transforms.keys())}",
            )

        transformed = transformer(content)

        # Copy result back to clipboard
        self._copy(transformed)

        preview = transformed[:200]
        if len(transformed) > 200:
            preview += "..."

        return SkillResult(
            success=True,
            message=f"Transformed ({transform_type}) and copied to clipboard:\n\n{preview}",
            speak=False,
        )

    @classmethod
    def _add_to_history(cls, text: str):
        """Add text to clipboard history."""
        cls._history.append({
            "text": text,
            "time": datetime.now().strftime("%H:%M:%S"),
        })
