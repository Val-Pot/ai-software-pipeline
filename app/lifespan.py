from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher
from fastapi import FastAPI

from adapters.telegram.handlers import register_handlers
from app.container import AppContainer
from config.settings import Settings
from webhooks.router import build_webhook_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    container: AppContainer = app.state.container
    runner = container.runner
    polling_task = None
    bot = getattr(app.state, "bot", None)
    dispatcher = getattr(app.state, "dispatcher", None)
    if bot is not None and dispatcher is not None:
        polling_task = asyncio.create_task(dispatcher.start_polling(bot))
        app.state.polling_task = polling_task
    await runner.recover_active_jobs()
    try:
        yield
    finally:
        runner.cancel_watchers()
        if polling_task is not None:
            polling_task.cancel()
        await container.aclose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    bot = Bot(settings.telegram_bot_token) if settings.telegram_bot_token else None
    container = AppContainer(settings, bot=bot)
    app = FastAPI(title="AI Software Pipeline", lifespan=lifespan)
    app.state.container = container
    app.state.bot = bot
    app.state.dispatcher = None
    app.state.polling_task = None
    app.include_router(build_webhook_router(container))

    @app.get("/health")
    async def health():
        return {"ok": True}

    if bot is not None:
        dp = Dispatcher()
        register_handlers(dp, container.telegram_handlers)
        app.state.dispatcher = dp

    return app
