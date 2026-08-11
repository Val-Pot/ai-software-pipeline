"""
FastAPI health check endpoints.

Exposes two endpoints:
  - ``GET /health``  — Kubernetes liveness probe (always 200 if process is up).
  - ``GET /health/ready`` — Kubernetes readiness probe (checks adapter availability).
"""
from __future__ import annotations

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    status: str = "ok"


class ReadinessResponse(BaseModel):
    status: str
    github_adapter: str
    coding_agent_adapter: str
    telegram_bot: str
    environment: str
    version: str = "0.1.0"


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe",
    description="Returns HTTP 200 when the process is alive.",
)
async def health_check() -> HealthResponse:
    """Kubernetes liveness probe — always returns 200 OK."""
    return HealthResponse(status="ok")


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    description="Checks all adapter singletons are initialised before serving traffic.",
)
async def readiness_check(
    request: Request,
) -> ReadinessResponse:
    """
    Kubernetes readiness probe.

    Returns ``status=ready`` only when all critical adapters are available.
    Individual adapter statuses are reported regardless.
    """
    settings: Settings = getattr(request.app.state, "settings", None) or get_settings()
    github_ok = getattr(request.app.state, "github_adapter", None) is not None
    agent_ok = getattr(request.app.state, "coding_agent_adapter", None) is not None
    bot_ok = getattr(request.app.state, "bot", None) is not None

    all_ready = bot_ok  # Telegram is always required; GitHub is optional in dev.

    return ReadinessResponse(
        status="ready" if all_ready else "degraded",
        github_adapter="ok" if github_ok else "disabled",
        coding_agent_adapter="ok" if agent_ok else "disabled",
        telegram_bot="ok" if bot_ok else "unavailable",
        environment=settings.environment,
    )
