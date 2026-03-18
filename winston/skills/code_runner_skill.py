"""
Code Runner Skill - Safely execute Python code snippets.
Provides a sandboxed environment for calculations, data processing, and scripting.
"""

import io
import logging
import sys
import traceback
from contextlib import redirect_stdout, redirect_stderr

from winston.skills.base import BaseSkill, SkillResult

logger = logging.getLogger("winston.skills.code_runner")

# Imports that are allowed in the sandbox
SAFE_MODULES = {
    "math", "statistics", "random", "datetime", "json", "re", "collections",
    "itertools", "functools", "operator", "string", "textwrap", "unicodedata",
    "decimal", "fractions", "hashlib", "base64", "urllib.parse", "html",
    "csv", "pprint", "copy", "enum", "dataclasses", "typing",
}

# Builtins that are BLOCKED
BLOCKED_BUILTINS = {
    "exec", "eval", "compile", "__import__", "globals", "locals",
    "breakpoint", "exit", "quit",
}

# Patterns that are never allowed in code
BLOCKED_PATTERNS = [
    "subprocess", "os.system", "os.popen", "os.exec",
    "shutil.rmtree", "shutil.move",
    "open(", "pathlib",  # file I/O blocked
    "__class__", "__subclasses__", "__bases__",
    "importlib", "ctypes", "socket",
    "requests", "httpx", "urllib.request",
]


class CodeRunnerSkill(BaseSkill):
    """Execute Python code safely in a sandboxed environment."""

    name = "code_runner"
    description = (
        "Execute Python code snippets safely. Supports math, data processing, "
        "string manipulation, and general calculations. Use this when the user "
        "asks to calculate something, run code, write a script, or process data."
    )
    parameters = {
        "action": "Action: 'run' (execute code), 'explain' (explain code without running)",
        "code": "The Python code to execute",
    }

    def execute(self, **kwargs) -> SkillResult:
        action = kwargs.get("action", "run")
        code = kwargs.get("code", "")

        if action == "explain":
            return SkillResult(
                success=True,
                message=f"Code to explain:\n```python\n{code}\n```\n"
                        f"(Ask me to explain this code in natural language)",
            )

        if not code:
            return SkillResult(success=False, message="No code provided to execute.")

        return self._run_code(code)

    def _run_code(self, code: str) -> SkillResult:
        """Execute code in a restricted sandbox."""
        # Security checks
        for pattern in BLOCKED_PATTERNS:
            if pattern in code:
                return SkillResult(
                    success=False,
                    message=f"Blocked: Code contains restricted operation '{pattern}'",
                )

        # Build restricted builtins
        safe_builtins = {
            k: v for k, v in __builtins__.__dict__.items()
            if k not in BLOCKED_BUILTINS
        } if isinstance(__builtins__, type(sys)) else {
            k: v for k, v in __builtins__.items()
            if k not in BLOCKED_BUILTINS
        }

        # Allow importing safe modules only
        def safe_import(name, *args, **kwargs):
            top_level = name.split(".")[0]
            if top_level not in SAFE_MODULES:
                raise ImportError(f"Import of '{name}' is not allowed in sandbox")
            return __import__(name, *args, **kwargs)

        safe_builtins["__import__"] = safe_import

        # Execution namespace
        namespace = {"__builtins__": safe_builtins}

        # Pre-import common modules
        try:
            import math, datetime, json, re, random, statistics, collections
            namespace.update({
                "math": math, "datetime": datetime, "json": json,
                "re": re, "random": random, "statistics": statistics,
                "collections": collections,
            })
        except Exception:
            pass

        # Capture output
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()

        try:
            with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                # Use exec for statements, but try eval first for expressions
                try:
                    result = eval(code, namespace)
                    if result is not None:
                        print(repr(result))
                except SyntaxError:
                    exec(code, namespace)

            stdout = stdout_capture.getvalue()
            stderr = stderr_capture.getvalue()

            output = ""
            if stdout:
                output += stdout.rstrip()
            if stderr:
                output += f"\n[stderr] {stderr.rstrip()}"

            if not output:
                output = "(Code executed successfully, no output)"

            # Truncate very long output
            if len(output) > 3000:
                output = output[:3000] + f"\n... (output truncated, {len(output)} chars total)"

            return SkillResult(
                success=True,
                message=f"Code output:\n```\n{output}\n```",
            )

        except Exception as e:
            error_msg = traceback.format_exc()
            # Keep just the last few lines of traceback
            lines = error_msg.strip().split("\n")
            short_error = "\n".join(lines[-3:]) if len(lines) > 3 else error_msg

            return SkillResult(
                success=False,
                message=f"Code error:\n```\n{short_error}\n```",
            )
