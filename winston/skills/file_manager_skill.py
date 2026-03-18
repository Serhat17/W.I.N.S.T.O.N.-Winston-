"""
File Manager Skill - Search, organize, and manage files on the system.
"""

import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

from winston.skills.base import BaseSkill, SkillResult

logger = logging.getLogger("winston.skills.file_manager")


class FileManagerSkill(BaseSkill):
    """Search, read, create, and organize files."""

    name = "file_manager"
    description = (
        "Search for files, list directory contents, read file content, create text files, "
        "and get file information. Use this when the user asks to find files, "
        "look for documents, read a file, create a file, or organize files."
    )
    parameters = {
        "action": "Action: 'search' (find files), 'list' (list directory), 'read' (read file), 'create' (create file), 'info' (file details), 'size' (folder size)",
        "path": "(list/read/info/size) File or directory path",
        "query": "(search) Filename pattern to search for (e.g. '*.pdf', 'report')",
        "search_dir": "(search) Directory to search in (default: home)",
        "content": "(create) Content for new file",
        "filename": "(create) Name for new file",
    }

    # Directories that should never be modified
    PROTECTED_DIRS = {"/", "/System", "/Library", "/usr", "/bin", "/sbin", "/etc", "/var", "/private"}

    # Maximum file size to read (5MB)
    MAX_READ_SIZE = 5 * 1024 * 1024

    def execute(self, **kwargs) -> SkillResult:
        action = kwargs.get("action", "list")

        actions = {
            "search": lambda: self._search(
                kwargs.get("query", ""),
                kwargs.get("search_dir", str(Path.home())),
            ),
            "list": lambda: self._list_dir(kwargs.get("path", str(Path.home()))),
            "read": lambda: self._read_file(kwargs.get("path", "")),
            "create": lambda: self._create_file(
                kwargs.get("filename", ""),
                kwargs.get("content", ""),
                kwargs.get("path", str(Path.home() / "Documents")),
            ),
            "info": lambda: self._file_info(kwargs.get("path", "")),
            "size": lambda: self._folder_size(kwargs.get("path", str(Path.home()))),
        }

        handler = actions.get(action)
        if handler:
            return handler()
        return SkillResult(success=False, message=f"Unknown file action: {action}")

    def _search(self, query: str, search_dir: str) -> SkillResult:
        """Search for files matching a pattern."""
        if not query:
            return SkillResult(success=False, message="No search query provided.")

        search_path = Path(search_dir).expanduser()
        if not search_path.exists():
            return SkillResult(success=False, message=f"Directory not found: {search_dir}")

        try:
            # Use find command for speed (respects permissions)
            import subprocess
            result = subprocess.run(
                ["find", str(search_path), "-maxdepth", "5",
                 "-iname", f"*{query}*", "-not", "-path", "*/.*"],
                capture_output=True,
                text=True,
                timeout=15,
            )

            files = [f for f in result.stdout.strip().split("\n") if f.strip()][:20]

            if not files:
                return SkillResult(success=True, message=f"No files matching '{query}' found in {search_dir}.")

            response = f"Found {len(files)} file(s) matching '{query}':\n\n"
            for f in files:
                p = Path(f)
                size = self._human_size(p.stat().st_size) if p.is_file() else "dir"
                response += f"  {size:>8s}  {f}\n"

            return SkillResult(success=True, message=response, speak=False)
        except Exception as e:
            return SkillResult(success=False, message=f"Search error: {e}")

    def _list_dir(self, path: str) -> SkillResult:
        """List contents of a directory."""
        dir_path = Path(path).expanduser()
        if not dir_path.exists():
            return SkillResult(success=False, message=f"Directory not found: {path}")
        if not dir_path.is_dir():
            return SkillResult(success=False, message=f"Not a directory: {path}")

        try:
            entries = sorted(dir_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
            # Limit to 50 entries
            entries = entries[:50]

            response = f"Contents of {dir_path}:\n\n"
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    response += f"  [DIR]    {entry.name}/\n"
                else:
                    size = self._human_size(entry.stat().st_size)
                    response += f"  {size:>8s}  {entry.name}\n"

            return SkillResult(success=True, message=response, speak=False)
        except PermissionError:
            return SkillResult(success=False, message=f"Permission denied: {path}")
        except Exception as e:
            return SkillResult(success=False, message=f"Error listing directory: {e}")

    def _read_file(self, path: str) -> SkillResult:
        """Read contents of a text file."""
        if not path:
            return SkillResult(success=False, message="No file path provided.")

        file_path = Path(path).expanduser()
        if not file_path.exists():
            return SkillResult(success=False, message=f"File not found: {path}")
        if not file_path.is_file():
            return SkillResult(success=False, message=f"Not a file: {path}")

        # Check file size
        size = file_path.stat().st_size
        if size > self.MAX_READ_SIZE:
            return SkillResult(
                success=False,
                message=f"File too large ({self._human_size(size)}). Maximum is 5MB.",
            )

        try:
            content = file_path.read_text(errors="replace")
            # Truncate very long files
            if len(content) > 10000:
                content = content[:10000] + f"\n\n... [truncated, {self._human_size(size)} total]"

            return SkillResult(
                success=True,
                message=f"Contents of {file_path.name}:\n\n{content}",
                data={"path": str(file_path), "content": content},
                speak=False,
            )
        except UnicodeDecodeError:
            return SkillResult(success=False, message=f"Cannot read binary file: {path}")
        except Exception as e:
            return SkillResult(success=False, message=f"Error reading file: {e}")

    def _create_file(self, filename: str, content: str, path: str) -> SkillResult:
        """Create a new text file."""
        if not filename:
            return SkillResult(success=False, message="No filename provided.")
        if not content:
            return SkillResult(success=False, message="No content provided.")

        # Safety: only allow creation in user-writable directories
        target_dir = Path(path).expanduser()
        resolved = target_dir.resolve()
        home = Path.home().resolve()

        if not str(resolved).startswith(str(home)):
            return SkillResult(success=False, message="Can only create files in your home directory.")

        target_dir.mkdir(parents=True, exist_ok=True)
        filepath = target_dir / filename

        if filepath.exists():
            return SkillResult(success=False, message=f"File already exists: {filepath}")

        try:
            filepath.write_text(content)
            return SkillResult(
                success=True,
                message=f"Created file: {filepath}",
                data={"path": str(filepath)},
            )
        except Exception as e:
            return SkillResult(success=False, message=f"Error creating file: {e}")

    def _file_info(self, path: str) -> SkillResult:
        """Get detailed information about a file or directory."""
        if not path:
            return SkillResult(success=False, message="No path provided.")

        file_path = Path(path).expanduser()
        if not file_path.exists():
            return SkillResult(success=False, message=f"Path not found: {path}")

        stat = file_path.stat()
        info = (
            f"Name: {file_path.name}\n"
            f"Type: {'Directory' if file_path.is_dir() else 'File'}\n"
            f"Size: {self._human_size(stat.st_size)}\n"
            f"Location: {file_path.parent}\n"
            f"Created: {datetime.fromtimestamp(stat.st_birthtime).strftime('%Y-%m-%d %H:%M')}\n"
            f"Modified: {datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')}\n"
            f"Permissions: {oct(stat.st_mode)[-3:]}\n"
        )

        if file_path.is_file():
            info += f"Extension: {file_path.suffix or 'none'}\n"

        return SkillResult(success=True, message=info, speak=False)

    def _folder_size(self, path: str) -> SkillResult:
        """Get total size of a folder."""
        dir_path = Path(path).expanduser()
        if not dir_path.exists() or not dir_path.is_dir():
            return SkillResult(success=False, message=f"Directory not found: {path}")

        try:
            import subprocess
            result = subprocess.run(
                ["du", "-sh", str(dir_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            size = result.stdout.strip().split("\t")[0] if result.stdout else "unknown"
            return SkillResult(success=True, message=f"Size of {dir_path}: {size}")
        except Exception as e:
            return SkillResult(success=False, message=f"Error calculating size: {e}")

    @staticmethod
    def _human_size(size_bytes: int) -> str:
        """Convert bytes to human readable."""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size_bytes < 1024:
                return f"{size_bytes:.1f}{unit}" if unit != "B" else f"{size_bytes}{unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f}PB"
