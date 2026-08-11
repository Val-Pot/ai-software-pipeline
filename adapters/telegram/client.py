"""
Thin wrapper over Aiogram Bot instance for outbound messaging.
"""
from __future__ import annotations

import logging
from typing import Optional
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup

logger = logging.getLogger(__name__)


class TelegramBotClient:
    """Encapsulates direct calls to Telegram via Aiogram Bot."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: Optional[InlineKeyboardMarkup] = None,
    ) -> None:
        """Send message via Aiogram bot instance."""
        try:
            await self._bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                disable_web_page_preview=False,
            )
            logger.debug("Successfully sent message to chat_id=%s", chat_id)
        except Exception as e:
            logger.error("Failed to send message to chat_id=%s: %s", chat_id, e, exc_info=True)
            raise
