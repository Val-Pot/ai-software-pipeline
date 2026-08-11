"""
Unit tests for GitHub Actions Adapter status resolution, webhooks, and polling.
"""
import pytest
import respx
from httpx import Response

from adapters.github.client import GitHubHTTPClient
from adapters.github.actions import GitHubActionsAdapter
from adapters.github.actions_models import WorkflowStatus, WorkflowConclusion, ActionsStatusEvent


@pytest.fixture
def actions_adapter():
    client = GitHubHTTPClient(token="token", owner="owner", repo="repo")
    return GitHubActionsAdapter(client=client)


@pytest.mark.asyncio
@respx.mock
async def test_get_workflow_run_status(actions_adapter):
    respx.get("https://api.github.com/repos/owner/repo/actions/runs/12345").mock(
        return_value=Response(
            200,
            json={
                "id": 12345,
                "name": "CI Tests",
                "status": "completed",
                "conclusion": "success",
                "html_url": "https://github.com/owner/repo/actions/runs/12345",
                "head_branch": "main",
                "head_sha": "abc1234",
            },
        )
    )

    run = await actions_adapter.get_workflow_run(12345)
    assert run.status == WorkflowStatus.COMPLETED
    assert run.conclusion == WorkflowConclusion.SUCCESS

    event = actions_adapter.to_status_event(run)
    assert event.event_type == "tests_passed"
    assert event.run_id == 12345


def test_parse_webhook_actions_in_progress():
    payload = {
        "action": "in_progress",
        "workflow_run": {
            "id": 999,
            "name": "Build",
            "status": "in_progress",
            "conclusion": None,
            "html_url": "http://run/999",
            "head_branch": "feature",
            "head_sha": "def5678",
        },
    }

    event = GitHubActionsAdapter.parse_webhook_payload(payload)
    assert event is not None
    assert event.status == WorkflowStatus.IN_PROGRESS
    assert event.event_type == "tests_running"


def test_parse_webhook_actions_completed_failure():
    payload = {
        "action": "completed",
        "workflow_run": {
            "id": 888,
            "name": "Unit Tests",
            "status": "completed",
            "conclusion": "failure",
            "html_url": "http://run/888",
            "head_branch": "patch-1",
            "head_sha": "123fff",
        },
    }

    event = GitHubActionsAdapter.parse_webhook_payload(payload)
    assert event is not None
    assert event.status == WorkflowStatus.COMPLETED
    assert event.conclusion == WorkflowConclusion.FAILURE
    assert event.event_type == "tests_failed"
