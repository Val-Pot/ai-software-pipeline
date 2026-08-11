"""
Telegram Bot Authentication Middleware.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Awaitable, FrozenSet
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User, Message, CallbackQuery

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseMiddleware):
    """Outer middleware checking if user ID is whitelisted."""

    def __init__(self, allowed_user_ids: FrozenSet[int]) -> None:
        super().__init__()
        self.allowed_user_ids = allowed_user_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if not user:
            return await handler(event, data)

        if self.allowed_user_ids and user.id not in self.allowed_user_ids:
            logger.warning("Unauthorized access attempt by user_id=%s (@%s)", user.id, user.username)
            if isinstance(event, Message):
                await event.answer("⛔ <b>Access Denied</b>: You are not authorized to use this bot.")
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Access Denied", show_alert=True)
            return None

        return await handler(event, data)
