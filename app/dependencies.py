"""
FastAPI Dependency Injection providers.

Every injectable service is exposed as a FastAPI dependency function here.
All singletons are stored on ``app.state`` during lifespan and retrieved
via ``Request`` — no module-level globals.

This module is the **single source of truth** for DI across the application.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from adapters.coding_agent import CodingAgentAdapter
from adapters.github import GitHubAdapter
from adapters.github.webhooks import GitHubWebhookVerifier
from adapters.telegram.notifier import TelegramNotifier
from config.settings import Settings, get_settings
from orchestrator.pipeline_runner import PipelineRunner


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def get_settings_dep(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Settings:
    """FastAPI dependency that returns the cached Settings singleton."""
    return settings


# ---------------------------------------------------------------------------
# GitHub adapters
# ---------------------------------------------------------------------------


def get_github_adapter(request: Request) -> GitHubAdapter | None:
    """Retrieve the singleton GitHubAdapter from application state."""
    return getattr(request.app.state, "github_adapter", None)


def get_coding_agent_adapter(request: Request) -> CodingAgentAdapter | None:
    """Retrieve the singleton CodingAgentAdapter from application state."""
    return getattr(request.app.state, "coding_agent_adapter", None)


def get_webhook_verifier(request: Request) -> GitHubWebhookVerifier:
    """
    Provide an HMAC webhook verifier initialised from the configured secret.
    """
    settings: Settings = getattr(request.app.state, "settings", None) or get_settings()
    return GitHubWebhookVerifier(secret=settings.github_webhook_secret)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def get_pipeline_runner(request: Request) -> PipelineRunner:
    """Retrieve the singleton PipelineRunner (Orchestrator) from app state."""
    return request.app.state.orchestrator_runner


# ---------------------------------------------------------------------------
# Telegram notifier
# ---------------------------------------------------------------------------


def get_notifier(request: Request) -> TelegramNotifier:
    """Retrieve the singleton TelegramNotifier from application state."""
    return request.app.state.notifier
