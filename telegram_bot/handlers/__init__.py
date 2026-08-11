"""handlers package init."""
from telegram_bot.handlers.commands import router as commands_router
from telegram_bot.handlers.messages import router as messages_router
from telegram_bot.handlers.callbacks import router as callbacks_router

__all__ = ["commands_router", "messages_router", "callbacks_router"]
