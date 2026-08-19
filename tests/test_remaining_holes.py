from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from adapters.coding_agent.adapter import CodingAgentAdapter, _FIX_TRIGGER_TEXT
from config.settings import Settings
from domain.models import EventType, JobState, PipelineEvent
from tests.conftest import open_pr, seed_job
from webhooks.router import build_webhook_router, verify_signature


async def test_recover_reuses_existing_issue_when_contract_complete(harness):
    jobs, runner, github = harness["jobs"], harness["runner"], harness["github"]
    from tests.conftest import complete_contract

    body = complete_contract()
    harness["store"].issues[3] = {
        "number": 3,
        "body": body,
        "html_url": "https://github.com/acme/repo/issues/3",
    }
    await seed_job(
        jobs,
        state=JobState.TASK_ACCEPTED,
        issue_number=3,
        issue_url="https://github.com/acme/repo/issues/3",
        pr_number=None,
        body=body,
    )
    await runner.recover_active_jobs()
    assert github.created_issues == []
    assert harness["coding_agent"].trigger_calls == [3]


async def test_recover_incomplete_contract_does_not_trigger(harness):
    jobs, runner = harness["jobs"], harness["runner"]
    job = await seed_job(
        jobs,
        state=JobState.TASK_ACCEPTED,
        issue_number=3,
        issue_url="https://github.com/acme/repo/issues/3",
        pr_number=None,
        body="do the thing",
    )
    await runner.recover_active_jobs()
    fresh = await jobs.get(job.id)
    assert fresh.state == JobState.TASK_ACCEPTED
    assert harness["coding_agent"].trigger_calls == []
    assert not any("Task Contract is incomplete" in text for _, text in harness["notifier"].texts)


async def test_recover_task_accepted_without_issue_creates_template(harness):
    jobs, runner, github = harness["jobs"], harness["runner"], harness["github"]
    job = await seed_job(
        jobs,
        state=JobState.TASK_ACCEPTED,
        issue_number=None,
        issue_url="",
        pr_number=None,
        body="",
    )
    await runner.recover_active_jobs()
    assert len(github.created_issues) == 1
    body = github.created_issues[0]["body"]
    assert body != ""
    assert "# Task Contract" in body
    assert "## Goal" in body
    assert "## Verification" in body
    assert "## Additional Context" in body
    assert harness["coding_agent"].trigger_calls == []
    fresh = await jobs.get(job.id)
    assert fresh.state == JobState.TASK_ACCEPTED
    assert fresh.issue_number == github.created_issues[0]["number"]
    assert fresh.body == ""


async def test_new_job_stops_previous(harness):
    jobs, runner = harness["jobs"], harness["runner"]
    old = await seed_job(jobs, state=JobState.CODING_AGENT_RUNNING, pr_number=None)
    await runner.start_job(chat_id=100, user_id=7, title="next", body="next task")
    fresh = await jobs.get(old.id)
    assert fresh.state == JobState.FAILED
    assert any("остановлена" in text for _, text in harness["notifier"].texts)


async def test_confirm_merge_requires_pending(harness):
    jobs, store, runner = harness["jobs"], harness["store"], harness["runner"]
    job = await seed_job(jobs, state=JobState.WAIT_TESTS)
    store.prs[12] = open_pr()
    store.runs["copilot/fix-12"] = [
        {"id": 1, "status": "completed", "conclusion": "success"}
    ]
    await runner.confirm_merge(job.id, True, operator_id=7)
    assert store.merge_calls == []
    assert any("/merge" in text for _, text in harness["notifier"].texts)


async def test_webhook_and_poll_share_event_ids():
    adapter = CodingAgentAdapter(github=None, settings=Settings())
    poll = adapter._event_from_comment(
        3,
        {
            "id": 99,
            "body": "Working on it",
            "user": {"login": "copilot-swe-agent[bot]"},
        },
    )
    hook = adapter.parse_webhook_event(
        "issue_comment",
        {
            "action": "created",
            "issue": {"number": 3},
            "comment": {
                "id": 99,
                "body": "Working on it",
                "user": {"login": "copilot-swe-agent[bot]"},
            },
        },
    )
    assert poll is not None and hook is not None
    assert poll.event_id == hook.event_id == "comment-started-99"


