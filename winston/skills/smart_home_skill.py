"""
Smart Home & URL Skill - Open URLs, control smart home devices, manage bookmarks.
Supports HTTP API-based smart home platforms (Home Assistant, MQTT, etc.)
"""

import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path

from winston.skills.base import BaseSkill, SkillResult

logger = logging.getLogger("winston.skills.smart_home")


class SmartHomeSkill(BaseSkill):
    """Open URLs, manage bookmarks, and control smart home devices."""

    name = "smart_home"
    description = (
        "Open websites/URLs in the browser, manage bookmarks, and control smart home devices. "
        "Use this when the user asks to open a website, go to a URL, save a bookmark, "
        "or control lights/devices/smart home."
    )
    parameters = {
        "action": "Action: 'open_url' (open in browser), 'bookmark' (save bookmark), 'list_bookmarks' (show saved), 'device_control' (smart home), 'list_devices' (show devices)",
        "url": "(open_url/bookmark) The URL to open or bookmark",
        "name": "(bookmark/device_control) Name of bookmark or device",
        "device_action": "(device_control) What to do: 'on', 'off', 'toggle', 'brightness', 'color'",
        "value": "(device_control) Value for the action (e.g. brightness level 0-100)",
    }

    BOOKMARKS_FILE = Path.home() / ".winston" / "bookmarks.json"
    DEVICES_FILE = Path.home() / ".winston" / "smart_devices.json"

    # Quick aliases for common URLs
    URL_SHORTCUTS = {
        "google": "https://www.google.com",
        "youtube": "https://www.youtube.com",
        "gmail": "https://mail.google.com",
        "github": "https://github.com",
        "twitter": "https://twitter.com",
        "x": "https://x.com",
        "reddit": "https://www.reddit.com",
        "amazon": "https://www.amazon.de",
        "netflix": "https://www.netflix.com",
        "spotify": "https://open.spotify.com",
        "chatgpt": "https://chat.openai.com",
        "claude": "https://claude.ai",
        "stackoverflow": "https://stackoverflow.com",
        "linkedin": "https://www.linkedin.com",
        "instagram": "https://www.instagram.com",
        "whatsapp": "https://web.whatsapp.com",
        "maps": "https://maps.google.com",
        "drive": "https://drive.google.com",
        "docs": "https://docs.google.com",
        "translate": "https://translate.google.com",
        "wikipedia": "https://www.wikipedia.org",
        "twitch": "https://www.twitch.tv",
    }

    def __init__(self, config=None):
        super().__init__(config)
        self.BOOKMARKS_FILE.parent.mkdir(parents=True, exist_ok=True)

    def execute(self, **kwargs) -> SkillResult:
        action = kwargs.get("action", "open_url")

        actions = {
            "open_url": lambda: self._open_url(kwargs.get("url", "")),
            "bookmark": lambda: self._save_bookmark(
                kwargs.get("name", ""),
                kwargs.get("url", ""),
            ),
            "list_bookmarks": self._list_bookmarks,
            "device_control": lambda: self._device_control(
                kwargs.get("name", ""),
                kwargs.get("device_action", "toggle"),
                kwargs.get("value", ""),
            ),
            "list_devices": self._list_devices,
        }

        handler = actions.get(action)
        if handler:
            return handler()
        return SkillResult(success=False, message=f"Unknown smart home action: {action}")

    def _open_url(self, url: str) -> SkillResult:
        """Open a URL in the default browser."""
        if not url:
            return SkillResult(success=False, message="No URL provided.")

        # Check shortcuts
        url_lower = url.lower().strip()
        if url_lower in self.URL_SHORTCUTS:
            url = self.URL_SHORTCUTS[url_lower]
        elif not url.startswith(("http://", "https://")):
            # If it looks like a domain, add https://
            if "." in url and " " not in url:
                url = f"https://{url}"
            else:
                # Treat as search query
                import urllib.parse
                url = f"https://www.google.com/search?q={urllib.parse.quote(url)}"

        try:
            subprocess.Popen(["open", url])
            return SkillResult(success=True, message=f"Opening {url}")
        except Exception as e:
            return SkillResult(success=False, message=f"Failed to open URL: {e}")

    def _save_bookmark(self, name: str, url: str) -> SkillResult:
        """Save a bookmark."""
        if not name or not url:
            return SkillResult(success=False, message="Both name and URL are required for a bookmark.")

        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        bookmarks = self._load_bookmarks()
        bookmarks[name.lower()] = {
            "name": name,
            "url": url,
            "added": datetime.now().isoformat(),
        }
        self._save_bookmarks_file(bookmarks)

        return SkillResult(success=True, message=f"Bookmark saved: '{name}' -> {url}")

    def _list_bookmarks(self) -> SkillResult:
        """List all saved bookmarks."""
        bookmarks = self._load_bookmarks()
        if not bookmarks:
            return SkillResult(success=True, message="No bookmarks saved yet.")

        response = f"Bookmarks ({len(bookmarks)}):\n\n"
        for key, bm in bookmarks.items():
            response += f"  {bm['name']}: {bm['url']}\n"

        return SkillResult(success=True, message=response, speak=False)

    def _device_control(self, device_name: str, action: str, value: str = "") -> SkillResult:
        """Control a smart home device via HTTP API."""
        if not device_name:
            return SkillResult(success=False, message="No device name provided.")

        devices = self._load_devices()
        device = devices.get(device_name.lower())

        if not device:
            available = ", ".join(devices.keys()) if devices else "none configured"
            return SkillResult(
                success=False,
                message=f"Device '{device_name}' not found. Available: {available}. "
                        f"Configure devices in ~/.winston/smart_devices.json",
            )

        try:
            import httpx

            endpoint = device.get("endpoint", "")
            api_type = device.get("type", "http")

            if api_type == "home_assistant":
                return self._ha_control(device, action, value)

            # Generic HTTP API
            payload = {
                "action": action,
                "value": value,
                "device": device_name,
            }

            response = httpx.post(
                endpoint,
                json=payload,
                timeout=10,
                headers=device.get("headers", {}),
            )

            if response.status_code == 200:
                return SkillResult(
                    success=True,
                    message=f"Device '{device_name}' {action}: OK",
                )
            else:
                return SkillResult(
                    success=False,
                    message=f"Device '{device_name}' returned status {response.status_code}",
                )

        except ImportError:
            return SkillResult(success=False, message="httpx not installed for API calls.")
        except Exception as e:
            return SkillResult(success=False, message=f"Device control error: {e}")

    def _ha_control(self, device: dict, action: str, value: str) -> SkillResult:
        """Control device via Home Assistant API."""
        try:
            import httpx

            ha_url = device.get("ha_url", "http://localhost:8123")
            ha_token = device.get("ha_token", "")
            entity_id = device.get("entity_id", "")

            service_map = {
                "on": "turn_on",
                "off": "turn_off",
                "toggle": "toggle",
            }

            service = service_map.get(action, action)
            domain = entity_id.split(".")[0] if "." in entity_id else "light"

            payload = {"entity_id": entity_id}
            if action == "brightness" and value:
                payload["brightness"] = int(int(value) * 255 / 100)
                service = "turn_on"

            response = httpx.post(
                f"{ha_url}/api/services/{domain}/{service}",
                json=payload,
                headers={
                    "Authorization": f"Bearer {ha_token}",
                    "Content-Type": "application/json",
                },
                timeout=10,
            )

            if response.status_code == 200:
                return SkillResult(
                    success=True,
                    message=f"Home Assistant: {entity_id} {action}",
                )
            return SkillResult(
                success=False,
                message=f"Home Assistant error: {response.status_code}",
            )
        except Exception as e:
            return SkillResult(success=False, message=f"Home Assistant error: {e}")

    def _list_devices(self) -> SkillResult:
        """List configured smart home devices."""
        devices = self._load_devices()
        if not devices:
            return SkillResult(
                success=True,
                message="No smart home devices configured.\n\n"
                        "To add devices, create ~/.winston/smart_devices.json with:\n"
                        '{\n  "living room light": {\n    "type": "home_assistant",\n'
                        '    "ha_url": "http://homeassistant.local:8123",\n'
                        '    "ha_token": "your_long_lived_token",\n'
                        '    "entity_id": "light.living_room"\n  }\n}',
                speak=False,
            )

        response = f"Configured Devices ({len(devices)}):\n\n"
        for name, dev in devices.items():
            dev_type = dev.get("type", "http")
            entity = dev.get("entity_id", dev.get("endpoint", "N/A"))
            response += f"  {name}: [{dev_type}] {entity}\n"

        return SkillResult(success=True, message=response, speak=False)

    def _load_bookmarks(self) -> dict:
        if self.BOOKMARKS_FILE.exists():
            try:
                return json.loads(self.BOOKMARKS_FILE.read_text())
            except Exception:
                return {}
        return {}

    def _save_bookmarks_file(self, bookmarks: dict):
        self.BOOKMARKS_FILE.write_text(json.dumps(bookmarks, indent=2))

    def _load_devices(self) -> dict:
        if self.DEVICES_FILE.exists():
            try:
                return json.loads(self.DEVICES_FILE.read_text())
            except Exception:
                return {}
        return {}
