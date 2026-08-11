"""
AI Software Pipeline — FastAPI application entry point.

This module is the **composition root** of the application.  It:

  1. Builds the FastAPI application via ``create_app()``.
  2. Attaches the lifespan context manager (startup / shutdown wiring).
  3. Mounts all API routers.
  4. Registers global exception handlers.
  5. Configures OpenAPI metadata.

The module-level ``app`` object is the ASGI target used by the server:

    uvicorn app.main:app --host 0.0.0.0 --port 8000

No business logic, no adapter construction — all of that lives in
``app/lifespan.py`` and ``app/dependencies.py``.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Callable, Optional

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import router as api_router
from app.lifespan import lifespan as _default_lifespan

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application metadata
# ---------------------------------------------------------------------------

_TITLE = "AI Software Pipeline"
_DESCRIPTION = (
    "Automated pipeline: Telegram → GitHub Issue → GitHub Copilot → "
    "Pull Request → CI → AI Review → Telegram."
)
_VERSION = "0.1.0"
_CONTACT = {
    "name": "AI Pipeline Team",
    "url": "https://github.com/your-org/ai-software-pipeline",
}
_LICENSE = {
    "name": "MIT",
    "url": "https://opensource.org/licenses/MIT",
}
_TAGS_METADATA = [
    {
        "name": "Health",
        "description": "Liveness and readiness probes for Kubernetes / load balancers.",
    },
    {
        "name": "GitHub Webhooks",
        "description": "Receives GitHub webhook events (issues, PRs, Actions) and routes them to the Orchestrator.",
    },
    {
        "name": "Telegram Webhook",
        "description": "Receives Telegram Bot updates via webhook (active when TELEGRAM_USE_WEBHOOK=true).",
    },
]


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app(
    lifespan: Optional[Callable] = None,
) -> FastAPI:
    """
    Build and return the configured FastAPI application.

    Parameters
    ----------
    lifespan:
        Optional async context manager used as the application lifespan.
        Defaults to the production lifespan from ``app.lifespan``.
        Pass a stub in tests to avoid real external service initialisation.
    """
    _lifespan = lifespan or _default_lifespan
    application = FastAPI(
        title=_TITLE,
        description=_DESCRIPTION,
        version=_VERSION,
        contact=_CONTACT,
        license_info=_LICENSE,
        openapi_tags=_TAGS_METADATA,
        lifespan=_lifespan,
        # Disable the automatic redirect for trailing slashes — avoids
        # surprising behaviour with Telegram/GitHub POSTs.
        redirect_slashes=False,
    )


    # ---- Middlewares -------------------------------------------------------

    # CORS: allow all origins in development; tighten in production via env.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    # ---- Routes ------------------------------------------------------------
    application.include_router(api_router)

    # ---- Global exception handlers ----------------------------------------
    _register_exception_handlers(application)

    logger.debug("FastAPI application created: %s v%s", _TITLE, _VERSION)
    return application


def _register_exception_handlers(application: FastAPI) -> None:
    """Attach global exception handlers to the FastAPI application."""

    @application.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """
        Catch-all handler for unhandled exceptions.

        Logs the full traceback and returns a generic 500 response so that
        internal details are never leaked to clients.
        """
        logger.error(
            "Unhandled exception on %s %s: %s",
            request.method,
            request.url.path,
            exc,
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "An unexpected error occurred. Please try again later."},
        )


# ---------------------------------------------------------------------------
# ASGI entry point
# ---------------------------------------------------------------------------

app = create_app()
