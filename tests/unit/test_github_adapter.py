"""
Unit tests for GitHub Adapter, Webhook HMAC Verification, and Event Parser.
"""
import json
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
from adapters.github.webhooks import parse_webhook_payload, extract_github_event_refs
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


@pytest.mark.asyncio
@respx.mock
async def test_assign_copilot_posts_swe_agent_assignee(github_adapter):
    respx.get("https://api.github.com/repos/test_owner/test_repo").mock(
        return_value=Response(200, json={"default_branch": "main", "size": 12})
    )
    respx.get("https://api.github.com/repos/test_owner/test_repo/git/ref/heads/main").mock(
        return_value=Response(200, json={"ref": "refs/heads/main", "object": {"sha": "abc"}})
    )
    assignees_route = respx.post(
        "https://api.github.com/repos/test_owner/test_repo/issues/42/assignees"
    ).mock(
        return_value=Response(
            201,
            json={
                "id": 101,
                "number": 42,
                "title": "task",
                "body": "details",
                "state": "open",
                "html_url": "https://github.com/test_owner/test_repo/issues/42",
                "user": {"login": "test_user", "id": 1, "type": "User"},
                "assignees": [{"login": "copilot-swe-agent[bot]", "id": 2, "type": "Bot"}],
            },
        )
    )
    labels_route = respx.post(
        "https://api.github.com/repos/test_owner/test_repo/issues/42/labels"
    ).mock(return_value=Response(200, json=[{"name": "copilot-agent"}]))

    result = await github_adapter.issues.assign_copilot(42, "github-copilot[bot]")

    assert result is True
    assert assignees_route.called
    sent = assignees_route.calls.last.request
    body = json.loads(sent.content)
    assert body["assignees"] == ["copilot-swe-agent[bot]"]
    assert body["agent_assignment"]["target_repo"] == "test_owner/test_repo"
    assert body["agent_assignment"]["base_branch"] == "main"
    assert labels_route.called


@pytest.mark.asyncio
@respx.mock
async def test_assign_copilot_bootstraps_empty_repo(github_adapter):
    respx.get("https://api.github.com/repos/test_owner/test_repo").mock(
        return_value=Response(200, json={"default_branch": "main", "size": 0})
    )
    respx.get("https://api.github.com/repos/test_owner/test_repo/git/ref/heads/main").mock(
        return_value=Response(409, json={"message": "Git Repository is empty."})
    )
    bootstrap = respx.put("https://api.github.com/repos/test_owner/test_repo/contents/README.md").mock(
        return_value=Response(201, json={"content": {"path": "README.md"}})
    )
    respx.post("https://api.github.com/repos/test_owner/test_repo/issues/42/assignees").mock(
        return_value=Response(
            201,
            json={
                "id": 101,
                "number": 42,
                "title": "task",
                "body": "details",
                "state": "open",
                "html_url": "https://github.com/test_owner/test_repo/issues/42",
                "user": {"login": "test_user", "id": 1, "type": "User"},
                "assignees": [{"login": "copilot-swe-agent[bot]", "id": 2, "type": "Bot"}],
            },
        )
    )
    respx.post("https://api.github.com/repos/test_owner/test_repo/issues/42/labels").mock(
        return_value=Response(200, json=[{"name": "copilot-agent"}])
    )

    result = await github_adapter.issues.assign_copilot(42, "github-copilot[bot]")

    assert result is True
    assert bootstrap.called


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


def test_parse_webhook_payload_json():
    payload = parse_webhook_payload(b'{"action": "labeled", "issue": {"number": 1}}')
    assert payload["action"] == "labeled"
    assert payload["issue"]["number"] == 1


def test_parse_webhook_payload_form_urlencoded():
    body = b'payload=%7B%22action%22%3A%22opened%22%7D'
    payload = parse_webhook_payload(body)
    assert payload == {"action": "opened"}


def test_parse_webhook_payload_empty_raises():
    with pytest.raises(ValueError, match="Empty webhook body"):
        parse_webhook_payload(b"")


def test_extract_refs_from_issue_body_job_id():
    job_id = "238179f6-1234-1234-1234-1234567890ab"
    refs = extract_github_event_refs(
        {
            "issue": {
                "number": 1,
                "body": f"### Pipeline Job Task\n\n**Job ID:** `{job_id}`\n",
            }
        }
    )
    assert refs["job_id"] == job_id
    assert refs["issue_number"] == 1