async def test_cancelled_run_does_not_trigger_fix():
    adapter = CodingAgentAdapter(github=None, settings=Settings())
    event = adapter.parse_webhook_event(
        "workflow_run",
        {
            "workflow_run": {
                "id": 5,
                "status": "completed",
                "conclusion": "cancelled",
                "head_branch": "copilot/issue-3",
                "pull_requests": [{"number": 12}],
            }
        },
    )
    assert event is None


async def test_bare_question_mark_is_not_copilot_question():
    adapter = CodingAgentAdapter(github=None, settings=Settings())
    event = adapter._event_from_comment(
        3,
        {
            "id": 1,
            "body": "Pushed a commit. Next step?",
            "user": {"login": "copilot-swe-agent[bot]"},
        },
    )
    assert event is not None
    assert event.type == EventType.AGENT_STARTED


async def test_terminal_job_ignores_events(harness):
    jobs, runner, notifier = harness["jobs"], harness["runner"], harness["notifier"]
    await seed_job(jobs, state=JobState.DONE)
    before = len(notifier.texts)
    await runner.process_event(
        PipelineEvent(
            event_id="late",
            type=EventType.AGENT_STARTED,
            issue_number=3,
        )
    )
    assert len(notifier.texts) == before


async def test_fix_log_is_truncated(harness):
    agent = harness["coding_agent"]
    from adapters.coding_agent.adapter import CodingAgentAdapter

    class _Comments:
        def __init__(self):
            self.bodies = []

        async def create_issue_comment(self, issue_number, body):
            self.bodies.append(body)

    class _Github:
        comments = _Comments()

    real = CodingAgentAdapter(_Github(), Settings())
    await real.trigger_fix_iteration(3, "E" * 20_000)
    posted = real.github.comments.bodies[0]
    assert posted.startswith(_FIX_TRIGGER_TEXT)
    assert "...[truncated]" in posted
    assert len(posted) < 20_000


def test_webhook_rejects_missing_secret():
    class _Settings:
        github_webhook_secret = ""

    class _Container:
        settings = _Settings()
        coding_agent = None
        runner = None

    app = FastAPI()
    app.include_router(build_webhook_router(_Container()))
    response = TestClient(app).post("/webhooks/github", json={"action": "ping"})
    assert response.status_code == 503


def test_signature_still_checks_when_secret_set():
    body = b'{"ok":true}'
    assert verify_signature("s3cret", body, "sha256=nope") is False


async def test_observed_merge_marks_job_done(harness):
    jobs, store, runner = harness["jobs"], harness["store"], harness["runner"]
    job = await seed_job(jobs, state=JobState.WAIT_TESTS)
    store.prs[12] = open_pr(merged=True, state="closed")
    await runner.request_merge(job.id)
    fresh = await jobs.get(job.id)
    assert fresh.state == JobState.DONE
    assert store.merge_calls == []


async def test_recover_watches_test_passed(harness):
    jobs, runner = harness["jobs"], harness["runner"]
    job = await seed_job(jobs, state=JobState.TEST_PASSED)
    await runner.recover_active_jobs()
    assert job.id in runner._watch_tasks


async def test_issue_closed_ids_match():
    adapter = CodingAgentAdapter(github=None, settings=Settings())
    hook = adapter.parse_webhook_event(
        "issues",
        {"action": "closed", "issue": {"number": 3}},
    )
    assert hook is not None
    assert hook.event_id == "issue-closed-3"


def _github_for_poll(*, actions):
    from tests.conftest import open_pr

    class _PR:
        async def list_pulls_for_issue(self, issue_number: int):
            return [{"number": 12}]

        async def get_pull_request(self, number: int):
            return open_pr()

    class _Issues:
        async def get_issue(self, number: int):
            return {"state": "open"}

    class _Comments:
        async def list_issue_comments(self, number: int):
            return []

    class _Github:
        def __init__(self) -> None:
            self.actions = actions
            self.pull_requests = _PR()
            self.issues = _Issues()
            self.comments = _Comments()

    return _Github()


