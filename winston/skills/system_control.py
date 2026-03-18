"""
System Control Skill - Execute system commands, manage files, check system info.
"""

import logging
import os
import platform
import shutil
import subprocess
from datetime import datetime

from winston.skills.base import BaseSkill, SkillResult

logger = logging.getLogger("winston.skills.system_control")


class SystemControlSkill(BaseSkill):
    """Control system operations and get system information."""

    name = "system_control"
    description = (
        "Control system operations: open applications, get system info, "
        "check time/date, manage files, run safe system commands. "
        "Use this for system-level operations."
    )
    parameters = {
        "action": "Action: 'time', 'date', 'system_info', 'open_app', 'run_command', 'weather'",
        "app_name": "(open_app) Name of the application to open",
        "command": "(run_command) The command to run (limited to safe commands)",
    }

    # Commands that are safe to run
    SAFE_COMMANDS = {
        "ls", "pwd", "whoami", "date", "cal", "uptime", "df", "du",
        "uname", "hostname", "which", "echo", "cat", "head", "tail",
        "wc", "sort", "grep", "find", "top", "ps", "free",
    }

    def execute(self, **kwargs) -> SkillResult:
        """Execute system control action."""
        action = kwargs.get("action", "time")

        actions = {
            "time": self._get_time,
            "date": self._get_date,
            "system_info": self._get_system_info,
            "open_app": lambda: self._open_app(kwargs.get("app_name", "")),
            "run_command": lambda: self._run_command(kwargs.get("command", "")),
        }

        handler = actions.get(action)
        if handler:
            return handler()
        return SkillResult(
            success=False,
            message=f"Unknown system action: {action}",
        )

    def _get_time(self) -> SkillResult:
        """Get current time."""
        now = datetime.now()
        time_str = now.strftime("%I:%M %p")
        return SkillResult(
            success=True,
            message=f"The current time is {time_str}.",
        )

    def _get_date(self) -> SkillResult:
        """Get current date."""
        now = datetime.now()
        date_str = now.strftime("%A, %B %d, %Y")
        return SkillResult(
            success=True,
            message=f"Today is {date_str}.",
        )

    def _get_system_info(self) -> SkillResult:
        """Get system information."""
        info = {
            "OS": f"{platform.system()} {platform.release()}",
            "Machine": platform.machine(),
            "Processor": platform.processor(),
            "Python": platform.python_version(),
            "Hostname": platform.node(),
            "User": os.getenv("USER", os.getenv("USERNAME", "unknown")),
        }

        # Get memory info
        try:
            import psutil
            mem = psutil.virtual_memory()
            info["RAM"] = f"{mem.total / (1024**3):.1f} GB ({mem.percent}% used)"
            info["CPU Cores"] = str(psutil.cpu_count())
            info["CPU Usage"] = f"{psutil.cpu_percent()}%"
            disk = psutil.disk_usage("/")
            info["Disk"] = f"{disk.total / (1024**3):.1f} GB ({disk.percent}% used)"
        except ImportError:
            pass

        response = "System Information:\n"
        for key, value in info.items():
            response += f"  {key}: {value}\n"

        return SkillResult(success=True, message=response)

    # Common app name aliases (what users say → actual macOS app name)
    APP_ALIASES = {
        "chrome": "Google Chrome",
        "google chrome": "Google Chrome",
        "firefox": "Firefox",
        "safari": "Safari",
        "terminal": "Terminal",
        "iterm": "iTerm",
        "iterm2": "iTerm",
        "vscode": "Visual Studio Code",
        "vs code": "Visual Studio Code",
        "code": "Visual Studio Code",
        "finder": "Finder",
        "mail": "Mail",
        "messages": "Messages",
        "imessage": "Messages",
        "notes": "Notes",
        "calendar": "Calendar",
        "music": "Music",
        "spotify": "Spotify",
        "slack": "Slack",
        "discord": "Discord",
        "zoom": "zoom.us",
        "teams": "Microsoft Teams",
        "word": "Microsoft Word",
        "excel": "Microsoft Excel",
        "powerpoint": "Microsoft PowerPoint",
        "outlook": "Microsoft Outlook",
        "photos": "Photos",
        "preview": "Preview",
        "activity monitor": "Activity Monitor",
        "system preferences": "System Preferences",
        "system settings": "System Settings",
        "app store": "App Store",
        "facetime": "FaceTime",
        "maps": "Maps",
        "weather": "Weather",
        "clock": "Clock",
        "calculator": "Calculator",
        "whatsapp": "WhatsApp",
        "telegram": "Telegram",
    }

    def _open_app(self, app_name: str) -> SkillResult:
        """Open an application."""
        if not app_name:
            return SkillResult(success=False, message="No application name provided.")

        # Resolve common aliases
        resolved = self.APP_ALIASES.get(app_name.lower(), app_name)

        system = platform.system()
        try:
            if system == "Darwin":  # macOS
                subprocess.Popen(["open", "-a", resolved])
            elif system == "Linux":
                subprocess.Popen([resolved])
            elif system == "Windows":
                os.startfile(resolved)
            else:
                return SkillResult(
                    success=False,
                    message=f"Cannot open apps on {system}.",
                )

            return SkillResult(
                success=True,
                message=f"Opening {resolved}.",
            )
        except Exception as e:
            return SkillResult(
                success=False,
                message=f"Failed to open {resolved}: {str(e)}",
            )

    # Characters that indicate shell injection attempts
    SHELL_INJECTION_CHARS = {";", "|", "&", "`", "$", "(", ")", "{", "}", "<", ">", "!", "\\"}

    def _run_command(self, command: str) -> SkillResult:
        """Run a safe system command (no shell injection possible)."""
        if not command:
            return SkillResult(success=False, message="No command provided.")

        # Parse into arguments safely (respects quoting)
        import shlex
        try:
            cmd_parts = shlex.split(command)
        except ValueError as e:
            return SkillResult(success=False, message=f"Invalid command syntax: {e}")

        if not cmd_parts:
            return SkillResult(success=False, message="Empty command.")

        base_cmd = cmd_parts[0]

        # Security check 1 - only allow whitelisted commands
        if base_cmd not in self.SAFE_COMMANDS:
            return SkillResult(
                success=False,
                message=(
                    f"Command '{base_cmd}' is not in the allowed list for security reasons. "
                    f"Allowed commands: {', '.join(sorted(self.SAFE_COMMANDS))}"
                ),
            )

        # Security check 2 - reject shell metacharacters in arguments
        for arg in cmd_parts[1:]:
            if any(c in arg for c in self.SHELL_INJECTION_CHARS):
                return SkillResult(
                    success=False,
                    message=(
                        f"Argument '{arg}' contains disallowed characters. "
                        "Shell operators (;, |, &, $, etc.) are not permitted."
                    ),
                )

        # Security check 3 - block path traversal
        for arg in cmd_parts[1:]:
            if ".." in arg or arg.startswith("/etc") or arg.startswith("/sys"):
                return SkillResult(
                    success=False,
                    message=f"Path '{arg}' is not allowed for security reasons.",
                )

        try:
            # shell=False prevents any shell interpretation
            result = subprocess.run(
                cmd_parts,
                shell=False,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=os.path.expanduser("~"),  # Run from home dir, not system root
            )
            output = result.stdout or result.stderr
            return SkillResult(
                success=result.returncode == 0,
                message=f"Command output:\n{output[:1000]}",
                data={"output": output, "returncode": result.returncode},
                speak=False,
            )
        except subprocess.TimeoutExpired:
            return SkillResult(success=False, message="Command timed out after 30 seconds.")
        except Exception as e:
            return SkillResult(success=False, message=f"Command failed: {str(e)}")
