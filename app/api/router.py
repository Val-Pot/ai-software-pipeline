"""
Central API router — combines all sub-routers into a single mount.

Registered routers (in order):
  - Health        : /health, /health/ready
  - GitHub Webhooks: /webhooks/github
  - Telegram Webhook: /telegram/webhook
"""
from fastapi import APIRouter

from app.api.health import router as health_router
from app.api.telegram import router as telegram_router
from webhooks.router import router as webhooks_router

router = APIRouter()
router.include_router(health_router)
router.include_router(webhooks_router)
router.include_router(telegram_router)