async def test_actions_403_does_not_abort_backup_poll():
    from domain.errors import GitHubForbiddenError

    calls = {"n": 0}

    class _Actions:
        async def list_runs_for_branch(self, branch, per_page: int = 10):
            calls["n"] += 1
            raise GitHubForbiddenError("GitHub API 403 Forbidden")

    adapter = CodingAgentAdapter(_github_for_poll(actions=_Actions()), Settings())
    first = [event async for event in adapter._poll_once(3)]
    second = [event async for event in adapter._poll_once(3)]
    assert adapter._actions_forbidden is True
    assert calls["n"] == 1
    assert [event.type for event in first] == [EventType.PR_OPENED, EventType.AGENT_COMPLETED]
    assert EventType.PR_OPENED not in [event.type for event in second]


async def test_actions_rate_limit_does_not_disable_poll():
    from domain.errors import GitHubUnavailableError

    calls = {"n": 0}

    class _Actions:
        async def list_runs_for_branch(self, branch, per_page: int = 10):
            calls["n"] += 1
            raise GitHubUnavailableError("GitHub API rate limited")

    adapter = CodingAgentAdapter(_github_for_poll(actions=_Actions()), Settings())
    [_ async for _ in adapter._poll_once(3)]
    [_ async for _ in adapter._poll_once(3)]
    assert adapter._actions_forbidden is False
    assert calls["n"] == 2


async def test_actions_403_does_not_spam_telegram(harness):
    import asyncio

    from domain.errors import GitHubForbiddenError

    class _Actions:
        async def list_runs_for_branch(self, branch, per_page: int = 10):
            raise GitHubForbiddenError("GitHub API 403 Forbidden")

    jobs, runner, notifier = harness["jobs"], harness["runner"], harness["notifier"]
    job = await seed_job(jobs, state=JobState.WAIT_TESTS)
    runner.coding_agent = CodingAgentAdapter(
        _github_for_poll(actions=_Actions()), Settings()
    )
    runner.settings.coding_agent_poll_interval_sec = 3600
    task = asyncio.create_task(runner._run_watch_issue(job))
    try:
        for _ in range(50):
            if notifier.texts:
                break
            await asyncio.sleep(0.01)
        dumped = [
            text
            for _, text in notifier.texts
            if "403" in text or "опроса" in text or "mozilla" in text
        ]
        assert dumped == []
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_merge_still_works_when_actions_api_is_forbidden(harness):
    from domain.errors import GitHubForbiddenError

    jobs, store, runner, github = (
        harness["jobs"],
        harness["store"],
        harness["runner"],
        harness["github"],
    )
    job = await seed_job(jobs, state=JobState.TEST_PASSED, pipeline_check_posted=True)
    store.prs[12] = open_pr()

    async def _boom(branch, per_page: int = 10):
        raise GitHubForbiddenError("GitHub API 403 Forbidden")

    github.list_runs_for_branch = _boom  # type: ignore[method-assign]
    await runner.request_merge(job.id)
    assert store.merge_calls == []
    assert harness["notifier"].confirmations
    texts = " ".join(text for _, text in harness["notifier"].texts)
    assert "403" not in texts
    assert "developer.mozilla.org" not in texts
    assert "api.github.com" not in texts


async def test_watch_error_message_does_not_dump_httpx(harness):
    import asyncio

    from domain.errors import GitHubForbiddenError

    jobs, runner, notifier = harness["jobs"], harness["runner"], harness["notifier"]
    job = await seed_job(jobs, state=JobState.CODING_AGENT_RUNNING, pr_number=None)
    runner.settings.coding_agent_poll_interval_sec = 3600

    async def _boom(_issue_number: int):
        raise GitHubForbiddenError(
            "Client error '403 Forbidden' for url "
            "'https://api.github.com/repos/x/y/actions/runs' "
            "For more information check: https://developer.mozilla.org/http/status/403"
        )
        if False:
            yield None

    runner.coding_agent.watch_issue = _boom  # type: ignore[method-assign]
    task = asyncio.create_task(runner._run_watch_issue(job))
    try:
        for _ in range(100):
            if notifier.texts:
                break
            await asyncio.sleep(0.01)
        assert notifier.texts
        text = notifier.texts[0][1]
        assert "mozilla.org" not in text
        assert "actions/runs" not in text
        assert "api.github.com" not in text
        assert "Actions: Read" in text
        await asyncio.sleep(0.05)
        assert len(notifier.texts) == 1
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
