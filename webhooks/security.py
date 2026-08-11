"""
FastAPI Dependency Injection providers for the Webhooks module.

Re-exports from ``app.dependencies`` so that the webhooks module does not
need to import directly from the app layer (avoids circular imports while
keeping a single source of truth for all DI in ``app.dependencies``).
"""
from __future__ import annotations

from app.dependencies import get_pipeline_runner, get_webhook_verifier

__all__ = ["get_pipeline_runner", "get_webhook_verifier"]
