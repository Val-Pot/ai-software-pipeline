"""telegram_bot package init."""
from telegram_bot.bot import create_bot
from telegram_bot.dispatcher import create_dispatcher

__all__ = ["create_bot", "create_dispatcher"]
