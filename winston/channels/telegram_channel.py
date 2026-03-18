"""
Telegram messaging channel for W.I.N.S.T.O.N.
Uses python-telegram-bot v21+ (async).
Supports private messages and group mention-gating.
"""

import base64
import asyncio
import io
import logging
import re
import json
import tempfile
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from winston.core.safety import ActionRequest

from .base import BaseChannel

logger = logging.getLogger("winston.channels.telegram")


class TelegramChannel(BaseChannel):
    """
    Telegram bot channel.

    Features:
    - Private DMs: always responds
    - Group chats: only responds when @mentioned
    - Optional user allowlist for security
    - HTML formatting for Telegram
    - 4096 character message limit handling
    """

    name = "telegram"

    def __init__(self, config: dict, process_fn: Callable[[str], str]):
        super().__init__(config, process_fn)
        self.bot_token = config.get("bot_token", "")
        self.allowed_users = config.get("allowed_users", [])
        self.application = None
        self._default_chat_id = config.get("default_chat_id")
        self._chat_id_file = Path.home() / ".winston" / "telegram_chat.json"
        # Per-chat voice toggle (True = send voice messages)
        self._voice_enabled: dict[str, bool] = {}

        # ElevenLabs config (injected by main.py after construction)
        self._el_api_key: str = ""
        self._el_voice_id: str = "JBFqnCBsd6RMkjVDRZzb"
        self._el_model: str = "eleven_turbo_v2"

        # Load cached chat ID if available and empty
        if not self._default_chat_id and self._chat_id_file.exists():
            try:
                data = json.loads(self._chat_id_file.read_text())
                self._default_chat_id = data.get("default_chat_id")
            except Exception:
                pass
        
        # Confirmation tracking
        self._pending_confirmations: dict[str, dict] = {}

    async def start(self):
        """Start the Telegram bot polling loop."""
        try:
            from telegram.ext import (
                ApplicationBuilder, MessageHandler, CommandHandler, filters,
                CallbackQueryHandler,
            )
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        except ImportError:
            logger.error(
                "python-telegram-bot not installed. "
                "Install with: pip install 'python-telegram-bot>=21.0'"
            )
            return

        if not self.bot_token:
            logger.error("Telegram bot token not configured")
            return

        self.application = (
            ApplicationBuilder()
            .token(self.bot_token)
            .build()
        )

        # Register handlers
        self.application.add_handler(CommandHandler("start", self._handle_start))
        self.application.add_handler(CommandHandler("help", self._handle_help))
        self.application.add_handler(CommandHandler("daily", self._handle_daily))
        self.application.add_handler(CommandHandler("briefing", self._handle_daily))
        self.application.add_handler(CommandHandler("voice", self._handle_voice))
        self.application.add_handler(CommandHandler("voiceon", self._handle_voice_on))
        self.application.add_handler(CommandHandler("voiceoff", self._handle_voice_off))
        self.application.add_handler(CommandHandler("observer", self._handle_observer))
        self.application.add_handler(CommandHandler("image", self._handle_image_command))
        self.application.add_handler(
            MessageHandler(filters.VOICE | filters.AUDIO, self._handle_voice_message)
        )
        self.application.add_handler(
            MessageHandler(filters.PHOTO, self._handle_photo)
        )
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )
        self.application.add_handler(
            CallbackQueryHandler(self._handle_callback_query)
        )

        # Global error handler so exceptions are not silently swallowed
        self.application.add_error_handler(self._handle_error)

        self._running = True
        self.last_start_at = __import__("time").time()
        logger.info("Telegram bot starting...")

        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling(drop_pending_updates=True)

        bot_info = await self.application.bot.get_me()
        logger.info(f"Telegram bot connected as @{bot_info.username}")

        # Keep running until stopped
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await self._shutdown()

    async def _shutdown(self):
        """Clean shutdown of the telegram bot."""
        if self.application:
            try:
                await self.application.updater.stop()
                await self.application.stop()
                await self.application.shutdown()
            except Exception as e:
                logger.warning(f"Telegram shutdown warning: {e}")

    async def stop(self):
        """Signal the bot to stop."""
        self._running = False

    async def _handle_start(self, update, context):
        """Handle /start command."""
        await update.message.reply_text(
            "Hello! I am W.I.N.S.T.O.N., your AI assistant.\n\n"
            "Send me a message and I'll help you out.\n"
            "In group chats, mention me with @mention to get my attention.",
            parse_mode="HTML",
        )

    async def _handle_help(self, update, context):
        """Handle /help command."""
        await update.message.reply_text(
            "<b>W.I.N.S.T.O.N. Commands:</b>\n\n"
            "Just send me a message and I'll respond.\n\n"
            "<b>Commands:</b>\n"
            "<code>/daily [on|off|time HH:MM|city NAME]</code> - Morning briefing\n"
            "<code>/voice</code> - Show voice/TTS status and setup guide\n"
            "<code>/help</code> - Show this message\n\n"
            "<b>Things I can do:</b>\n"
            "- Search the web\n"
            "- Send & read emails\n"
            "- Manage notes, calendar & to-dos\n"
            "- Run scheduled tasks\n"
            "- And more!\n\n"
            "In groups: @mention me to get my attention.",
            parse_mode="HTML",
        )

    async def _handle_daily(self, update, context):
        """Handle /daily or /briefing configuration command."""
        if not context.args:
            await update.message.reply_text(
                "<b>Daily Briefing Configuration</b>\n\n"
                "Usage:\n"
                "<code>/daily on</code> - Enable morning briefing\n"
                "<code>/daily off</code> - Disable morning briefing\n"
                "<code>/daily time 08:30</code> - Set the briefing time\n"
                "<code>/daily city London</code> - Set your city for weather\n\n"
                "<i>This will automatically send you the weather, news, and calendar events every morning.</i>",
                parse_mode="HTML",
            )
            return

        cmd = context.args[0].lower()
        # Find the scheduler system
        # Since TelegramChannel only has access to process_fn directly, 
        # we can inject a special command to main.py or handle it via a shared config object,
        # but the cleanest way is to pass a config callback or use the global config object if available.
        # However, to avoid circular dependencies, we'll execute a hidden command and catch it in main.py,
        # OR just format a natural language sentence that W.I.N.S.T.O.N. understands!
        
        reply = "Processing your request..."
        if cmd == "on":
            reply = "Enabled the daily morning briefing."
            # Tell W.I.N.S.T.O.N. to do this via the LLM/system
            await self._process_async("Turn the morning briefing ON.")
            
        elif cmd == "off":
            reply = "Disabled the daily morning briefing."
            await self._process_async("Turn the morning briefing OFF.")
            
        elif cmd == "time" and len(context.args) > 1:
            time_str = context.args[1]
            reply = f"Set morning briefing time to {time_str}."
            await self._process_async(f"Change the morning briefing schedule to {time_str}.")
            
        elif cmd == "city" and len(context.args) > 1:
            city = " ".join(context.args[1:])
            reply = f"Set weather location for morning briefing to {city}."
            await self._process_async(f"Update the good_morning routine to check the weather in {city}.")
            
        await update.message.reply_text(reply)

    async def _handle_message(self, update, context):
        """Process incoming Telegram message."""
        if not update.message or not update.message.text:
            return

        self.record_event()
        user = update.effective_user
        chat = update.effective_chat

        # Auto-capture chat id for proactive messages (like briefings)
        if not self._default_chat_id or self._default_chat_id != str(chat.id):
            self._default_chat_id = str(chat.id)
            try:
                self._chat_id_file.parent.mkdir(parents=True, exist_ok=True)
                self._chat_id_file.write_text(json.dumps({"default_chat_id": self._default_chat_id}))
            except Exception as e:
                logger.error(f"Failed to cache telegram chat ID: {e}")

        # User allowlist check (empty = allow all)
        if self.allowed_users and user.id not in self.allowed_users:
            logger.info(f"Ignoring message from unauthorized user {user.id}")
            return

        text = update.message.text

        # In groups: only respond if bot is mentioned
        if chat.type in ("group", "supergroup"):
            bot_username = (await context.bot.get_me()).username
            if f"@{bot_username}" not in text:
                return
            text = text.replace(f"@{bot_username}", "").strip()

        if not text:
            return

        logger.info(f"Telegram message from {user.first_name} ({user.id}): {text[:100]}")

        # Send typing indicator
        await chat.send_action("typing")

        # Process through WINSTON pipeline
        try:
            result = await self._process_async(text)
            
            if isinstance(result, dict):
                response = result.get("message", "")
                data = result.get("data", [])
                await self._handle_skill_response(update, response, data)
            else:
                response = result
                formatted = self.format_response(response)
                # Handle Telegram's 4096 character limit
                if len(formatted) > 4096:
                    chunks = self._split_message(formatted, 4096)
                    for chunk in chunks:
                        await update.message.reply_text(chunk, parse_mode="HTML")
                else:
                    await update.message.reply_text(formatted, parse_mode="HTML")

            # Send voice message if enabled for this chat
            chat_id_str = str(chat.id)
            if self._voice_enabled.get(chat_id_str, False) and self._el_api_key:
                audio_bytes = await self._generate_voice_audio(response)
                if audio_bytes:
                    await update.message.reply_voice(
                        voice=io.BytesIO(audio_bytes),
                        caption="🎙️ W.I.N.S.T.O.N.",
                    )
        except Exception as e:
            logger.error(f"Error processing Telegram message: {e}")
            await update.message.reply_text(
                "I encountered an error processing your request. Please try again."
            )

    async def _handle_observer(self, update, context):
        """Toggle Observer Mode on/off."""
        if not context.args:
            await update.message.reply_text("Usage: /observer on OR /observer off")
            return
            
        cmd = context.args[0].lower()
        if cmd == "on":
            await self._process_async("turn the observer mode on")
            await update.message.reply_text("🤖 Observer mode activated. I am now listening to your physical environment in the background.")
        elif cmd == "off":
            await self._process_async("turn the observer mode off")
            await update.message.reply_text("💤 Observer mode deactivated.")
        else:
            await update.message.reply_text("Usage: /observer on OR /observer off")

    async def _handle_error(self, update, context):
        """Log errors from telegram handlers so they are not silently swallowed."""
        logger.error(f"Telegram handler error: {context.error}", exc_info=context.error)

    async def _handle_voice_message(self, update, context):
        """Process incoming voice or audio messages."""
        if not update.message:
            return

        user = update.effective_user
        chat = update.effective_chat

        # User allowlist check
        if self.allowed_users and user.id not in self.allowed_users:
            logger.info(f"Ignoring voice message from unauthorized user {user.id}")
            return

        # Determine if it's a voice note or an audio file
        audio_obj = update.message.voice or update.message.audio
        if not audio_obj:
            return

        logger.info(f"Telegram voice message received from {user.first_name} ({user.id})")

        await chat.send_action("typing")

        try:
            # 1. Download the file
            file = await context.bot.get_file(audio_obj.file_id)

            # Use a temporary file with .ogg extension (Telegram's default for voice)
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
                await file.download_to_drive(tmp.name)
                tmp_path = tmp.name

            # 2. Transcribe using Whisper
            if hasattr(self, "_transcribe_callback") and self._transcribe_callback:
                loop = asyncio.get_event_loop()
                transcript = await loop.run_in_executor(None, self._transcribe_callback, tmp_path)
            else:
                await update.message.reply_text("My transcription module is currently offline.", parse_mode="HTML")
                Path(tmp_path).unlink(missing_ok=True)
                return

            Path(tmp_path).unlink(missing_ok=True)

            if not transcript:
                await update.message.reply_text("I couldn't hear any speech in that audio.", parse_mode="HTML")
                return

            # Echo the transcript so the user knows what we heard
            await update.message.reply_text(f"<i>Transcript: \"{transcript}\"</i>", parse_mode="HTML")

            # 3. Process the transcript through the brain
            prefix = "The user sent a voice message. Here is the transcript: "
            response = await self._process_async(f"{prefix} '{transcript}'")
            formatted = self.format_response(response)

            # 4. Reply
            if len(formatted) > 4096:
                chunks = self._split_message(formatted, 4096)
                for chunk in chunks:
                    await update.message.reply_text(chunk, parse_mode="HTML")
            else:
                await update.message.reply_text(formatted, parse_mode="HTML")

            # Send voice message if enabled
            chat_id_str = str(chat.id)
            if self._voice_enabled.get(chat_id_str, False) and self._el_api_key:
                audio_bytes = await self._generate_voice_audio(response)
                if audio_bytes:
                    await update.message.reply_voice(
                        voice=io.BytesIO(audio_bytes),
                        caption="W.I.N.S.T.O.N.",
                    )

        except Exception as e:
            logger.error(f"Error processing Telegram voice message: {e}")
            await update.message.reply_text("I encountered an error processing your audio message.", parse_mode="HTML")

    async def _handle_photo(self, update, context):
        """Process incoming photos with captions."""
        if not update.message or not update.message.photo:
            return

        user = update.effective_user
        chat = update.effective_chat
        caption = update.message.caption or "What is in this image?"

        # User allowlist check
        if self.allowed_users and user.id not in self.allowed_users:
            return

        logger.info(f"Telegram photo from {user.first_name} ({user.id}): {caption[:100]}")

        await chat.send_action("typing")

        try:
            # Get the highest resolution photo
            photo_file = await update.message.photo[-1].get_file()

            # Download to memory
            photo_bytes = io.BytesIO()
            await photo_file.download_to_memory(photo_bytes)
            photo_bytes.seek(0)

            # Encode to base64
            image_b64 = base64.b64encode(photo_bytes.read()).decode("utf-8")

            # Process through WINSTON pipeline with the image
            response = await self._process_async(caption, images=[image_b64])
            formatted = self.format_response(response)

            # Send response
            if len(formatted) > 4096:
                chunks = self._split_message(formatted, 4096)
                for chunk in chunks:
                    await update.message.reply_text(chunk, parse_mode="HTML")
            else:
                await update.message.reply_text(formatted, parse_mode="HTML")

        except Exception as e:
            logger.error(f"Error processing Telegram photo: {e}")
            await update.message.reply_text(
                "I encountered an error analyzing your photo. Please try again."
            )

    async def _generate_voice_audio(self, text: str) -> Optional[bytes]:
        """Generate ElevenLabs audio for the given text. Returns MP3 bytes or None."""
        if not self._el_api_key:
            return None
        try:
            import httpx
            import re as _re
            # Strip markdown/symbols for cleaner speech
            clean = _re.sub(r"<[^>]+>", " ", text)          # HTML tags
            clean = _re.sub(r"[*_`#>]", "", clean)           # markdown
            clean = _re.sub(r"https?://\S+", "", clean)       # URLs
            clean = _re.sub(r"\s+", " ", clean).strip()
            if not clean:
                return None
            # Limit to ~500 chars to stay within free-tier limits
            clean = clean[:500]

            url = f"https://api.elevenlabs.io/v1/text-to-speech/{self._el_voice_id}"
            headers = {
                "xi-api-key": self._el_api_key,
                "Content-Type": "application/json",
            }
            payload = {
                "text": clean,
                "model_id": self._el_model,
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            }
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 200:
                    return resp.content
                else:
                    logger.warning(f"ElevenLabs TTS returned {resp.status_code}")
                    return None
        except Exception as e:
            logger.warning(f"Voice audio generation failed: {e}")
            return None

    async def _handle_voice_on(self, update, context):
        """Enable voice messages for this chat."""
        chat_id = str(update.effective_chat.id)
        self._voice_enabled[chat_id] = True
        if not self._el_api_key:
            await update.message.reply_text(
                "⚠️ ElevenLabs API key not set. Add it to <code>config/settings.yaml</code> and restart.",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                "🎙️ Voice messages <b>ON</b> — I'll send audio with my replies!",
                parse_mode="HTML",
            )

    async def _handle_voice_off(self, update, context):
        """Disable voice messages for this chat."""
        chat_id = str(update.effective_chat.id)
        self._voice_enabled[chat_id] = False
        await update.message.reply_text("🔇 Voice messages <b>OFF</b>.", parse_mode="HTML")


    async def send_message(self, channel_id: str, text: str):
        """Send a proactive message (for scheduled task results)."""
        if not self.application or not self._running:
            return

        try:
            # Handle message length
            if len(text) > 4096:
                chunks = self._split_message(text, 4096)
                for chunk in chunks:
                    await self.application.bot.send_message(
                        chat_id=channel_id, text=chunk, parse_mode="HTML",
                    )
            else:
                await self.application.bot.send_message(
                    chat_id=channel_id, text=text, parse_mode="HTML",
                )
                
            # Proactive text should also be spoken if voice is enabled!
            if self._voice_enabled.get(channel_id, False) and self._el_api_key:
                audio_bytes = await self._generate_voice_audio(text)
                if audio_bytes:
                    await self.application.bot.send_voice(
                        chat_id=channel_id,
                        voice=io.BytesIO(audio_bytes),
                        caption="🎙️ W.I.N.S.T.O.N. Update",
                    )
        except Exception as e:
            logger.error(f"Failed to send Telegram message to {channel_id}: {e}")

    async def request_user_confirmation(self, action_req: 'ActionRequest') -> bool:
        """Ask the user to confirm a pending action via Telegram buttons."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        
        # Always use the default chat ID for confirmations if coming from background, 
        # or use a cached "current" chat ID if needed. For now, default is safer.
        chat_id = self._default_chat_id
        if not chat_id:
            logger.warning("No default chat ID for Telegram confirmation")
            return False

        # Create buttons
        keyboard = [
            [
                InlineKeyboardButton("✅ Ja", callback_data=f"confirm_{action_req.id}_yes"),
                InlineKeyboardButton("❌ Nein", callback_data=f"confirm_{action_req.id}_no"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Build message
        header = "<b>⚠️ Maßnahme erfordert Bestätigung</b>\n"
        if action_req.screenshot_path:
            header += "<i>(Formular wurde ausgefüllt, siehe Vorschau unten)</i>\n"
        
        msg = f"{header}\n{action_req.summary()}"

        # Store event to wait on
        event = asyncio.Event()
        self._pending_confirmations[action_req.id] = {"event": event, "result": False}

        try:
            if action_req.screenshot_path and Path(action_req.screenshot_path).exists():
                with open(action_req.screenshot_path, "rb") as photo:
                    await self.application.bot.send_photo(
                        chat_id=chat_id,
                        photo=photo,
                        caption=msg[:1024], # Telegram caption limit
                        reply_markup=reply_markup,
                        parse_mode="HTML"
                    )
            else:
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text=msg,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )

            # Wait for response (timeout 10 minutes)
            try:
                await asyncio.wait_for(event.wait(), timeout=600)
                return self._pending_confirmations[action_req.id]["result"]
            except asyncio.TimeoutError:
                logger.warning(f"Telegram confirmation timed out for {action_req.id}")
                return False
        except Exception as e:
            logger.error(f"Failed to send Telegram confirmation: {e}")
            return False
        finally:
            self._pending_confirmations.pop(action_req.id, None)

    async def _handle_callback_query(self, update, context):
        """Handle button clicks for confirmations."""
        query = update.callback_query
        await query.answer()

        data = query.data
        if data.startswith("confirm_"):
            parts = data.split("_")
            if len(parts) != 3:
                return
            _, action_id, result = parts
            
            if action_id in self._pending_confirmations:
                approved = (result == "yes")
                self._pending_confirmations[action_id]["result"] = approved
                self._pending_confirmations[action_id]["event"].set()
                
                # Update message to show status
                status = "✅ Bestätigt" if approved else "❌ Abgelehnt"
                
                try:
                    if query.message.caption:
                        new_caption = f"{query.message.caption}\n\n<b>Status: {status}</b>"
                        await query.edit_message_caption(caption=new_caption[:1024], parse_mode="HTML")
                    else:
                        new_text = f"{query.message.text}\n\n<b>Status: {status}</b>"
                        await query.edit_message_text(text=new_text, parse_mode="HTML")
                except Exception as e:
                    logger.warning(f"Could not update confirmation message: {e}")

    async def _handle_voice(self, update, context):
        """Handle /voice — show TTS engine status and setup guide."""
        await update.message.reply_text(
            "<b>🎙️ Voice / TTS Configuration</b>\n\n"
            "W.I.N.S.T.O.N. supports two high-quality voice engines:\n\n"
            "<b>Option 1 — ElevenLabs (best quality, cloud)</b>\n"
            "1. Sign up free at <a href=\"https://elevenlabs.io\">elevenlabs.io</a>\n"
            "2. Copy your API key from Profile → API Key\n"
            "3. Add to <code>~/.winston/config.yaml</code>:\n"
            "<pre>tts:\n"
            "  engine: elevenlabs\n"
            "  elevenlabs_api_key: YOUR_KEY_HERE\n"
            "  elevenlabs_voice_id: onwK4e9ZLuTAKqWW03F9  # Daniel (British)</pre>\n\n"
            "<b>Option 2 — Piper TTS (local, offline, fast)</b>\n"
            "Add to <code>~/.winston/config.yaml</code>:\n"
            "<pre>tts:\n"
            "  engine: piper\n"
            "  piper_model: en_US-lessac-high  # auto-downloaded</pre>\n"
            "Then install the binary:\n"
            "<code>brew install piper-tts</code>\n\n"
            "🔄 Restart W.I.N.S.T.O.N. after changing the config.",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    async def _handle_image_command(self, update, context):
        """Handle /image [prompt] command."""
        if not context.args:
            await update.message.reply_text("Usage: <code>/image a futuristic city</code>", parse_mode="HTML")
            return

        prompt = " ".join(context.args)
        user = update.effective_user
        chat = update.effective_chat

        if self.allowed_users and user.id not in self.allowed_users:
            return

        logger.info(f"Telegram /image from {user.first_name}: {prompt}")
        await chat.send_action("upload_photo")

        try:
            # Process via brain (which should trigger image_gen skill)
            result = await self._process_async(f"Generate an image: {prompt}")
            
            if isinstance(result, dict):
                response = result.get("message", "")
                data = result.get("data", [])
                await self._handle_skill_response(update, response, data)
            else:
                await update.message.reply_text(self.format_response(result), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error in Telegram /image: {e}")
            await update.message.reply_text("Failed to generate image.")

    async def _handle_skill_response(self, update, response: str, data: list[dict]):
        """Handle sending rich data (images, etc.) back to Telegram."""
        # Send text first
        formatted = self.format_response(response)
        if len(formatted) > 4096:
            chunks = self._split_message(formatted, 4096)
            for chunk in chunks:
                await update.message.reply_text(chunk, parse_mode="HTML")
        else:
            await update.message.reply_text(formatted, parse_mode="HTML")

        # Then send media from skill data
        for item in data:
            if "image_b64" in item:
                img_data = base64.b64decode(item["image_b64"])
                await update.message.reply_photo(
                    photo=io.BytesIO(img_data),
                    caption="🎨 Generated by W.I.N.S.T.O.N."
                )
            elif "local_path" in item:
                path = Path(item["local_path"])
                if path.exists():
                    with open(path, "rb") as f:
                        await update.message.reply_photo(
                            photo=f,
                            caption="🎨 Generated by W.I.N.S.T.O.N."
                        )

    def format_response(self, text: str) -> str:
        """Convert markdown-style text to Telegram HTML."""
        # Bold: **text** -> <b>text</b>
        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
        # Italic: *text* -> <i>text</i>  (but not **bold**)
        text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", text)
        # Code: `text` -> <code>text</code>
        text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
        # Code blocks: ```text``` -> <pre>text</pre>
        text = re.sub(r"```(?:\w*\n)?(.*?)```", r"<pre>\1</pre>", text, flags=re.DOTALL)
        return text

    @staticmethod
    def _split_message(text: str, max_len: int) -> list[str]:
        """Split a long message into chunks, preserving Markdown structure."""
        from winston.utils.chunker import chunk_message
        return chunk_message(text, max_len=max_len, channel="telegram")
