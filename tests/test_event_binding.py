from __future__ import annotations

from adapters.coding_agent.adapter import CodingAgentAdapter
from adapters.coding_agent.issue_refs import extract_issue_number
from adapters.github.models import GitHubPullRequest, GitHubUser
from adapters.telegram.handlers import _authorized
from config.settings import Settings
from domain.models import EventType, JobState, PipelineEvent
from tests.conftest import seed_job


def test_extract_issue_number_from_body_and_branch():
    assert extract_issue_number("Fixes #3") == 3
    assert extract_issue_number("copilot/issue-7") == 7
    assert extract_issue_number("random text") is None


def test_webhook_pr_opened_binds_issue_from_body():
    adapter = CodingAgentAdapter(github=None, settings=Settings())
    event = adapter.parse_webhook_event(
        "pull_request",
        {
            "action": "opened",
            "pull_request": {
                "number": 12,
                "html_url": "https://github.com/acme/repo/pull/12",
                "body": "Fixes #3",
                "title": "task",
                "draft": True,
                "head": {"ref": "copilot/issue-3"},
                "user": {"login": "copilot-swe-agent[bot]"},
            },
        },
    )
    assert event is not None
    assert event.type == EventType.PR_OPENED
    assert event.issue_number == 3
    assert event.pr_number == 12


def test_webhook_ready_for_review_is_completion():
    adapter = CodingAgentAdapter(github=None, settings=Settings())
    event = adapter.parse_webhook_event(
        "pull_request",
        {
            "action": "ready_for_review",
            "pull_request": {
                "number": 12,
                "body": "Closes #3",
                "html_url": "https://github.com/acme/repo/pull/12",
            },
        },
    )
    assert event is not None
    assert event.type == EventType.AGENT_COMPLETED
    assert event.issue_number == 3


async def test_unknown_event_is_not_consumed(harness):
    runner = harness["runner"]
    await runner.process_event(
        PipelineEvent(
            event_id="orphan",
            type=EventType.PR_OPENED,
            issue_number=99,
            pr_number=99,
        )
    )
    assert "orphan" not in runner.processed_event_ids


async def test_pr_without_issue_does_not_bind_lone_running_job(harness):
    jobs, runner = harness["jobs"], harness["runner"]
    job = await seed_job(jobs, state=JobState.CODING_AGENT_RUNNING, pr_number=None)
    await runner.process_event(
        PipelineEvent(
            event_id="pr-loose",
            type=EventType.PR_OPENED,
            pr_number=12,
            payload={"html_url": "https://github.com/acme/repo/pull/12"},
        )
    )
    fresh = await jobs.get(job.id)
    assert fresh.pr_number is None
    assert fresh.state == JobState.CODING_AGENT_RUNNING


async def test_recover_does_not_repost_pipeline_check(harness):
    jobs, runner, github = harness["jobs"], harness["runner"], harness["github"]
    await seed_job(jobs, state=JobState.TEST_PASSED, pipeline_check_posted=True)
    await runner.recover_active_jobs()
    assert github.review_comments == []


async def test_tests_passed_is_idempotent(harness):
    jobs, runner, github = harness["jobs"], harness["runner"], harness["github"]
    job = await seed_job(jobs, state=JobState.WAIT_TESTS)
    event = PipelineEvent(
        event_id="ci-1",
        type=EventType.TESTS_PASSED,
        issue_number=3,
        pr_number=12,
    )
    await runner.process_event(event)
    await runner.process_event(event)
    fresh = await jobs.get(job.id)
    assert fresh.state == JobState.TEST_PASSED
    assert github.review_comments == [
        "Pipeline check: CI passed, no automated review configured."
    ]


async def test_draft_alone_is_not_completion():
    pr = GitHubPullRequest(
        number=12,
        html_url="https://example/pr/12",
        state="open",
        draft=False,
        requested_reviewers=[],
    )

    class _PR:
        async def get_pull_request(self, number: int):
            return pr

    class _Issues:
        async def get_issue(self, number: int):
            return {"state": "open"}

    class _Comments:
        async def list_issue_comments(self, number: int):
            return []

    class _Github:
        pull_requests = _PR()
        issues = _Issues()
        comments = _Comments()

    adapter = CodingAgentAdapter(_Github(), Settings())
    assert await adapter.detect_task_completion(3, 12) is False
    adapter._draft_seen.add(12)
    assert await adapter.detect_task_completion(3, 12) is True


async def test_poll_rechecks_known_pr_for_completion():
    pr = GitHubPullRequest(
        number=12,
        html_url="https://example/pr/12",
        state="open",
        draft=True,
        requested_reviewers=[],
        head_ref="copilot/issue-3",
    )

    class _PR:
        async def get_pull_request(self, number: int):
            return pr

        async def list_pulls_for_issue(self, issue_number: int):
            return [{"number": 12}]

    class _Issues:
        async def get_issue(self, number: int):
            return {"state": "open"}

    class _Comments:
        async def list_issue_comments(self, number: int):
            return []

    class _Actions:
        async def list_runs_for_branch(self, branch: str, per_page: int = 10):
            return []

    class _Github:
        pull_requests = _PR()
        issues = _Issues()
        comments = _Comments()
        actions = _Actions()

    adapter = CodingAgentAdapter(_Github(), Settings())
    first = [event async for event in adapter._poll_once(3)]
    assert [event.type for event in first] == [EventType.PR_OPENED]
    pr.draft = False
    pr.requested_reviewers = [GitHubUser(login="owner")]
    second = [event async for event in adapter._poll_once(3)]
    assert [event.type for event in second] == [EventType.AGENT_COMPLETED]


def test_telegram_auth_fails_closed_without_allowlist():
    settings = Settings(telegram_allowed_user_ids="")
    assert _authorized(1, settings) is False
    settings = Settings(telegram_allowed_user_ids="7")
    assert _authorized(7, settings) is True
    assert _authorized(8, settings) is False
