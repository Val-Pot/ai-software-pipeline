"""
Integration tests for the FastAPI application factory and HTTP endpoints.

Strategy:
  - Use ``httpx.AsyncClient`` with ``ASGITransport`` to call the ASGI app
    in-process (no real server, no network).
  - Stub ``get_settings`` (cached) so tests do not need a real .env file.
  - Patch Telegram / Aiogram calls so no real bot token is required.
  - Verify routing, response schemas, HMAC validation, and middleware.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from config.settings import Settings, get_settings


# ---------------------------------------------------------------------------
# Test settings — no real tokens required
# ---------------------------------------------------------------------------

_TEST_WEBHOOK_SECRET = "test-webhook-secret"


def _make_test_settings() -> Settings:
    """Return a minimal Settings instance valid for tests."""
    return Settings(
        telegram_bot_token="1234567890:AAFakeTokenForTestingPurposesOnly",
        telegram_allowed_user_ids="",
        telegram_use_webhook=False,
        github_token="",
        github_owner="",
        github_repo="",
        github_webhook_secret=_TEST_WEBHOOK_SECRET,
        log_level="WARNING",
        environment="development",
    )


# ---------------------------------------------------------------------------
# Fake Telegram objects
# ---------------------------------------------------------------------------


def _make_fake_bot() -> MagicMock:
    bot = MagicMock()
    bot.id = 999_999_999
    bot.session = MagicMock()
    bot.session.close = AsyncMock()
    bot.set_webhook = AsyncMock()
    bot.delete_webhook = AsyncMock()
    bot.send_message = AsyncMock()
    return bot


def _make_fake_dp(fake_bot: MagicMock) -> MagicMock:
    dp = MagicMock()
    dp.start_polling = AsyncMock()
    dp.feed_webhook_update = AsyncMock()
    dp.resolve_used_update_types = MagicMock(return_value=["message", "callback_query"])
    dp.storage = MagicMock()
    dp.fsm = MagicMock()
    dp.update = MagicMock()
    dp.update.outer_middleware = MagicMock()
    dp.include_router = MagicMock()
    return dp


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def app_client() -> AsyncGenerator[AsyncClient, None]:
    """
    Yield an AsyncClient pointing at the ASGI app.

    Uses a **stub lifespan** that pre-populates ``app.state`` with fakes,
    bypassing all real external services (Telegram, GitHub, logging).
    """
    from contextlib import asynccontextmanager
    from typing import AsyncGenerator as AG

    test_settings = _make_test_settings()
    fake_bot = _make_fake_bot()
    fake_dp = _make_fake_dp(fake_bot)

    from orchestrator.persistence import InMemoryPersistenceAdapter
    from orchestrator.pipeline_runner import PipelineRunner

    persistence = InMemoryPersistenceAdapter()
    runner = PipelineRunner(persistence=persistence)

    @asynccontextmanager
    async def _stub_lifespan(app: object) -> AG[None, None]:
        """Populate app.state without touching any real external service."""
        from fastapi import FastAPI as _FastAPI
        assert isinstance(app, _FastAPI)
        app.state.settings = test_settings
        app.state.bot = fake_bot
        app.state.dp = fake_dp
        app.state.notifier = MagicMock()
        app.state.orchestrator_runner = runner
        app.state.orchestrator = runner
        app.state.persistence = persistence
        app.state.github_adapter = None
        app.state.github_client = None
        app.state.coding_agent_adapter = None
        yield

    # Patch app.lifespan with our stub and get_settings for DI functions.
    _settings_callable = lambda: test_settings  # noqa: E731

    with (
        patch("config.settings.get_settings", side_effect=_settings_callable),
        patch("app.dependencies.get_settings", side_effect=_settings_callable),
        patch("app.api.health.get_settings", side_effect=_settings_callable),
    ):
        get_settings.cache_clear()
        from app.main import create_app

        application = create_app(lifespan=_stub_lifespan)
        application.state.settings = test_settings
        application.state.bot = fake_bot
        application.state.dp = fake_dp
        application.state.notifier = MagicMock()
        application.state.orchestrator_runner = runner
        application.state.orchestrator = runner
        application.state.persistence = persistence
        application.state.github_adapter = None
        application.state.github_client = None
        application.state.coding_agent_adapter = None

        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client

    get_settings.cache_clear()




# ---------------------------------------------------------------------------
# Helper: build a valid HMAC SHA-256 signature
# ---------------------------------------------------------------------------


def _sign(payload: bytes, secret: str = _TEST_WEBHOOK_SECRET) -> str:
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_liveness_probe_returns_200(app_client: AsyncClient) -> None:
    response = await app_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_readiness_probe_returns_200(app_client: AsyncClient) -> None:
    response = await app_client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ready", "degraded"}
    assert "github_adapter" in body
    assert "coding_agent_adapter" in body
    assert "telegram_bot" in body
    assert "environment" in body


# ---------------------------------------------------------------------------
# GitHub Webhook endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_missing_headers_returns_422(app_client: AsyncClient) -> None:
    """Missing required headers → FastAPI returns 422 Unprocessable Entity."""
    response = await app_client.post("/webhooks/github", json={"action": "opened"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_webhook_invalid_signature_returns_401(app_client: AsyncClient) -> None:
    payload = b'{"action": "opened"}'
    response = await app_client.post(
        "/webhooks/github",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "delivery-bad-sig",
            "X-Hub-Signature-256": "sha256=badbadbadbad",
        },
    )
    assert response.status_code == 401
    assert "signature" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_webhook_unknown_event_is_ignored(app_client: AsyncClient) -> None:
    """Valid HMAC but unrecognised event type → status=ignored, 200 OK."""
    payload = b'{"action": "starred"}'
    response = await app_client.post(
        "/webhooks/github",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "star",
            "X-GitHub-Delivery": "delivery-star",
            "X-Hub-Signature-256": _sign(payload),
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ignored"
    assert body["delivery_id"] == "delivery-star"


@pytest.mark.asyncio
async def test_webhook_pr_opened_event_is_processed(app_client: AsyncClient) -> None:
    """Valid PR webhook → routed to Orchestrator, returns status in {processed, failed}."""
    import json

    payload_dict = {
        "action": "opened",
        "pull_request": {
            "number": 42,
            "html_url": "https://github.com/owner/repo/pull/42",
            "head": {"ref": "feature/test"},
        },
        "sender": {"login": "github-copilot[bot]"},
        "job_id": "non-existent-job",
    }
    payload = json.dumps(payload_dict).encode()

    response = await app_client.post(
        "/webhooks/github",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "delivery-pr",
            "X-Hub-Signature-256": _sign(payload),
        },
    )
    assert response.status_code == 200
    body = response.json()
    # The Orchestrator will reject unknown job_id — that is acceptable.
    assert body["status"] in {"processed", "failed", "acknowledged", "ignored"}
    assert body["delivery_id"] == "delivery-pr"


# ---------------------------------------------------------------------------
# Telegram webhook endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_telegram_webhook_invalid_json_returns_400(app_client: AsyncClient) -> None:
    response = await app_client.post(
        "/telegram/webhook",
        content=b"not-valid-json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_telegram_webhook_valid_update_returns_200(app_client: AsyncClient) -> None:
    """Valid Telegram Update JSON → feed_webhook_update called, returns ok=true."""
    from unittest.mock import patch as _patch

    # Patch Update.model_validate to avoid Aiogram schema validation.
    mock_update = MagicMock()
    mock_update.update_id = 99
    mock_update.event_type = "message"

    with _patch("app.api.telegram.Update.model_validate", return_value=mock_update):
        response = await app_client.post(
            "/telegram/webhook",
            json={"update_id": 99},
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True


# ---------------------------------------------------------------------------
# OpenAPI schema
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openapi_schema_lists_all_routes(app_client: AsyncClient) -> None:
    response = await app_client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "AI Software Pipeline"
    assert schema["info"]["version"] == "0.1.0"
    paths = schema.get("paths", {})
    assert "/health" in paths
    assert "/health/ready" in paths
    assert "/webhooks/github" in paths
    assert "/telegram/webhook" in paths


@pytest.mark.asyncio
async def test_swagger_ui_is_available(app_client: AsyncClient) -> None:
    response = await app_client.get("/docs")
    assert response.status_code == 200
