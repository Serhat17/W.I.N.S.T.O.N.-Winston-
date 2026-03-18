"""
Auto-manage the WAHA Docker container for WhatsApp integration.

Handles checking Docker availability, starting/creating the WAHA container,
health checks, and persistent volumes so users never need to touch Docker.
"""

import asyncio
import json
import logging
import secrets
import subprocess
from pathlib import Path

import httpx

logger = logging.getLogger("winston.docker")


class WAHAManager:
    """Manages the WAHA (WhatsApp HTTP API) Docker container."""

    CONTAINER_NAME = "waha"
    IMAGE = "devlikeapro/waha"
    VOLUME_NAME = "waha_sessions"
    HEALTH_TIMEOUT = 60  # seconds to wait for WAHA to become healthy
    HEALTH_INTERVAL = 2  # seconds between health checks

    @classmethod
    async def ensure_running(cls, config: dict) -> bool:
        """
        Ensure the WAHA container is running and healthy.

        Args:
            config: WhatsApp channel config dict with waha_url, waha_api_key, etc.

        Returns:
            True if WAHA is ready, False if it could not be started.
        """
        if not config.get("auto_start_docker", True):
            logger.info("WAHA auto-start disabled, assuming it's running")
            return True

        waha_url = config.get("waha_url", "http://localhost:3000").rstrip("/")
        api_key = config.get("waha_api_key", "")

        # Quick check — maybe WAHA is already running
        if await cls._is_healthy(waha_url, api_key):
            logger.info("WAHA is already running and healthy")
            return True

        # Check Docker is available
        if not cls._docker_available():
            logger.error(
                "Docker is not installed or not running. "
                "Install Docker Desktop from https://www.docker.com/products/docker-desktop/ "
                "to use WhatsApp integration."
            )
            return False

        # Generate API key if not set
        if not api_key:
            api_key = secrets.token_hex(16)
            config["waha_api_key"] = api_key
            cls._save_api_key(api_key)
            logger.info("Generated new WAHA API key (saved to settings.yaml)")

        # Parse port from waha_url
        port = cls._parse_port(waha_url)

        # Check container state
        state = cls._get_container_state()

        if state == "running":
            # Container is running but not healthy yet — wait for it
            logger.info("WAHA container is running, waiting for it to become healthy...")
        elif state == "exited":
            logger.info("Starting stopped WAHA container...")
            cls._run_docker(["docker", "start", cls.CONTAINER_NAME])
        elif state == "missing":
            logger.info("Creating and starting WAHA container...")
            cls._create_container(port, api_key)
        else:
            # Unknown state — remove and recreate
            logger.warning(f"WAHA container in unexpected state '{state}', recreating...")
            cls._run_docker(["docker", "rm", "-f", cls.CONTAINER_NAME])
            cls._create_container(port, api_key)

        # Wait for WAHA to become healthy
        if await cls._wait_for_healthy(waha_url, api_key):
            logger.info("WAHA is ready")
            return True

        logger.error(
            f"WAHA did not become healthy within {cls.HEALTH_TIMEOUT}s. "
            "Check Docker logs: docker logs waha"
        )
        return False

    @classmethod
    def _docker_available(cls) -> bool:
        """Check if Docker CLI is available and the daemon is running."""
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    @classmethod
    def _get_container_state(cls) -> str:
        """Get the state of the WAHA container: 'running', 'exited', or 'missing'."""
        try:
            result = subprocess.run(
                [
                    "docker", "inspect",
                    "--format", "{{.State.Status}}",
                    cls.CONTAINER_NAME,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return "missing"
            return result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return "missing"

    @classmethod
    def _create_container(cls, port: int, api_key: str):
        """Create and start a new WAHA container."""
        cmd = [
            "docker", "run", "-d",
            "--name", cls.CONTAINER_NAME,
            "-p", f"{port}:{port}",
            "-e", f"WAHA_API_KEY={api_key}",
            "-e", f"WHATSAPP_DEFAULT_ENGINE=WEBJS",
            "-v", f"{cls.VOLUME_NAME}:/app/.sessions",
            "--restart", "unless-stopped",
            cls.IMAGE,
        ]
        cls._run_docker(cmd)

    @classmethod
    def _run_docker(cls, cmd: list[str]):
        """Run a Docker command and log any errors."""
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                logger.error(f"Docker command failed: {' '.join(cmd)}")
                logger.error(f"stderr: {result.stderr.strip()}")
        except subprocess.TimeoutExpired:
            logger.error(f"Docker command timed out: {' '.join(cmd)}")

    @classmethod
    async def _is_healthy(cls, waha_url: str, api_key: str) -> bool:
        """Check if WAHA API is responding."""
        headers = {"X-Api-Key": api_key} if api_key else {}
        try:
            async with httpx.AsyncClient(timeout=5, headers=headers) as client:
                resp = await client.get(f"{waha_url}/api/sessions")
                return resp.status_code in (200, 201)
        except Exception:
            return False

    @classmethod
    async def _wait_for_healthy(cls, waha_url: str, api_key: str) -> bool:
        """Poll WAHA until it's healthy or timeout."""
        elapsed = 0
        while elapsed < cls.HEALTH_TIMEOUT:
            if await cls._is_healthy(waha_url, api_key):
                return True
            await asyncio.sleep(cls.HEALTH_INTERVAL)
            elapsed += cls.HEALTH_INTERVAL
            if elapsed % 10 == 0:
                logger.info(f"Waiting for WAHA... ({elapsed}s)")
        return False

    @classmethod
    def _parse_port(cls, waha_url: str) -> int:
        """Extract port number from WAHA URL."""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(waha_url)
            return parsed.port or 3000
        except Exception:
            return 3000

    @classmethod
    def _save_api_key(cls, api_key: str):
        """Save the auto-generated API key to settings.yaml."""
        settings_path = Path(__file__).parent.parent.parent / "config" / "settings.yaml"
        if not settings_path.exists():
            return
        try:
            content = settings_path.read_text()
            # Replace empty waha_api_key or add it
            if 'waha_api_key: ""' in content:
                content = content.replace(
                    'waha_api_key: ""',
                    f'waha_api_key: "{api_key}"',
                )
            elif "waha_api_key:" in content:
                # Already has a key — don't overwrite
                return
            else:
                # Add after waha_url line
                content = content.replace(
                    'session_name: "default"',
                    f'waha_api_key: "{api_key}"\n    session_name: "default"',
                )
            settings_path.write_text(content)
        except Exception as e:
            logger.warning(f"Could not save WAHA API key to settings: {e}")
