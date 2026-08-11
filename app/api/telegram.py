"""
FastAPI route for Telegram webhook updates.

When the application runs in webhook mode (``TELEGRAM_USE_WEBHOOK=true``),
Telegram POSTs update objects to this endpoint instead of the bot using
long-polling.  The route feeds the raw update directly into the Aiogram
Dispatcher for normal handler processing.
"""
from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Telegram Webhook"])


@router.post(
    "/telegram/webhook",
    status_code=status.HTTP_200_OK,
    summary="Telegram Webhook Receiver",
    description=(
        "Receives Telegram Bot updates via webhook (active only when "
        "TELEGRAM_USE_WEBHOOK=true). Feeds updates into the Aiogram Dispatcher."
    ),
    include_in_schema=True,
)
async def telegram_webhook(request: Request) -> JSONResponse:
    """
    Process an incoming Telegram webhook update.

    Aiogram's ``feed_webhook_update`` handles the update exactly as it
    would in polling mode — all registered handlers and middlewares apply.
    """
    bot: Bot = request.app.state.bot
    dp: Dispatcher = request.app.state.dp

    try:
        body = await request.json()
    except Exception as exc:
        logger.error("Failed to parse Telegram webhook JSON body: %s", exc)
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"ok": False})

    update = Update.model_validate(body)
    logger.debug("Telegram webhook update_id=%s type=%s", update.update_id, update.event_type)

    await dp.feed_webhook_update(bot=bot, update=update)
    return JSONResponse(status_code=status.HTTP_200_OK, content={"ok": True})
