"""
Unit tests for GitHub Adapter, Webhook HMAC Verification, and Event Parser.
"""
import pytest
import hmac
import hashlib
import respx
from httpx import Response

from adapters.github import (
    GitHubAdapter,
    GitHubWebhookVerifier,
    GitHubWebhookParser,
    WebhookVerificationError,
)
from orchestrator.context import PipelineJob


@pytest.fixture
def github_adapter():
    return GitHubAdapter(
        token="test_token",
        owner="test_owner",
        repo="test_repo",
    )


@pytest.mark.asyncio
@respx.mock
async def test_create_issue(github_adapter):
    respx.post("https://api.github.com/repos/test_owner/test_repo/issues").mock(
        return_value=Response(
            201,
            json={
                "id": 101,
                "number": 42,
                "title": "[Pipeline Task] Implement auth feature",
                "body": "Task details",
                "state": "open",
                "html_url": "https://github.com/test_owner/test_repo/issues/42",
                "user": {"login": "test_user", "id": 1, "type": "User"},
            },
        )
    )

    job = PipelineJob(chat_id=1, user_id=1, task_description="Implement auth feature")
    updated_job = await github_adapter.create_issue(job)

    assert updated_job.issue_number == 42
    assert updated_job.issue_url == "https://github.com/test_owner/test_repo/issues/42"


def test_webhook_hmac_verifier_success():
    secret = "secret_key_123"
    verifier = GitHubWebhookVerifier(secret=secret)

    payload = b'{"action": "opened"}'
    sig = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    header = f"sha256={sig}"

    assert verifier.verify(payload, header) is True


def test_webhook_hmac_verifier_failure():
    verifier = GitHubWebhookVerifier(secret="secret_key_123")
    payload = b'{"action": "opened"}'
    invalid_header = "sha256=invalid_signature"

    with pytest.raises(WebhookVerificationError):
        verifier.verify(payload, invalid_header)


def test_copilot_question_detection():
    payload = {
        "action": "created",
        "comment": {"body": "Should I use OAuth2 or JWT for authentication?"},
        "sender": {"login": "github-copilot[bot]"},
        "issue": {"number": 42},
    }

    event = GitHubWebhookParser.parse_event("issue_comment", payload)
    assert event is not None
    assert event["event_type"] == "copilot_question"
    assert event["issue_number"] == 42
    assert "OAuth2" in event["question"]
