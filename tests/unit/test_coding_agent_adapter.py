"""
Unit tests for the CodingAgentAdapter.

Tests cover all eight adapter functions:
  1. assign_to_copilot
  2. detect_agent_start
  3. detect_copilot_question
  4. send_user_reply
  5. detect_pull_request
  6. detect_task_completion
  7. trigger_fix_iteration
  8. parse_webhook_event (webhook-driven event parsing, synchronous)

Strategy:
  - Mock GitHubHTTPClient methods with AsyncMock to avoid real API calls.
  - Assert on CodingAgentEvent fields returned from every method.
  - Verify retry/error paths produce ADAPTER_ERROR events.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adapters.coding_agent.adapter import CodingAgentAdapter, _FIX_TRIGGER_TEXT
from adapters.coding_agent.models import AgentEventType, CodingAgentEvent
from adapters.github.client import GitHubClientError
from adapters.github.models import GitHubComment, GitHubUser


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_comment(
    id_: int,
    body: str,
    login: str = "github-copilot[bot]",
) -> GitHubComment:
    """Helper to build a GitHubComment fixture."""
    return GitHubComment(
        id=id_,
        body=body,
        user=GitHubUser(login=login, id=99, type="Bot"),
        created_at=datetime.now(timezone.utc),
        html_url=f"https://github.com/owner/repo/issues/1#issuecomment-{id_}",
    )


@pytest.fixture
def mock_client() -> MagicMock:
    """Return a MagicMock standing in for GitHubHTTPClient."""
    client = MagicMock()
    client.owner = "test_owner"
    client.repo = "test_repo"
    return client


@pytest.fixture
def adapter(mock_client: MagicMock) -> CodingAgentAdapter:
    """Return a CodingAgentAdapter wired to the mock client."""
    return CodingAgentAdapter(
        client=mock_client,
        copilot_username="github-copilot[bot]",
        max_retries=1,
        poll_interval=0.01,
        request_timeout=5.0,
    )


# ---------------------------------------------------------------------------
# 1. assign_to_copilot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assign_to_copilot_success(adapter: CodingAgentAdapter) -> None:
    adapter._issues.assign_copilot = AsyncMock(return_value=True)

    event = await adapter.assign_to_copilot(issue_number=42, job_id="job-abc")

    assert event.event_type == AgentEventType.AGENT_ASSIGNED
    assert event.issue_number == 42
    assert event.job_id == "job-abc"
    assert "42" in event.message
    adapter._issues.assign_copilot.assert_awaited_once_with(42, "github-copilot[bot]")


@pytest.mark.asyncio
async def test_assign_to_copilot_api_failure(adapter: CodingAgentAdapter) -> None:
    adapter._issues.assign_copilot = AsyncMock(
        side_effect=GitHubClientError("API unreachable")
    )

    event = await adapter.assign_to_copilot(issue_number=42)

    assert event.event_type == AgentEventType.ADAPTER_ERROR
    assert "42" in event.message


# ---------------------------------------------------------------------------
# 2. detect_agent_start
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_agent_start_found(adapter: CodingAgentAdapter) -> None:
    comments = [
        _make_comment(10, "I'll start working on this issue now.", "github-copilot[bot]"),
    ]
    adapter._issues.get_comments = AsyncMock(return_value=comments)

    event = await adapter.detect_agent_start(issue_number=7, job_id="j1")

    assert event is not None
    assert event.event_type == AgentEventType.AGENT_STARTED
    assert event.comment_id == 10
    assert event.issue_number == 7


@pytest.mark.asyncio
async def test_detect_agent_start_not_found(adapter: CodingAgentAdapter) -> None:
    adapter._issues.get_comments = AsyncMock(return_value=[])

    event = await adapter.detect_agent_start(issue_number=7)

    assert event is None


@pytest.mark.asyncio
async def test_detect_agent_start_ignores_since_comment_id(adapter: CodingAgentAdapter) -> None:
    comments = [
        _make_comment(5, "Earlier comment", "github-copilot[bot]"),
        _make_comment(20, "Later comment", "github-copilot[bot]"),
    ]
    adapter._issues.get_comments = AsyncMock(return_value=comments)

    # since_comment_id=10 should skip comment_id=5 but detect comment_id=20
    event = await adapter.detect_agent_start(issue_number=7, since_comment_id=10)

    assert event is not None
    assert event.comment_id == 20


# ---------------------------------------------------------------------------
# 3. detect_copilot_question
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_copilot_question_found(adapter: CodingAgentAdapter) -> None:
    comments = [
        _make_comment(30, "Could you clarify the auth requirements?", "github-copilot[bot]"),
    ]
    adapter._issues.get_comments = AsyncMock(return_value=comments)

    event = await adapter.detect_copilot_question(issue_number=9)

    assert event is not None
    assert event.event_type == AgentEventType.COPILOT_QUESTION
    assert event.question is not None
    assert "clarify" in event.question.lower()


@pytest.mark.asyncio
async def test_detect_copilot_question_not_found_no_question_mark(
    adapter: CodingAgentAdapter,
) -> None:
    comments = [
        _make_comment(31, "Working on it now.", "github-copilot[bot]"),
    ]
    adapter._issues.get_comments = AsyncMock(return_value=comments)

    event = await adapter.detect_copilot_question(issue_number=9)

    assert event is None


@pytest.mark.asyncio
async def test_detect_copilot_question_ignores_human_comments(
    adapter: CodingAgentAdapter,
) -> None:
    comments = [
        _make_comment(32, "Should we use Redis or Postgres?", "human-user"),
    ]
    adapter._issues.get_comments = AsyncMock(return_value=comments)

    event = await adapter.detect_copilot_question(issue_number=9)

    assert event is None


# ---------------------------------------------------------------------------
# 4. send_user_reply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_user_reply_success(adapter: CodingAgentAdapter) -> None:
    reply_comment = _make_comment(50, "Please use OAuth2.", "human-user")
    adapter._issues.add_comment = AsyncMock(return_value=reply_comment)

    event = await adapter.send_user_reply(
        issue_number=11, reply_text="Please use OAuth2.", job_id="j2"
    )

    assert event.comment_id == 50
    assert event.issue_number == 11
    assert event.event_type == AgentEventType.USER_REPLY
    adapter._issues.add_comment.assert_awaited_once_with(11, "Please use OAuth2.")


@pytest.mark.asyncio
async def test_send_user_reply_failure(adapter: CodingAgentAdapter) -> None:
    adapter._issues.add_comment = AsyncMock(
        side_effect=GitHubClientError("Network error")
    )

    event = await adapter.send_user_reply(issue_number=11, reply_text="Hi")

    assert event.event_type == AgentEventType.ADAPTER_ERROR


# ---------------------------------------------------------------------------
# 5. detect_pull_request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_pull_request_from_comments(adapter: CodingAgentAdapter) -> None:
    pr_body = "I've created PR #77: https://github.com/owner/repo/pull/77"
    comments = [_make_comment(60, pr_body, "github-copilot[bot]")]
    adapter._issues.get_comments = AsyncMock(return_value=comments)

    event = await adapter.detect_pull_request(issue_number=5, job_id="j3")

    assert event is not None
    assert event.event_type == AgentEventType.PR_CREATED
    assert event.pr_number == 77
    assert event.pr_url == "https://github.com/owner/repo/pull/77"


@pytest.mark.asyncio
async def test_detect_pull_request_from_pr_list(
    adapter: CodingAgentAdapter, mock_client: MagicMock
) -> None:
    # No PR link in comments, but one exists in the open PRs list.
    adapter._issues.get_comments = AsyncMock(return_value=[])
    mock_client.get = AsyncMock(
        return_value=[
            {
                "number": 88,
                "html_url": "https://github.com/owner/repo/pull/88",
                "title": "Fix issue #5",
                "body": "Closes #5",
                "user": {"login": "github-copilot[bot]", "id": 99, "type": "Bot"},
            }
        ]
    )

    event = await adapter.detect_pull_request(issue_number=5)

    assert event is not None
    assert event.pr_number == 88
    mock_client.get.assert_awaited()
    assert mock_client.get.await_args.kwargs["params"]["state"] == "all"


@pytest.mark.asyncio
async def test_detect_pull_request_from_merged_pr_list(
    adapter: CodingAgentAdapter, mock_client: MagicMock
) -> None:
    adapter._issues.get_comments = AsyncMock(return_value=[])
    mock_client.get = AsyncMock(
        return_value=[
            {
                "number": 91,
                "html_url": "https://github.com/owner/repo/pull/91",
                "title": "Implement task",
                "body": "Fixes #5",
                "state": "closed",
                "merged_at": "2026-08-16T12:00:00Z",
                "user": {"login": "github-copilot[bot]", "id": 99, "type": "Bot"},
            }
        ]
    )

    event = await adapter.detect_pull_request(issue_number=5)

    assert event is not None
    assert event.pr_number == 91
    assert event.pr_url == "https://github.com/owner/repo/pull/91"


@pytest.mark.asyncio
async def test_detect_pull_request_from_timeline(
    adapter: CodingAgentAdapter, mock_client: MagicMock
) -> None:
    adapter._issues.get_comments = AsyncMock(return_value=[])

    async def fake_get(endpoint: str, params: dict | None = None):
        if str(endpoint).endswith("/timeline"):
            return [
                {
                    "event": "cross-referenced",
                    "source": {
                        "issue": {
                            "number": 14,
                            "html_url": "https://github.com/owner/repo/pull/14",
                            "pull_request": {
                                "html_url": "https://github.com/owner/repo/pull/14",
                            },
                        }
                    },
                }
            ]
        return []

    mock_client.get = AsyncMock(side_effect=fake_get)

    event = await adapter.detect_pull_request(issue_number=6)

    assert event is not None
    assert event.pr_number == 14
    assert event.pr_url == "https://github.com/owner/repo/pull/14"


@pytest.mark.asyncio
async def test_detect_pull_request_not_found(
    adapter: CodingAgentAdapter, mock_client: MagicMock
) -> None:
    adapter._issues.get_comments = AsyncMock(return_value=[])
    mock_client.get = AsyncMock(return_value=[])

    event = await adapter.detect_pull_request(issue_number=5)

    assert event is None


# ---------------------------------------------------------------------------
# 6. detect_task_completion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_task_completion_closed_issue(
    adapter: CodingAgentAdapter, mock_client: MagicMock
) -> None:
    mock_client.get = AsyncMock(return_value={"number": 6, "state": "closed"})

    event = await adapter.detect_task_completion(issue_number=6, job_id="j4")

    assert event is not None
    assert event.event_type == AgentEventType.AGENT_COMPLETED
    assert "closed" in event.message.lower()


@pytest.mark.asyncio
async def test_detect_task_completion_from_comment(
    adapter: CodingAgentAdapter, mock_client: MagicMock
) -> None:
    mock_client.get = AsyncMock(return_value={"number": 6, "state": "open"})
    adapter._issues.get_comments = AsyncMock(
        return_value=[
            _make_comment(70, "The pull request has been created. Task is complete.", "github-copilot[bot]"),
        ]
    )

    event = await adapter.detect_task_completion(issue_number=6)

    assert event is not None
    assert event.event_type == AgentEventType.AGENT_COMPLETED


@pytest.mark.asyncio
async def test_detect_task_completion_not_detected(
    adapter: CodingAgentAdapter, mock_client: MagicMock
) -> None:
    mock_client.get = AsyncMock(return_value={"number": 6, "state": "open"})
    adapter._issues.get_comments = AsyncMock(
        return_value=[_make_comment(71, "Still working on it.", "github-copilot[bot]")]
    )

    event = await adapter.detect_task_completion(issue_number=6)

    assert event is None


# ---------------------------------------------------------------------------
# 7. trigger_fix_iteration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_fix_iteration_success(adapter: CodingAgentAdapter) -> None:
    fix_comment = _make_comment(80, "fix comment body", "pipeline-bot")
    adapter._issues.add_comment = AsyncMock(return_value=fix_comment)

    event = await adapter.trigger_fix_iteration(
        issue_number=13,
        test_failure_log="AssertionError: expected True got False",
        retry_count=1,
        max_retries=3,
        job_id="j5",
    )

    assert event.event_type == AgentEventType.FIX_REQUESTED
    assert event.comment_id == 80
    assert event.issue_number == 13

    # Verify the fix trigger text was included in the posted comment.
    call_args = adapter._issues.add_comment.call_args
    posted_body: str = call_args[0][1]
    assert _FIX_TRIGGER_TEXT in posted_body
    assert "AssertionError" in posted_body


@pytest.mark.asyncio
async def test_trigger_fix_iteration_failure(adapter: CodingAgentAdapter) -> None:
    adapter._issues.add_comment = AsyncMock(
        side_effect=GitHubClientError("Timeout")
    )

    event = await adapter.trigger_fix_iteration(
        issue_number=13,
        test_failure_log="Error details",
    )

    assert event.event_type == AgentEventType.ADAPTER_ERROR


# ---------------------------------------------------------------------------
# 8. parse_webhook_event
# ---------------------------------------------------------------------------


def test_parse_webhook_issue_comment_question(adapter: CodingAgentAdapter) -> None:
    payload = {
        "action": "created",
        "comment": {
            "id": 100,
            "body": "Could you please clarify the expected response format?",
        },
        "sender": {"login": "github-copilot[bot]"},
        "issue": {"number": 20},
    }

    event = adapter.parse_webhook_event("issue_comment", payload, job_id="j6")

    assert event is not None
    assert event.event_type == AgentEventType.COPILOT_QUESTION
    assert event.issue_number == 20
    assert event.comment_id == 100
    assert "clarify" in (event.question or "").lower()


def test_parse_webhook_issue_comment_start(adapter: CodingAgentAdapter) -> None:
    payload = {
        "action": "created",
        "comment": {"id": 101, "body": "Starting implementation now."},
        "sender": {"login": "github-copilot[bot]"},
        "issue": {"number": 21},
    }

    event = adapter.parse_webhook_event("issue_comment", payload)

    assert event is not None
    assert event.event_type == AgentEventType.AGENT_STARTED


def test_parse_webhook_issue_comment_completion(adapter: CodingAgentAdapter) -> None:
    payload = {
        "action": "created",
        "comment": {
            "id": 102,
            "body": "I've created a pull request for this issue. Task is complete.",
        },
        "sender": {"login": "github-copilot[bot]"},
        "issue": {"number": 22},
    }

    event = adapter.parse_webhook_event("issue_comment", payload)

    assert event is not None
    assert event.event_type == AgentEventType.AGENT_COMPLETED


def test_parse_webhook_pr_opened_by_copilot(adapter: CodingAgentAdapter) -> None:
    payload = {
        "action": "opened",
        "pull_request": {
            "number": 55,
            "html_url": "https://github.com/owner/repo/pull/55",
            "title": "Fix: implement auth",
            "body": "Closes #30",
            "user": {"login": "github-copilot[bot]", "id": 99, "type": "Bot"},
        },
    }

    event = adapter.parse_webhook_event("pull_request", payload, job_id="j7")

    assert event is not None
    assert event.event_type == AgentEventType.PR_CREATED
    assert event.pr_number == 55
    assert event.issue_number == 30


def test_parse_webhook_pr_opened_by_human_ignored(adapter: CodingAgentAdapter) -> None:
    payload = {
        "action": "opened",
        "pull_request": {
            "number": 56,
            "html_url": "https://github.com/owner/repo/pull/56",
            "title": "Human PR",
            "body": "",
            "user": {"login": "human-dev", "id": 1, "type": "User"},
        },
    }

    event = adapter.parse_webhook_event("pull_request", payload)

    assert event is None


def test_parse_webhook_issue_closed(adapter: CodingAgentAdapter) -> None:
    payload = {
        "action": "closed",
        "issue": {"number": 99},
    }

    event = adapter.parse_webhook_event("issues", payload, job_id="j8")

    assert event is not None
    assert event.event_type == AgentEventType.AGENT_COMPLETED
    assert event.issue_number == 99


def test_parse_webhook_unknown_event_type(adapter: CodingAgentAdapter) -> None:
    event = adapter.parse_webhook_event("star", {"action": "created"})
    assert event is None


def test_parse_webhook_non_copilot_comment_ignored(adapter: CodingAgentAdapter) -> None:
    payload = {
        "action": "created",
        "comment": {"id": 200, "body": "What should I do?"},
        "sender": {"login": "human-dev"},
        "issue": {"number": 25},
    }

    event = adapter.parse_webhook_event("issue_comment", payload)

    assert event is None


@pytest.mark.asyncio
async def test_watch_issue_detects_pr_after_question(adapter: CodingAgentAdapter) -> None:
    start = CodingAgentEvent(
        event_type=AgentEventType.AGENT_STARTED,
        issue_number=1,
        comment_id=1,
        message="started",
    )
    question = CodingAgentEvent(
        event_type=AgentEventType.COPILOT_QUESTION,
        issue_number=1,
        comment_id=2,
        question="Which auth library?",
        message="question",
    )
    pr = CodingAgentEvent(
        event_type=AgentEventType.PR_CREATED,
        issue_number=1,
        pr_number=9,
        pr_url="https://github.com/owner/repo/pull/9",
        message="pr",
    )
    adapter.detect_agent_start = AsyncMock(return_value=start)
    adapter.detect_copilot_question = AsyncMock(return_value=question)
    adapter.detect_pull_request = AsyncMock(return_value=pr)
    adapter.detect_task_completion = AsyncMock(return_value=None)

    seen: list[AgentEventType] = []
    async for event in adapter.watch_issue(1, timeout=1.0):
        seen.append(event.event_type)
        if event.event_type == AgentEventType.PR_CREATED:
            break

    assert AgentEventType.COPILOT_QUESTION in seen
    assert AgentEventType.PR_CREATED in seen
    adapter.detect_pull_request.assert_awaited()


@pytest.mark.asyncio
async def test_watch_issue_detects_pr_without_agent_start_comment(adapter: CodingAgentAdapter) -> None:
    pr = CodingAgentEvent(
        event_type=AgentEventType.PR_CREATED,
        issue_number=6,
        pr_number=12,
        pr_url="https://github.com/owner/repo/pull/12",
        message="pr",
    )
    adapter.detect_agent_start = AsyncMock(return_value=None)
    adapter.detect_copilot_question = AsyncMock(return_value=None)
    adapter.detect_pull_request = AsyncMock(return_value=pr)
    adapter.detect_task_completion = AsyncMock(return_value=None)

    seen: list[AgentEventType] = []
    async for event in adapter.watch_issue(6, timeout=1.0):
        seen.append(event.event_type)
        if event.event_type == AgentEventType.PR_CREATED:
            break

    assert seen == [AgentEventType.PR_CREATED]


@pytest.mark.asyncio
async def test_watch_issue_detects_closed_issue_without_agent_start(adapter: CodingAgentAdapter) -> None:
    done = CodingAgentEvent(
        event_type=AgentEventType.AGENT_COMPLETED,
        issue_number=6,
        message="closed",
    )
    adapter.detect_agent_start = AsyncMock(return_value=None)
    adapter.detect_copilot_question = AsyncMock(return_value=None)
    adapter.detect_pull_request = AsyncMock(return_value=None)
    adapter.detect_task_completion = AsyncMock(return_value=done)

    events = [event async for event in adapter.watch_issue(6, timeout=1.0)]

    assert events[0].event_type == AgentEventType.AGENT_COMPLETED


@pytest.mark.asyncio
async def test_watch_issue_idle_timeout(adapter: CodingAgentAdapter) -> None:
    adapter.detect_agent_start = AsyncMock(return_value=None)
    adapter.detect_copilot_question = AsyncMock(return_value=None)
    adapter.detect_pull_request = AsyncMock(return_value=None)
    adapter.detect_task_completion = AsyncMock(return_value=None)

    events = [event async for event in adapter.watch_issue(1, timeout=0.05)]

    assert events
    assert events[-1].event_type == AgentEventType.ADAPTER_ERROR
    assert "No news from GitHub" in (events[-1].message or "")


@pytest.mark.asyncio
async def test_send_user_reply_notes_watcher_activity(adapter: CodingAgentAdapter) -> None:
    adapter._issues.add_comment = AsyncMock(
        return_value=_make_comment(50, "Please use OAuth2.", "human-user")
    )

    await adapter.send_user_reply(11, "Please use OAuth2.")

    assert 11 in adapter._watcher_activity


@pytest.mark.asyncio
async def test_watch_issue_idle_clock_honors_activity_bump(adapter: CodingAgentAdapter) -> None:
    adapter.detect_agent_start = AsyncMock(return_value=None)
    adapter.detect_copilot_question = AsyncMock(return_value=None)
    adapter.detect_pull_request = AsyncMock(return_value=None)
    adapter.detect_task_completion = AsyncMock(return_value=None)

    async def expire_after_bump() -> None:
        await asyncio.sleep(0.02)
        adapter.note_watcher_activity(1)
        await asyncio.sleep(0.02)
        adapter._watcher_activity[1] = asyncio.get_running_loop().time() - 100

    bump_task = asyncio.create_task(expire_after_bump())
    events = [event async for event in adapter.watch_issue(1, timeout=1.0)]
    await bump_task

    assert events
    assert events[-1].event_type == AgentEventType.ADAPTER_ERROR
