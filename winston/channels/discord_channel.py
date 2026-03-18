"""
Discord messaging channel for W.I.N.S.T.O.N.
Uses discord.py v2+.
Supports DMs and guild mention-gating.
"""

import asyncio
import base64
import logging
import tempfile
from pathlib import Path
from typing import Callable, Optional

from .base import BaseChannel

logger = logging.getLogger("winston.channels.discord")


class DiscordChannel(BaseChannel):
    """
    Discord bot channel.

    Features:
    - DMs: always responds
    - Guild channels: only responds when @mentioned
    - Optional guild allowlist for security
    - Native markdown formatting (Discord supports it)
    - 2000 character message limit handling
    """

    name = "discord"

    def __init__(self, config: dict, process_fn: Callable[[str], str]):
        super().__init__(config, process_fn)
        self.bot_token = config.get("bot_token", "")
        self.allowed_guilds = config.get("allowed_guilds", [])
        self._default_channel_id = config.get("default_channel_id", "")
        self.client = None

        # Transcription callback injected by server.py
        self._transcribe_callback: Optional[Callable] = None

    async def start(self):
        """Start the Discord bot."""
        try:
            import discord
        except ImportError:
            logger.error(
                "discord.py not installed. "
                "Install with: pip install 'discord.py>=2.3.0'"
            )
            return

        if not self.bot_token:
            logger.error("Discord bot token not configured")
            return

        intents = discord.Intents.default()
        intents.message_content = True
        self.client = discord.Client(intents=intents)

        @self.client.event
        async def on_ready():
            self._running = True
            self.last_start_at = __import__("time").time()
            logger.info(f"Discord bot connected as {self.client.user}")

        @self.client.event
        async def on_message(message):
            # Ignore own messages
            if message.author == self.client.user:
                return

            self.record_event()

            # Guild allowlist check (empty = allow all)
            if message.guild:
                if self.allowed_guilds and message.guild.id not in self.allowed_guilds:
                    return

                # In guilds: only respond if mentioned
                if not self.client.user.mentioned_in(message):
                    return

                # Remove the mention from the text
                text = message.content
                for mention in message.mentions:
                    if mention.id == self.client.user.id:
                        text = text.replace(f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
                text = text.strip()
            else:
                # DMs: always respond
                text = message.content

            # Handle voice/audio attachments
            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith("audio"):
                    await self._handle_voice_attachment(message, attachment)
                    return
                if attachment.content_type and attachment.content_type.startswith("image"):
                    await self._handle_image_attachment(message, attachment, text)
                    return

            if not text:
                return

            logger.info(f"Discord message from {message.author}: {text[:100]}")

            # Show typing indicator
            async with message.channel.typing():
                try:
                    response = await self._process_async(text)
                    formatted = self.format_response(response)

                    # Handle Discord's 2000 character limit
                    if len(formatted) > 2000:
                        chunks = self._split_message(formatted, 2000)
                        for chunk in chunks:
                            await message.channel.send(chunk)
                    else:
                        await message.channel.send(formatted)
                except Exception as e:
                    logger.error(f"Error processing Discord message: {e}")
                    await message.channel.send(
                        "I encountered an error processing your request. Please try again."
                    )

        try:
            await self.client.start(self.bot_token)
        except asyncio.CancelledError:
            pass
        finally:
            if self.client and not self.client.is_closed():
                await self.client.close()

    async def request_user_confirmation(self, action_req) -> bool:
        """Request confirmation via Discord buttons."""
        if not self.client or not self._running:
            return False

        try:
            import discord
            from discord.ui import Button, View

            channel_id = self._default_channel_id
            if not channel_id:
                logger.error("No default channel ID for Discord confirmation")
                return False

            channel = self.client.get_channel(int(channel_id))
            if not channel:
                channel = await self.client.fetch_channel(int(channel_id))

            if not channel:
                return False

            # Create a simple View with Approve/Deny buttons
            class ConfirmationView(View):
                def __init__(self, timeout=600):
                    super().__init__(timeout=timeout)
                    self.value = None
                    self.event = asyncio.Event()

                @discord.ui.button(label="Ja / Approve", style=discord.ButtonStyle.green)
                async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
                    self.value = True
                    self.event.set()
                    await interaction.response.send_message("Action approved ✓", ephemeral=True)
                    self.stop()

                @discord.ui.button(label="Nein / Deny", style=discord.ButtonStyle.red)
                async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
                    self.value = False
                    self.event.set()
                    await interaction.response.send_message("Action denied ✕", ephemeral=True)
                    self.stop()

            view = ConfirmationView()
            
            embed = discord.Embed(
                title="🛡️ Security Confirmation",
                description=f"WINSTON is requesting to perform:\n**{action_req.description}**",
                color=discord.Color.blue()
            )
            embed.add_field(name="Risk", value=f"⚠️ {action_req.risk_level.value}", inline=True)
            
            file = None
            if action_req.screenshot_path and Path(action_req.screenshot_path).exists():
                file = discord.File(action_req.screenshot_path, filename="screenshot.png")
                embed.set_image(url="attachment://screenshot.png")

            msg = await channel.send(embed=embed, view=view, file=file)
            
            # Wait for user input
            await view.event.wait()
            
            # Cleanup buttons
            try:
                await msg.edit(view=None)
            except:
                pass
                
            return view.value if view.value is not None else False

        except Exception as e:
            logger.error(f"Discord confirmation failed: {e}")
            return False

    async def stop(self):
        """Signal the bot to stop."""
        self._running = False
        if self.client and not self.client.is_closed():
            await self.client.close()

    async def send_message(self, channel_id: str, text: str):
        """Send a proactive message to a Discord channel."""
        if not self.client or not self._running:
            return

        try:
            channel = self.client.get_channel(int(channel_id))
            if channel:
                formatted = self.format_response(text)
                if len(formatted) > 2000:
                    chunks = self._split_message(formatted, 2000)
                    for chunk in chunks:
                        await channel.send(chunk)
                else:
                    await channel.send(formatted)
            else:
                logger.warning(f"Discord channel {channel_id} not found")
        except Exception as e:
            logger.error(f"Failed to send Discord message to {channel_id}: {e}")

    async def _handle_voice_attachment(self, message, attachment):
        """Download and transcribe a voice/audio attachment, then process it."""
        logger.info(f"Discord voice attachment from {message.author}: {attachment.filename}")

        if not self._transcribe_callback:
            await message.channel.send("My transcription module is currently offline.")
            return

        async with message.channel.typing():
            try:
                # Download the audio file
                audio_bytes = await attachment.read()
                suffix = Path(attachment.filename).suffix or ".ogg"

                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(audio_bytes)
                    tmp_path = tmp.name

                try:
                    loop = asyncio.get_event_loop()
                    transcript = await loop.run_in_executor(
                        None, self._transcribe_callback, tmp_path
                    )
                finally:
                    Path(tmp_path).unlink(missing_ok=True)

                if not transcript:
                    await message.channel.send("I couldn't hear any speech in that audio.")
                    return

                # Echo transcript back
                await message.channel.send(f"*Transcript: \"{transcript}\"*")

                # Process the transcript
                prefix = "The user sent a voice message. Here is the transcript: "
                response = await self._process_async(f"{prefix}'{transcript}'")
                formatted = self.format_response(response)

                if len(formatted) > 2000:
                    for chunk in self._split_message(formatted, 2000):
                        await message.channel.send(chunk)
                else:
                    await message.channel.send(formatted)

            except Exception as e:
                logger.error(f"Error processing Discord voice attachment: {e}")
                await message.channel.send(
                    "I encountered an error processing your audio message."
                )

    async def _handle_image_attachment(self, message, attachment, text: str = ""):
        """Download an image attachment, encode as base64, and process via vision."""
        logger.info(
            f"Discord image attachment from {message.author}: "
            f"{attachment.filename} ({text[:80] if text else 'no caption'})"
        )

        async with message.channel.typing():
            try:
                image_bytes = await attachment.read()
                image_b64 = base64.b64encode(image_bytes).decode("utf-8")

                prompt = text if text else "What is in this image?"
                response = await self._process_async(prompt, images=[image_b64])
                formatted = self.format_response(response)

                if len(formatted) > 2000:
                    for chunk in self._split_message(formatted, 2000):
                        await message.channel.send(chunk)
                else:
                    await message.channel.send(formatted)

            except Exception as e:
                logger.error(f"Error processing Discord image attachment: {e}")
                await message.channel.send(
                    "I encountered an error analyzing your image."
                )

    def format_response(self, text: str) -> str:
        """
        Discord natively supports markdown, so minimal conversion needed.
        Just clean up any HTML that might have leaked through.
        """
        # Remove HTML tags if present (from other formatters)
        import re
        text = re.sub(r"</?(?:b|i|code|pre)>", "", text)
        return text

    @staticmethod
    def _split_message(text: str, max_len: int) -> list[str]:
        """Split a long message into chunks, preserving Markdown structure."""
        from winston.utils.chunker import chunk_message
        return chunk_message(text, max_len=max_len, channel="discord")
