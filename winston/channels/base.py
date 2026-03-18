"""
Base channel abstraction and channel manager for W.I.N.S.T.O.N.
Channels receive messages from external platforms and route them
through the WINSTON processing pipeline.
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Callable, Optional, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from winston.core.safety import ActionRequest

logger = logging.getLogger("winston.channels")


class BaseChannel(ABC):
    """
    Abstract base for messaging channels (Telegram, Discord, etc.).

    Each channel:
    - Receives messages from an external platform
    - Calls process_fn(text) to get a WINSTON response
    - Sends the response back to the platform
    - Supports proactive messaging via send_message()
    """

    name: str = "base"

    def __init__(self, config: dict, process_fn: Callable[[str], str]):
        """
        Args:
            config: Channel-specific configuration dict
            process_fn: Reference to Winston.process_input() or equivalent
        """
        self.config = config
        self.process_fn = process_fn
        self._running = False
        self.last_event_at: Optional[float] = None
        self.last_start_at: Optional[float] = None

    @abstractmethod
    async def start(self):
        """Start listening for messages. Called as an asyncio task."""
        pass

    @abstractmethod
    async def stop(self):
        """Graceful shutdown."""
        pass

    @abstractmethod
    async def send_message(self, channel_id: str, text: str):
        """Send a proactive message to a specific channel/chat."""
        pass

    @abstractmethod
    async def request_user_confirmation(self, action_req: 'ActionRequest') -> bool:
        """Ask the user to confirm a pending action via this channel."""
        pass

    @abstractmethod
    def format_response(self, text: str) -> str:
        """Format WINSTON response for this channel's markup."""
        pass

    def record_event(self):
        """Record that an event (message) was received on this channel."""
        self.last_event_at = time.time()

    def is_running(self) -> bool:
        return self._running

    async def _process_async(self, text: str, images: list[str] = None, return_dict: bool = True) -> Union[str, dict]:
        """Run the synchronous process_fn in a thread to avoid blocking.
        If return_dict is True, returns a dict with 'message' and 'data'.
        """
        return await asyncio.to_thread(self.process_fn, text, channel=self, images=images, return_dict=return_dict)


class ChannelManager:
    """
    Manages all active messaging channels.
    Provides broadcast capability for scheduled task results.
    """

    def __init__(self):
        self._channels: dict[str, BaseChannel] = {}
        self._tasks: list[asyncio.Task] = []

    def register(self, channel: BaseChannel):
        """Register a channel instance."""
        self._channels[channel.name] = channel
        logger.info(f"Registered channel: {channel.name}")

    async def start_all(self):
        """Start all registered channels as concurrent tasks."""
        if not self._channels:
            return

        tasks = []
        for name, channel in self._channels.items():
            logger.info(f"Starting channel: {name}")
            task = asyncio.create_task(
                self._run_channel(name, channel),
                name=f"channel-{name}",
            )
            tasks.append(task)

        self._tasks = tasks
        # Wait for all channel tasks (they run until stopped)
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_channel(self, name: str, channel: BaseChannel):
        """Run a single channel with error recovery."""
        try:
            await channel.start()
        except asyncio.CancelledError:
            logger.info(f"Channel {name} cancelled")
        except Exception as e:
            logger.error(f"Channel {name} crashed: {e}")

    async def stop_all(self):
        """Stop all channels gracefully."""
        for name, channel in self._channels.items():
            try:
                await channel.stop()
                logger.info(f"Stopped channel: {name}")
            except Exception as e:
                logger.error(f"Error stopping channel {name}: {e}")

        # Cancel any remaining tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()

    async def broadcast(self, message: str, targets: dict[str, str] = None):
        """
        Send a message to all channels (or specific targets).
        Used by the scheduler to push task results to active channels.

        Args:
            message: The message text to broadcast
            targets: Optional dict of {channel_name: channel_id}.
                     If None, sends to default targets of all channels.
        """
        for name, channel in self._channels.items():
            if not channel.is_running():
                continue

            try:
                if targets and name in targets:
                    channel_id = targets[name]
                else:
                    # Use default target from channel config
                    channel_id = channel.config.get("default_chat_id") or channel.config.get("default_channel_id", "")

                if channel_id:
                    formatted = channel.format_response(message)
                    await channel.send_message(channel_id, formatted)
            except Exception as e:
                logger.error(f"Broadcast to {name} failed: {e}")

    def get_channel(self, name: str) -> Optional[BaseChannel]:
        """Get a specific channel by name."""
        return self._channels.get(name)

    def list_channels(self) -> list[dict]:
        """List all registered channels and their status."""
        return [
            {"name": name, "running": channel.is_running()}
            for name, channel in self._channels.items()
        ]
