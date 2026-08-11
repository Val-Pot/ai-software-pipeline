"""
Bot factory for creating initialized Aiogram Bot instances.
"""
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode


def create_bot(token: str) -> Bot:
    """Create and return an Aiogram Bot with HTML parse mode enabled globally."""
    return Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
