from __future__ import annotations

from io import BytesIO

from aiogram import Bot
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)


class TelegramNotifier:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send_text(self, chat_id: int, text: str) -> None:
        await self._bot.send_message(chat_id, text)

    async def send_document(
        self, chat_id: int, filename: str, content: bytes, caption: str = ""
    ) -> None:
        document = BufferedInputFile(content, filename=filename)
        await self._bot.send_document(chat_id, document, caption=caption)

    async def send_merge_confirmation(
        self, chat_id: int, job_id: str, text: str
    ) -> None:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Merge", callback_data=f"merge:confirm:{job_id}"),
                    InlineKeyboardButton(text="Cancel", callback_data=f"merge:cancel:{job_id}"),
                ]
            ]
        )
        await self._bot.send_message(chat_id, text, reply_markup=keyboard)
