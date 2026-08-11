"""
Dispatcher factory for Aiogram 3.
"""
from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config.settings import Settings
from adapters.base import OrchestratorPort
from telegram_bot.middlewares import AuthMiddleware
from telegram_bot.handlers import commands_router, messages_router, callbacks_router


def create_dispatcher(settings: Settings, orchestrator: OrchestratorPort) -> Dispatcher:
    """Create and configure Dispatcher with storage, middlewares, and routers."""
    dp = Dispatcher(storage=MemoryStorage())

    # Register dependency injection data for handlers
    dp["orchestrator"] = orchestrator
    dp["settings"] = settings

    # Register outer authentication middleware
    dp.update.outer_middleware(AuthMiddleware(allowed_user_ids=settings.allowed_user_ids))

    # Include handler routers
    dp.include_router(commands_router)
    dp.include_router(messages_router)
    dp.include_router(callbacks_router)

    return dp
