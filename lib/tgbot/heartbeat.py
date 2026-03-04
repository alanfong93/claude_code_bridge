"""
Heartbeat status message system for CCB Telegram bot.

Provides visual feedback to the user while an AI provider is working:
- Self-editing status message with elapsed time
- Cycling indicator characters
- Continuous typing action (re-sent every 4s since Telegram expires it at 5s)
- Deletes status message when task completes
"""

import asyncio
import time
from typing import Optional

from telegram import Bot
from telegram.constants import ChatAction
from telegram.error import TelegramError

from .config import TelegramConfig


class Heartbeat:
    """Manages a self-editing status message with typing indicator.

    Usage:
        hb = Heartbeat(bot, chat_id, provider, config)
        await hb.start()
        # ... wait for AI to finish ...
        await hb.stop()
    """

    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        provider: str,
        config: TelegramConfig,
    ):
        self.bot = bot
        self.chat_id = chat_id
        self.provider = provider
        self.config = config
        self._status_message_id: Optional[int] = None
        self._start_time: float = 0
        self._indicator_index: int = 0
        self._typing_task: Optional[asyncio.Task] = None
        self._edit_task: Optional[asyncio.Task] = None
        self._stopped = False

    @property
    def _indicators(self) -> str:
        return self.config.heartbeat.indicators

    def _next_indicator(self) -> str:
        """Get next cycling indicator character."""
        char = self._indicators[self._indicator_index % len(self._indicators)]
        self._indicator_index += 1
        return char

    def _format_elapsed(self) -> str:
        """Format elapsed time as human-readable string."""
        elapsed = int(time.time() - self._start_time)
        if elapsed < 60:
            return f"{elapsed}s"
        minutes = elapsed // 60
        seconds = elapsed % 60
        if minutes < 60:
            return f"{minutes}m {seconds}s"
        hours = minutes // 60
        minutes = minutes % 60
        return f"{hours}h {minutes}m"

    def _status_text(self) -> str:
        """Build the current status message text."""
        indicator = self._next_indicator()
        elapsed = self._format_elapsed()
        return f"[ {indicator} ] {self.provider.capitalize()} is working... ({elapsed})"

    async def start(self) -> None:
        """Start the heartbeat: send initial status message and begin loops."""
        self._start_time = time.time()
        self._stopped = False

        # Send initial status message
        try:
            msg = await self.bot.send_message(
                chat_id=self.chat_id,
                text=f"[ {self._next_indicator()} ] Starting {self.provider.capitalize()}... (0s)",
            )
            self._status_message_id = msg.message_id
        except TelegramError:
            # If we can't send status, continue without heartbeat
            return

        # Start background loops
        self._typing_task = asyncio.create_task(self._typing_loop())
        self._edit_task = asyncio.create_task(self._edit_loop())

    async def stop(self) -> None:
        """Stop the heartbeat and delete the status message."""
        self._stopped = True

        # Cancel background tasks
        if self._typing_task and not self._typing_task.done():
            self._typing_task.cancel()
            try:
                await self._typing_task
            except asyncio.CancelledError:
                pass

        if self._edit_task and not self._edit_task.done():
            self._edit_task.cancel()
            try:
                await self._edit_task
            except asyncio.CancelledError:
                pass

        # Delete status message
        if self._status_message_id:
            try:
                await self.bot.delete_message(
                    chat_id=self.chat_id,
                    message_id=self._status_message_id,
                )
            except TelegramError:
                pass  # Message may already be deleted or too old
            self._status_message_id = None

    async def _typing_loop(self) -> None:
        """Send typing action every N seconds."""
        interval = self.config.heartbeat.typing_interval_seconds
        while not self._stopped:
            try:
                await self.bot.send_chat_action(
                    chat_id=self.chat_id,
                    action=ChatAction.TYPING,
                )
            except TelegramError:
                pass
            await asyncio.sleep(interval)

    async def _edit_loop(self) -> None:
        """Edit status message every N seconds with updated elapsed time."""
        interval = self.config.heartbeat.interval_seconds
        while not self._stopped:
            await asyncio.sleep(interval)
            if self._stopped or not self._status_message_id:
                break
            try:
                await self.bot.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=self._status_message_id,
                    text=self._status_text(),
                )
            except TelegramError:
                pass  # Edit may fail if message was deleted
