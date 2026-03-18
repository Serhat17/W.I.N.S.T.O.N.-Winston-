"""
WhatsApp messaging channel for W.I.N.S.T.O.N.
Uses WAHA (WhatsApp HTTP API) — a self-hosted Docker container
that exposes REST endpoints for sending/receiving WhatsApp messages.

Requires:
    - WAHA running in Docker (https://github.com/devlikeapro/waha)
    - httpx, aiohttp installed
    - Webhook port accessible from the WAHA container
"""

import asyncio
import base64
import io
import json
import logging
import re
import tempfile
from pathlib import Path
from typing import Callable, Optional

import httpx
from aiohttp import web

from .base import BaseChannel

logger = logging.getLogger("winston.channels.whatsapp")


class WhatsAppChannel(BaseChannel):
    """
    WhatsApp channel via WAHA (WhatsApp HTTP API).

    Features:
    - Receives messages via webhook from WAHA
    - Supports text, image, and voice/audio messages
    - Optional phone number allowlist for security
    - WhatsApp-native formatting (*bold*, _italic_, ```code```)
    - 4096 character message limit handling
    - Auto-captures default_chat_id on first message
    """

    name = "whatsapp"

    def __init__(self, config: dict, process_fn: Callable[[str], str]):
        super().__init__(config, process_fn)
        self._config = config
        self.waha_url = config.get("waha_url", "http://localhost:3000").rstrip("/")
        self.session_name = config.get("session_name", "default")
        self.waha_api_key = config.get("waha_api_key", "")
        self.allowed_numbers = config.get("allowed_numbers", [])
        self._webhook_port = config.get("webhook_port", 3001)
        self._default_chat_id = config.get("default_chat_id")
        self._chat_id_file = Path.home() / ".winston" / "whatsapp_chat.json"

        # Transcription callback injected by server.py
        self._transcribe_callback: Optional[Callable] = None

        # aiohttp runner for the webhook server
        self._runner: Optional[web.AppRunner] = None

        # httpx client for WAHA API calls (created in start())
        self._http_client: Optional[httpx.AsyncClient] = None

        # Pending confirmations: chat_id -> [Event, result]
        self._pending_confirmations: dict[str, list] = {}

        # Load cached chat ID if not configured
        if not self._default_chat_id and self._chat_id_file.exists():
            try:
                data = json.loads(self._chat_id_file.read_text())
                self._default_chat_id = data.get("default_chat_id")
            except Exception:
                pass

    async def start(self):
        """
        Start the WhatsApp channel:
        1. Auto-start WAHA Docker container if needed
        2. Create httpx client for WAHA API
        3. Start aiohttp webhook server
        4. Register webhook URL with WAHA
        5. Keep running until stopped
        """
        # Auto-start WAHA Docker container if needed
        if self._config.get("auto_start_docker", True):
            from winston.utils.docker_manager import WAHAManager
            ready = await WAHAManager.ensure_running(self._config)
            if not ready:
                logger.error("WAHA is not available — WhatsApp channel will not start")
                return
            # Pick up auto-generated API key if one was created
            if self._config.get("waha_api_key") and not self.waha_api_key:
                self.waha_api_key = self._config["waha_api_key"]

        headers = {}
        if self.waha_api_key:
            headers["X-Api-Key"] = self.waha_api_key
        self._http_client = httpx.AsyncClient(timeout=30, headers=headers)

        # Build the aiohttp webhook server
        app = web.Application()
        app.router.add_post("/webhook", self._handle_webhook)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self._webhook_port)
        await site.start()

        logger.info(f"WhatsApp webhook server started on port {self._webhook_port}")

        # Register webhook with WAHA
        await self._register_webhook()

        self._running = True
        self.last_start_at = __import__("time").time()
        logger.info(
            f"WhatsApp channel started (WAHA: {self.waha_url}, "
            f"session: {self.session_name})"
        )

        # Keep running until stopped
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await self._cleanup()

    async def _register_webhook(self):
        """Register the webhook URL with the WAHA session."""
        webhook_url = f"http://host.docker.internal:{self._webhook_port}/webhook"
        url = f"{self.waha_url}/api/sessions/{self.session_name}/webhooks"
        payload = {
            "url": webhook_url,
            "events": ["message"],
        }

        try:
            resp = await self._http_client.put(url, json=payload)
            if resp.status_code in (200, 201):
                logger.info(f"Webhook registered with WAHA: {webhook_url}")
            else:
                logger.error(
                    f"Failed to register WAHA webhook: "
                    f"{resp.status_code} {resp.text}"
                )
        except Exception as e:
            logger.error(f"Error registering WAHA webhook: {e}")

    async def _cleanup(self):
        """Clean up resources on shutdown."""
        if self._runner:
            try:
                await self._runner.cleanup()
            except Exception as e:
                logger.warning(f"WhatsApp webhook cleanup warning: {e}")
            self._runner = None

        if self._http_client:
            try:
                await self._http_client.aclose()
            except Exception as e:
                logger.warning(f"WhatsApp HTTP client cleanup warning: {e}")
            self._http_client = None

    async def stop(self):
        """Signal the channel to stop."""
        self._running = False

    async def request_user_confirmation(self, action_req) -> bool:
        """Request confirmation via WhatsApp text (Ja/Nein)."""
        if not self._default_chat_id or not self._running:
            return False

        try:
            event = asyncio.Event()
            self._pending_confirmations[self._default_chat_id] = [event, False]

            msg = (
                f"🛡️ *Security Confirmation*\n\n"
                f"WINSTON is requesting to perform:\n"
                f"_{action_req.description}_\n\n"
                f"Risk: {action_req.risk_level.value}\n\n"
                f"Please reply with *Ja* to approve or *Nein* to deny."
            )
            
            await self.send_message(self._default_chat_id, msg)

            # Wait for reply via webhook (timeout 10 mins)
            try:
                await asyncio.wait_for(event.wait(), timeout=600)
                return self._pending_confirmations[self._default_chat_id][1]
            except asyncio.TimeoutError:
                await self.send_message(self._default_chat_id, "Confirmation timed out.")
                return False
            finally:
                self._pending_confirmations.pop(self._default_chat_id, None)

        except Exception as e:
            logger.error(f"WhatsApp confirmation failed: {e}")
            return False

    async def send_message(self, channel_id: str, text: str):
        """
        Send a text message to a WhatsApp chat via WAHA.

        Args:
            channel_id: The chat ID (e.g. "491234567890@c.us")
            text: The message text to send
        """
        if not self._http_client or not self._running:
            return

        try:
            # Handle WhatsApp's ~4096 character limit
            if len(text) > 4096:
                chunks = self._split_message(text, 4096)
                for chunk in chunks:
                    await self._send_text(channel_id, chunk)
            else:
                await self._send_text(channel_id, text)
        except Exception as e:
            logger.error(f"Failed to send WhatsApp message to {channel_id}: {e}")

    async def _send_text(self, chat_id: str, text: str):
        """Send a single text message via WAHA API."""
        url = f"{self.waha_url}/api/sendText"
        payload = {
            "chatId": chat_id,
            "text": text,
            "session": self.session_name,
        }
        resp = await self._http_client.post(url, json=payload)
        if resp.status_code not in (200, 201):
            logger.error(
                f"WAHA sendText failed: {resp.status_code} {resp.text}"
            )

    def format_response(self, text: str) -> str:
        """
        Convert markdown-style text to WhatsApp formatting.

        WhatsApp supports:
            *bold*      (single asterisks)
            _italic_    (single underscores)
            ```code```  (triple backticks for monospace blocks)
            `code`      (not officially supported, keep as-is or use ```)
        """
        # Bold: **text** -> *text* (WhatsApp uses single asterisks)
        text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
        # Italic: keep _text_ as-is (already WhatsApp-native)
        # Code blocks: ```lang\ncode``` -> ```code``` (strip language hint)
        text = re.sub(
            r"```\w*\n(.*?)```", r"```\1```", text, flags=re.DOTALL
        )
        return text

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        """
        Handle incoming WAHA webhook events.

        WAHA sends JSON payloads for various events. We care about:
        - 'message' / 'message.any': incoming messages
        """
        try:
            data = await request.json()
        except Exception as e:
            logger.warning(f"Invalid webhook payload: {e}")
            return web.Response(status=400, text="Invalid JSON")

        event = data.get("event", "")

        # Only process message events
        if event not in ("message", "message.any"):
            return web.Response(status=200, text="OK")

        # Run message handling in background so webhook returns quickly
        asyncio.create_task(self._process_webhook_message(data))

        return web.Response(status=200, text="OK")

    async def _process_webhook_message(self, data: dict):
        """Process a webhook message payload from WAHA."""
        self.record_event()
        try:
            payload = data.get("payload", {})

            # Ignore outgoing messages (from us)
            if payload.get("fromMe", False):
                return

            chat_id = payload.get("from", "")
            if not chat_id:
                return

            # Allowed numbers check (empty list = allow all)
            if self.allowed_numbers:
                # Extract number from chat_id (e.g. "491234567890@c.us" -> "491234567890")
                number = chat_id.split("@")[0]
                if number not in self.allowed_numbers:
                    logger.info(
                        f"Ignoring message from unauthorized number: {number}"
                    )
                    return

            # Auto-capture default chat ID on first message
            if not self._default_chat_id:
                self._default_chat_id = chat_id
                try:
                    self._chat_id_file.parent.mkdir(parents=True, exist_ok=True)
                    self._chat_id_file.write_text(
                        json.dumps({"default_chat_id": self._default_chat_id})
                    )
                    logger.info(f"Auto-captured default chat ID: {chat_id}")
                except Exception as e:
                    logger.error(f"Failed to cache WhatsApp chat ID: {e}")

            # Determine message type and handle accordingly
            body = payload.get("body", "")
            has_media = payload.get("hasMedia", False)
            media_type = payload.get("type", "chat")

            if media_type == "image" and has_media:
                await self._handle_image_message(payload, chat_id, body)
            elif media_type in ("audio", "ptt") and has_media:
                await self._handle_audio_message(payload, chat_id)
            elif body:
                # Check for pending confirmation reply
                if chat_id in self._pending_confirmations:
                    text_lower = body.strip().lower()
                    if text_lower in ("ja", "yes", "ok", "approve"):
                        self._pending_confirmations[chat_id][1] = True
                        self._pending_confirmations[chat_id][0].set()
                        await self.send_message(chat_id, "Action approved ✓")
                        return
                    elif text_lower in ("nein", "no", "deny", "stop"):
                        self._pending_confirmations[chat_id][1] = False
                        self._pending_confirmations[chat_id][0].set()
                        await self.send_message(chat_id, "Action denied ✕")
                        return

                await self._handle_text_message(body, chat_id)
            else:
                logger.debug(f"Ignoring unsupported message type: {media_type}")

        except Exception as e:
            logger.error(f"Error processing WhatsApp webhook message: {e}")

    async def _handle_text_message(self, text: str, chat_id: str):
        """Process an incoming text message."""
        logger.info(
            f"WhatsApp message from {chat_id}: {text[:100]}"
        )

        try:
            response = await self._process_async(text)
            formatted = self.format_response(response)
            await self.send_message(chat_id, formatted)
        except Exception as e:
            logger.error(f"Error processing WhatsApp text message: {e}")
            await self.send_message(
                chat_id,
                "I encountered an error processing your request. Please try again.",
            )

    async def _handle_image_message(
        self, payload: dict, chat_id: str, caption: str
    ):
        """Process an incoming image message."""
        logger.info(f"WhatsApp image from {chat_id}: {caption[:100] if caption else '(no caption)'}")

        try:
            # Download the media file from WAHA
            media_url = payload.get("mediaUrl", "")
            if not media_url:
                logger.warning("Image message has no mediaUrl")
                return

            media_bytes = await self._download_media(media_url)
            if not media_bytes:
                await self.send_message(
                    chat_id, "I couldn't download that image. Please try again."
                )
                return

            # Base64 encode the image
            image_b64 = base64.b64encode(media_bytes).decode("utf-8")

            # Use caption or default prompt
            prompt = caption if caption else "What is in this image?"

            # Process through WINSTON pipeline with the image
            response = await self._process_async(prompt, images=[image_b64])
            formatted = self.format_response(response)
            await self.send_message(chat_id, formatted)

        except Exception as e:
            logger.error(f"Error processing WhatsApp image message: {e}")
            await self.send_message(
                chat_id,
                "I encountered an error analyzing your image. Please try again.",
            )

    async def _handle_audio_message(self, payload: dict, chat_id: str):
        """Process an incoming voice/audio message."""
        logger.info(f"WhatsApp voice message from {chat_id}")

        try:
            # Download the media file from WAHA
            media_url = payload.get("mediaUrl", "")
            if not media_url:
                logger.warning("Audio message has no mediaUrl")
                return

            media_bytes = await self._download_media(media_url)
            if not media_bytes:
                await self.send_message(
                    chat_id,
                    "I couldn't download that audio message. Please try again.",
                )
                return

            # Transcribe the audio
            if not self._transcribe_callback:
                await self.send_message(
                    chat_id,
                    "My transcription module is currently offline.",
                )
                return

            # Save to a temp file for the transcriber
            with tempfile.NamedTemporaryFile(
                suffix=".ogg", delete=False
            ) as tmp:
                tmp.write(media_bytes)
                tmp_path = tmp.name

            try:
                loop = asyncio.get_event_loop()
                transcript = await loop.run_in_executor(
                    None, self._transcribe_callback, tmp_path
                )
            finally:
                Path(tmp_path).unlink(missing_ok=True)

            if not transcript:
                await self.send_message(
                    chat_id,
                    "I couldn't hear any speech in that audio.",
                )
                return

            # Echo the transcript back to the user
            await self.send_message(
                chat_id, f'_Transcript: "{transcript}"_'
            )

            # Process the transcript through the brain
            prefix = "The user sent a voice message. Here is the transcript: "
            response = await self._process_async(f"{prefix}'{transcript}'")
            formatted = self.format_response(response)
            await self.send_message(chat_id, formatted)

        except Exception as e:
            logger.error(f"Error processing WhatsApp voice message: {e}")
            await self.send_message(
                chat_id,
                "I encountered an error processing your audio message.",
            )

    async def _download_media(self, media_url: str) -> Optional[bytes]:
        """
        Download media from a WAHA media URL.

        Args:
            media_url: The URL to download (may be relative or absolute)

        Returns:
            The file bytes, or None on failure.
        """
        try:
            # If the URL is relative, prepend the WAHA base URL
            if media_url.startswith("/"):
                media_url = f"{self.waha_url}{media_url}"

            resp = await self._http_client.get(media_url)
            if resp.status_code == 200:
                return resp.content
            else:
                logger.error(
                    f"WAHA media download failed: {resp.status_code} {media_url}"
                )
                return None
        except Exception as e:
            logger.error(f"Error downloading WAHA media: {e}")
            return None

    @staticmethod
    def _split_message(text: str, max_len: int) -> list[str]:
        """Split a long message into chunks, preserving Markdown structure."""
        from winston.utils.chunker import chunk_message
        return chunk_message(text, max_len=max_len, channel="whatsapp")
        return chunks
