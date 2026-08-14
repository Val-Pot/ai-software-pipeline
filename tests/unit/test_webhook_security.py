"""
Integration & Security unit tests for FastAPI GitHub Webhooks Receiver.
"""
import hmac
import hashlib
import json
from urllib.parse import urlencode

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from orchestrator import InMemoryPersistenceAdapter, PipelineRunner


@pytest.fixture
def webhook_secret():
    return "test_webhook_secret_321"


@pytest.fixture(autouse=True)
def setup_app_state(monkeypatch, webhook_secret):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_bot_token_123")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", webhook_secret)

    persistence = InMemoryPersistenceAdapter()
    runner = PipelineRunner(persistence=persistence)
    app.state.orchestrator_runner = runner
    app.state.persistence = persistence
    app.state.coding_agent_adapter = None


@pytest.mark.asyncio
async def test_webhook_unauthorized_missing_signature():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/webhooks/github",
            json={"action": "opened"},
            headers={"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "delivery_001"},
        )
        assert response.status_code == 422  # Missing header validation error


@pytest.mark.asyncio
async def test_webhook_invalid_signature(webhook_secret):
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "delivery_002",
        "X-Hub-Signature-256": "sha256=invalid_signature",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/webhooks/github", json={"action": "opened"}, headers=headers)
        assert response.status_code == 401
        assert "Invalid HMAC" in response.json()["detail"]


@pytest.mark.asyncio
async def test_webhook_valid_signature_and_forwarding(webhook_secret):
    runner: PipelineRunner = app.state.orchestrator_runner
    job = await runner.create_and_start_job(100, 200, "user", "Add webhook feature")

    payload = {
        "action": "opened",
        "job_id": job.job_id,
        "pull_request": {"number": 10, "html_url": "https://github.com/pr/10", "head": {"ref": "feature"}},
    }
    raw_body = str(payload).replace("'", '"').encode("utf-8")
    sig = hmac.new(webhook_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

    headers = {
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "delivery_003",
        "X-Hub-Signature-256": f"sha256={sig}",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/webhooks/github", content=raw_body, headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "processed"
        assert data["event_type"] == "pr_opened"
        assert data["delivery_id"] == "delivery_003"


@pytest.mark.asyncio
async def test_webhook_form_urlencoded_payload(webhook_secret):
    runner: PipelineRunner = app.state.orchestrator_runner
    job = await runner.create_and_start_job(101, 201, "user", "Form payload")

    payload = {
        "action": "opened",
        "job_id": job.job_id,
        "pull_request": {"number": 11, "html_url": "https://github.com/pr/11", "head": {"ref": "feature"}},
    }
    raw_json = json.dumps(payload)
    form_body = urlencode({"payload": raw_json}).encode("utf-8")
    sig = hmac.new(webhook_secret.encode("utf-8"), form_body, hashlib.sha256).hexdigest()

    headers = {
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "delivery_004",
        "X-Hub-Signature-256": f"sha256={sig}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/webhooks/github", content=form_body, headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "processed"
        assert data["event_type"] == "pr_opened"


@pytest.mark.asyncio
async def test_webhook_pr_opened_without_job_id_matches_issue(webhook_secret):
    from orchestrator.states import PipelineState

    runner: PipelineRunner = app.state.orchestrator_runner
    job = await runner.create_and_start_job(102, 202, "user", "No job_id in payload")
    stored = await runner.persistence.load_job(job.job_id)
    await runner.persistence.save_job(stored.model_copy(update={"issue_number": 1}))

    payload = {
        "action": "opened",
        "pull_request": {
            "number": 2,
            "html_url": "https://github.com/Val-Pot/ai-pipe/pull/2",
            "head": {"ref": "copilot/fix-auth"},
            "body": "Fixes #1",
        },
        "sender": {"login": "copilot-swe-agent[bot]"},
    }
    raw_body = json.dumps(payload).encode("utf-8")
    sig = hmac.new(webhook_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "delivery_005",
        "X-Hub-Signature-256": f"sha256={sig}",
        "Content-Type": "application/json",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/webhooks/github", content=raw_body, headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "processed"
    updated = await runner.persistence.load_job(job.job_id)
    assert updated.state == PipelineState.WAIT_TESTS
    assert updated.pr_number == 2
