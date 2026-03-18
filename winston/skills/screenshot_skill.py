"""
Screenshot & Vision Skill - Capture screen and analyze images with AI.
Uses macOS screencapture + Ollama vision models for image understanding.
"""

import base64
import logging
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from winston.skills.base import BaseSkill, SkillResult

logger = logging.getLogger("winston.skills.screenshot")


class ScreenshotSkill(BaseSkill):
    """Take screenshots and analyze images with vision AI."""

    name = "desktop_screenshot"
    description = (
        "Take a physical screenshot of the computer desktop/monitor and optionally analyze it with AI vision. "
        "Use this for capturing local MacOS windows or the entire desktop display. "
        "CRITICAL: NEVER use this skill if the user asks for a screenshot of a WEBSITE, URL, OR WEB BROWSER. "
        "If the user wants a screenshot of a website (e.g. apple.com, google.com), YOU MUST USE the 'browser' skill with action='screenshot_page' instead."
    )
    parameters = {
        "action": "Action: 'capture' (take screenshot), 'analyze' (capture + describe with AI), 'read_text' (capture + extract text)",
        "prompt": "(analyze) Optional prompt for what to look for in the screenshot",
    }

    SCREENSHOT_DIR = Path.home() / ".winston" / "screenshots"

    def __init__(self, config=None):
        super().__init__(config)
        self.SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    def execute(self, **kwargs) -> SkillResult:
        action = kwargs.get("action", "capture")
        prompt = kwargs.get("prompt", "")

        actions = {
            "capture": lambda: self._capture(),
            "analyze": lambda: self._analyze(prompt),
            "read_text": lambda: self._read_text(),
        }

        handler = actions.get(action)
        if handler:
            return handler()
        return SkillResult(success=False, message=f"Unknown screenshot action: {action}")

    def _capture(self) -> SkillResult:
        """Take a screenshot and save it."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = self.SCREENSHOT_DIR / f"screenshot_{timestamp}.png"

        try:
            # macOS screencapture (-x = no sound)
            subprocess.run(
                ["screencapture", "-x", str(filepath)],
                check=True,
                timeout=10,
            )

            if filepath.exists():
                return SkillResult(
                    success=True,
                    message=f"Screenshot saved to {filepath}",
                    data={"path": str(filepath)},
                )
            return SkillResult(success=False, message="Screenshot capture failed.")
        except FileNotFoundError:
            return SkillResult(
                success=False,
                message="screencapture not available (macOS only).",
            )
        except Exception as e:
            return SkillResult(success=False, message=f"Screenshot error: {e}")

    def _analyze(self, prompt: str = "") -> SkillResult:
        """Take a screenshot and analyze it with Ollama vision."""
        capture_result = self._capture()
        if not capture_result.success:
            return capture_result

        filepath = capture_result.data["path"]
        analysis_prompt = prompt or "Describe what you see on this screen in detail. What applications are open? What is the user doing?"

        try:
            import httpx

            # Read and encode the image
            with open(filepath, "rb") as f:
                image_b64 = base64.b64encode(f.read()).decode("utf-8")

            # Use Ollama with a vision-capable model
            host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
            response = httpx.post(
                f"{host}/api/generate",
                json={
                    "model": "llava:7b",  # Vision model
                    "prompt": analysis_prompt,
                    "images": [image_b64],
                    "stream": False,
                },
                timeout=120.0,
            )
            response.raise_for_status()
            analysis = response.json().get("response", "Could not analyze the image.")

            return SkillResult(
                success=True,
                message=f"Screenshot saved to {filepath}\n\nAnalysis: {analysis}",
                data={"path": filepath, "analysis": analysis},
                speak=False,
            )
        except Exception as e:
            logger.error(f"Vision analysis error: {e}")
            return SkillResult(
                success=True,
                message=f"Screenshot saved to {filepath}, but vision analysis failed: {e}. "
                        f"You may need to pull a vision model: ollama pull llava:7b",
                data={"path": filepath},
            )

    def _read_text(self) -> SkillResult:
        """Take a screenshot and attempt to read text from it."""
        return self._analyze(
            prompt="Read and extract all visible text from this screenshot. "
                   "Return the text content organized by sections or areas of the screen."
        )