def test_extract_refs_fixes_issue_from_pr_body():
    refs = extract_github_event_refs(
        {
            "pull_request": {
                "number": 9,
                "body": "Fixes #7\n\nImplemented auth.",
            }
        }
    )
    assert refs["pr_number"] == 9
    assert refs["issue_number"] == 7


def test_extract_refs_from_workflow_run_pull_requests():
    refs = extract_github_event_refs(
        {
            "workflow_run": {
                "id": 55,
                "pull_requests": [{"number": 9}],
            }
        }
    )
    assert refs["pr_number"] == 9


def test_extract_refs_from_workflow_run_includes_pr_url():
    refs = extract_github_event_refs(
        {
            "workflow_run": {
                "id": 55,
                "pull_requests": [
                    {
                        "number": 9,
                        "html_url": "https://github.com/o/r/pull/9",
                    }
                ],
            }
        }
    )
    assert refs["pr_number"] == 9
    assert refs["pr_url"] == "https://github.com/o/r/pull/9"


def test_extract_refs_ignores_active_job_sentinel():
    refs = extract_github_event_refs({"job_id": "active_job", "issue": {"number": 3}})
    assert "job_id" not in refs
    assert refs["issue_number"] == 3


def test_parse_event_pull_request_includes_linked_issue():
    event = GitHubWebhookParser.parse_event(
        "pull_request",
        {
            "action": "opened",
            "pull_request": {
                "number": 4,
                "html_url": "https://github.com/o/r/pull/4",
                "head": {"ref": "copilot/fix"},
                "body": "Closes #1",
            },
            "sender": {"login": "copilot-swe-agent[bot]"},
        },
    )
    assert event is not None
    assert event["event_type"] == "pr_opened"
    assert event["pr_number"] == 4
    assert event["issue_number"] == 1


def test_parse_event_workflow_run_includes_pr_number():
    event = GitHubWebhookParser.parse_event(
        "workflow_run",
        {
            "action": "completed",
            "workflow_run": {
                "id": 99,
                "name": "CI",
                "conclusion": "success",
                "pull_requests": [
                    {"number": 4, "html_url": "https://github.com/o/r/pull/4"}
                ],
            },
        },
    )
    assert event is not None
    assert event["event_type"] == "tests_passed"
    assert event["pr_number"] == 4
    assert event["pr_url"] == "https://github.com/o/r/pull/4"


def test_parse_event_ignores_pull_request_synchronize():
    event = GitHubWebhookParser.parse_event(
        "pull_request",
        {
            "action": "synchronize",
            "pull_request": {
                "number": 4,
                "html_url": "https://github.com/o/r/pull/4",
                "head": {"ref": "copilot/fix"},
            },
        },
    )
    assert event is None


def test_parse_event_ignores_skipped_and_cancelled_workflows():
    skipped = GitHubWebhookParser.parse_event(
        "workflow_run",
        {
            "action": "completed",
            "workflow_run": {"id": 1, "name": "CI", "conclusion": "skipped"},
        },
    )
    cancelled = GitHubWebhookParser.parse_event(
        "workflow_run",
        {
            "action": "completed",
            "workflow_run": {"id": 2, "name": "CI", "conclusion": "cancelled"},
        },
    )
    assert skipped is None
    assert cancelled is None


def test_parse_event_workflow_failure_is_tests_failed():
    event = GitHubWebhookParser.parse_event(
        "workflow_run",
        {
            "action": "completed",
            "workflow_run": {
                "id": 3,
                "name": "CI",
                "conclusion": "failure",
                "pull_requests": [{"number": 4}],
            },
        },
    )
    assert event is not None
    assert event["event_type"] == "tests_failed"
    assert event["pr_number"] == 4


def test_parse_event_filters_workflow_by_name():
    payload = {
        "action": "completed",
        "workflow_run": {"id": 4, "name": "CodeQL", "conclusion": "success"},
    }
    ignored = GitHubWebhookParser.parse_event(
        "workflow_run", payload, ci_workflow_names=frozenset({"ci"})
    )
    accepted = GitHubWebhookParser.parse_event(
        "workflow_run",
        {
            "action": "completed",
            "workflow_run": {"id": 5, "name": "CI", "conclusion": "success"},
        },
        ci_workflow_names=frozenset({"ci"}),
    )
    assert ignored is None
    assert accepted is not None
    assert accepted["event_type"] == "tests_passed"
