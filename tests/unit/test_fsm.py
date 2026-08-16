"""
Comprehensive unit tests for Orchestrator FSM Engine & Pipeline Runner.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock

from orchestrator import (
    PipelineState,
    PipelineJob,
    FSMEngine,
    InvalidTransitionError,
    InMemoryPersistenceAdapter,
    PipelineRunner,
)


@pytest.fixture
def persistence():
    return InMemoryPersistenceAdapter()


@pytest.fixture
def notifier():
    mock = AsyncMock()
    return mock


@pytest.fixture
def runner(persistence, notifier):
    return PipelineRunner(
        persistence=persistence,
        notifier=notifier,
        max_retries=2,
        enable_ai_review=True,
    )


def test_fsm_valid_transitions():
    job = PipelineJob(chat_id=1, user_id=1, task_description="test")
    assert job.state == PipelineState.NEW

    job1 = FSMEngine.transition(job, PipelineState.TASK_ACCEPTED)
    assert job1.state == PipelineState.TASK_ACCEPTED

    job2 = FSMEngine.transition(job1, PipelineState.CODING_AGENT_RUNNING)
    assert job2.state == PipelineState.CODING_AGENT_RUNNING


def test_fsm_invalid_transition_raises_error():
    job = PipelineJob(chat_id=1, user_id=1, task_description="test")
    with pytest.raises(InvalidTransitionError):
        FSMEngine.transition(job, PipelineState.DONE)


@pytest.mark.asyncio
async def test_pipeline_runner_happy_path(runner, notifier):
    job = await runner.create_and_start_job(
        chat_id=100, user_id=200, username="user", task_description="Implement auth"
    )
    assert job.chat_id == 100

    # Simulate PR opened event
    res1 = await runner.process_event(
        job_id=job.job_id,
        event_id="evt_1",
        event_type="pr_opened",
        payload={"pr_url": "https://github.com/pr/1", "pr_number": 1},
    )
    assert res1 is True

    job_after_pr = await runner.persistence.load_job(job.job_id)
    assert job_after_pr.state == PipelineState.WAIT_TESTS
    assert job_after_pr.pr_url == "https://github.com/pr/1"

    # Simulate Tests Passed event -> AI Review -> DONE
    res2 = await runner.process_event(
        job_id=job.job_id,
        event_id="evt_2",
        event_type="tests_passed",
        payload={},
    )
    assert res2 is True

    final_job = await runner.persistence.load_job(job.job_id)
    assert final_job.state == PipelineState.DONE
    notifier.notify_final_result.assert_called_once()


@pytest.mark.asyncio
async def test_idempotent_event_processing(runner):
    job = await runner.create_and_start_job(100, 200, "user", "Fix bug")

    # First event processing
    await runner.process_event(job.job_id, "evt_unique_100", "pr_opened", {"pr_url": "http://pr/1"})
    job1 = await runner.persistence.load_job(job.job_id)
    assert "evt_unique_100" in job1.processed_event_ids

    # Duplicate event processing (should be ignored safely)
    res_dup = await runner.process_event(job.job_id, "evt_unique_100", "pr_opened", {"pr_url": "http://pr/1"})
    assert res_dup is True


@pytest.mark.asyncio
async def test_retry_limit_exceeded(runner, notifier):
    job = await runner.create_and_start_job(100, 200, "user", "Fix flaky test")

    # Move to WAIT_TESTS state
    await runner.process_event(job.job_id, "evt_pr", "pr_opened", {"pr_url": "http://pr/1"})

    # Fail test iteration 1
    await runner.process_event(job.job_id, "evt_fail_1", "tests_failed", {"failure_log": "Assertion Error"})
    job1 = await runner.persistence.load_job(job.job_id)
    assert job1.retry_count == 1
    assert job1.state == PipelineState.WAIT_TESTS

    # Fail test iteration 2 (reaches max retries = 2)
    await runner.process_event(job.job_id, "evt_fail_2", "tests_failed", {"failure_log": "Assertion Error"})
    job2 = await runner.persistence.load_job(job.job_id)
    assert job2.state == PipelineState.FAILED
    assert "Max retries" in job2.error


async def _store_with_issue(runner: PipelineRunner, job: PipelineJob, issue_number: int) -> PipelineJob:
    stored = await runner.persistence.load_job(job.job_id)
    assert stored is not None
    updated = stored.model_copy(update={"issue_number": issue_number})
    await runner.persistence.save_job(updated)
    return updated


@pytest.mark.asyncio
async def test_process_event_resolves_job_by_issue_number(runner):
    job = await runner.create_and_start_job(100, 200, "user", "Task A")
    await _store_with_issue(runner, job, 7)

    ok = await runner.process_event(
        job_id="",
        event_id="evt_pr_no_id",
        event_type="pr_opened",
        payload={"pr_url": "https://github.com/pr/9", "pr_number": 9, "issue_number": 7},
    )
    assert ok is True
    updated = await runner.persistence.load_job(job.job_id)
    assert updated.state == PipelineState.WAIT_TESTS
    assert updated.pr_number == 9


@pytest.mark.asyncio
async def test_process_event_picks_matching_job_among_several(runner):
    job_a = await runner.create_and_start_job(1, 1, "a", "A")
    job_b = await runner.create_and_start_job(2, 2, "b", "B")
    await _store_with_issue(runner, job_a, 10)
    await _store_with_issue(runner, job_b, 20)

    ok = await runner.process_event(
        "",
        "evt_b",
        "pr_opened",
        {"pr_number": 21, "pr_url": "https://github.com/pr/21", "issue_number": 20},
    )
    assert ok is True
    assert (await runner.persistence.load_job(job_a.job_id)).state == PipelineState.CODING_AGENT_RUNNING
    assert (await runner.persistence.load_job(job_b.job_id)).state == PipelineState.WAIT_TESTS


@pytest.mark.asyncio
async def test_process_event_resolves_sole_active_job_without_refs(runner):
    job = await runner.create_and_start_job(100, 200, "user", "Only job")

    ok = await runner.process_event(
        "active_job",
        "evt_pr",
        "pr_opened",
        {"pr_url": "https://github.com/pr/1", "pr_number": 1},
    )
    assert ok is True
    updated = await runner.persistence.load_job(job.job_id)
    assert updated.state == PipelineState.WAIT_TESTS
    assert updated.pr_number == 1


@pytest.mark.asyncio
async def test_process_event_skips_pr_opened_if_already_advanced(runner):
    job = await runner.create_and_start_job(100, 200, "user", "Already waiting")
    await runner.process_event(job.job_id, "evt_pr", "pr_opened", {"pr_url": "http://pr/1", "pr_number": 1})

    ok = await runner.process_event(
        job.job_id, "evt_pr_dup_state", "pr_opened", {"pr_url": "http://pr/1", "pr_number": 1}
    )
    assert ok is True
    stored = await runner.persistence.load_job(job.job_id)
    assert stored.state == PipelineState.WAIT_TESTS


@pytest.mark.asyncio
async def test_tests_passed_before_pr_advances_when_pr_number_present(runner, notifier):
    job = await runner.create_and_start_job(100, 200, "user", "CI first")

    ok = await runner.process_event(
        job.job_id,
        "evt_ci_early",
        "tests_passed",
        {"pr_number": 4, "pr_url": "https://github.com/o/r/pull/4"},
    )
    assert ok is True
    stored = await runner.persistence.load_job(job.job_id)
    assert stored.state == PipelineState.DONE
    assert stored.pr_number == 4
    notifier.notify_pr_opened.assert_awaited()
    notifier.notify_final_result.assert_awaited()


@pytest.mark.asyncio
async def test_tests_passed_stashed_until_pr_opened(runner, notifier):
    job = await runner.create_and_start_job(100, 200, "user", "CI without PR refs")

    ok = await runner.process_event(job.job_id, "evt_ci_stash", "tests_passed", {})
    assert ok is True
    stored = await runner.persistence.load_job(job.job_id)
    assert stored.state == PipelineState.CODING_AGENT_RUNNING
    assert stored.pending_ci_event == "tests_passed"

    await runner.process_event(
        job.job_id,
        "evt_pr_later",
        "pr_opened",
        {"pr_url": "https://github.com/o/r/pull/2", "pr_number": 2},
    )
    stored = await runner.persistence.load_job(job.job_id)
    assert stored.state == PipelineState.DONE
    assert stored.pending_ci_event is None
    assert stored.pr_number == 2
    notifier.notify_final_result.assert_awaited()


@pytest.mark.asyncio
async def test_tests_failed_stashed_until_pr_opened(runner):
    job = await runner.create_and_start_job(100, 200, "user", "Red CI first")

    await runner.process_event(
        job.job_id,
        "evt_ci_fail",
        "tests_failed",
        {"failure_log": "boom"},
    )
    stored = await runner.persistence.load_job(job.job_id)
    assert stored.state == PipelineState.CODING_AGENT_RUNNING
    assert stored.pending_ci_event == "tests_failed"

    await runner.process_event(
        job.job_id,
        "evt_pr",
        "pr_opened",
        {"pr_url": "https://github.com/o/r/pull/3", "pr_number": 3},
    )
    stored = await runner.persistence.load_job(job.job_id)
    assert stored.state == PipelineState.WAIT_TESTS
    assert stored.retry_count == 1
    assert stored.pending_ci_event is None


async def _drain_watcher(runner: PipelineRunner, job_id: str) -> None:
    await asyncio.sleep(0)
    task = runner._watch_tasks.get(job_id)
    if task is not None:
        await asyncio.wait_for(task, timeout=2)


def _watch_event(**fields):
    defaults = {
        "pr_number": None,
        "pr_url": None,
        "comment_id": None,
        "question": None,
        "message": None,
        "issue_number": None,
    }
    defaults.update(fields)
    return type("WatchEvent", (), defaults)()


@pytest.mark.asyncio
async def test_issue_watcher_advances_on_pr_when_webhook_missing(persistence, notifier):
    github = AsyncMock()

    async def create_issue(job: PipelineJob) -> PipelineJob:
        return job.model_copy(update={"issue_number": 7, "issue_url": "https://github.com/o/r/issues/7"})

    github.create_issue = create_issue
    github.trigger_coding_agent = AsyncMock(return_value=True)

    class FakeWatcher:
        async def watch_issue(self, issue_number, job_id=None, timeout=3600.0):
            yield _watch_event(
                event_type="pr_created",
                pr_number=9,
                pr_url="https://github.com/o/r/pull/9",
                issue_number=issue_number,
                message="PR opened",
            )

    runner = PipelineRunner(
        persistence=persistence,
        notifier=notifier,
        github=github,
        issue_watcher=FakeWatcher(),
        max_retries=2,
        enable_ai_review=False,
    )
    job = await runner.create_and_start_job(1, 1, "u", "Watch me")
    await _drain_watcher(runner, job.job_id)

    stored = await persistence.load_job(job.job_id)
    assert stored.state == PipelineState.WAIT_TESTS
    assert stored.pr_number == 9
    notifier.notify_pr_opened.assert_awaited()


@pytest.mark.asyncio
async def test_issue_watcher_agent_completed_finishes_job_without_ci_webhook(persistence, notifier):
    github = AsyncMock()

    async def create_issue(job: PipelineJob) -> PipelineJob:
        return job.model_copy(update={"issue_number": 8, "issue_url": "https://github.com/o/r/issues/8"})

    github.create_issue = create_issue
    github.trigger_coding_agent = AsyncMock(return_value=True)

    class FakeWatcher:
        async def watch_issue(self, issue_number, job_id=None, timeout=3600.0):
            yield _watch_event(
                event_type="pr_created",
                pr_number=4,
                pr_url="https://github.com/o/r/pull/4",
                issue_number=issue_number,
            )
            yield _watch_event(
                event_type="agent_completed",
                pr_number=4,
                pr_url="https://github.com/o/r/pull/4",
                issue_number=issue_number,
                message="Issue closed",
            )

    runner = PipelineRunner(
        persistence=persistence,
        notifier=notifier,
        github=github,
        issue_watcher=FakeWatcher(),
        enable_ai_review=False,
    )
    job = await runner.create_and_start_job(1, 1, "u", "Finish me")
    await _drain_watcher(runner, job.job_id)

    stored = await persistence.load_job(job.job_id)
    assert stored.state == PipelineState.DONE
    assert stored.pr_number == 4
    notifier.notify_pr_opened.assert_awaited()
    notifier.notify_final_result.assert_awaited()


@pytest.mark.asyncio
async def test_issue_watcher_timeout_notifies_and_fails(persistence, notifier):
    github = AsyncMock()

    async def create_issue(job: PipelineJob) -> PipelineJob:
        return job.model_copy(update={"issue_number": 3, "issue_url": "https://github.com/o/r/issues/3"})

    github.create_issue = create_issue
    github.trigger_coding_agent = AsyncMock(return_value=True)

    class TimeoutWatcher:
        async def watch_issue(self, issue_number, job_id=None, timeout=3600.0):
            yield type(
                "E",
                (),
                {
                    "event_type": "adapter_error",
                    "message": f"Watcher timed out after {timeout:.0f}s monitoring issue #{issue_number}.",
                    "issue_number": issue_number,
                },
            )()

    runner = PipelineRunner(
        persistence=persistence,
        notifier=notifier,
        github=github,
        issue_watcher=TimeoutWatcher(),
        watcher_timeout=60,
    )
    job = await runner.create_and_start_job(1, 1, "u", "Hang")
    await _drain_watcher(runner, job.job_id)

    stored = await persistence.load_job(job.job_id)
    assert stored.state == PipelineState.FAILED
    assert "timed out" in (stored.error or "").lower()
    notifier.notify_status_change.assert_awaited()
    notifier.notify_final_result.assert_awaited()


@pytest.mark.asyncio
async def test_submit_answer_posts_user_reply_not_fix(persistence, notifier):
    github = AsyncMock()
    watcher = AsyncMock()
    watcher.send_user_reply = AsyncMock(
        return_value=type("E", (), {"event_type": "user_reply"})()
    )

    runner = PipelineRunner(
        persistence=persistence,
        notifier=notifier,
        github=github,
        issue_watcher=watcher,
    )
    job = PipelineJob(
        chat_id=1,
        user_id=1,
        task_description="Auth",
        state=PipelineState.CODING_AGENT_RUNNING,
        issue_number=12,
    )
    await persistence.save_job(job)

    ok = await runner.submit_answer(job.job_id, "Use OAuth2")

    assert ok is True
    watcher.send_user_reply.assert_awaited_once_with(12, "Use OAuth2", job_id=job.job_id)
    github.trigger_fix.assert_not_called()


@pytest.mark.asyncio
async def test_submit_answer_fails_when_reply_cannot_be_posted(persistence, notifier):
    watcher = AsyncMock()
    watcher.send_user_reply = AsyncMock(
        return_value=type("E", (), {"event_type": "adapter_error"})()
    )
    runner = PipelineRunner(
        persistence=persistence,
        notifier=notifier,
        issue_watcher=watcher,
    )
    job = PipelineJob(
        chat_id=1,
        user_id=1,
        task_description="Auth",
        state=PipelineState.CODING_AGENT_RUNNING,
        issue_number=12,
    )
    await persistence.save_job(job)

    assert await runner.submit_answer(job.job_id, "Use OAuth2") is False


@pytest.mark.asyncio
async def test_issue_watcher_crash_fails_job_after_retries(persistence, notifier):
    github = AsyncMock()

    async def create_issue(job: PipelineJob) -> PipelineJob:
        return job.model_copy(update={"issue_number": 4, "issue_url": "https://github.com/o/r/issues/4"})

    github.create_issue = create_issue
    github.trigger_coding_agent = AsyncMock(return_value=True)

    class CrashWatcher:
        async def watch_issue(self, issue_number, job_id=None, timeout=3600.0):
            raise RuntimeError("boom")
            yield  # pragma: no cover — keep this an async generator

    runner = PipelineRunner(
        persistence=persistence,
        notifier=notifier,
        github=github,
        issue_watcher=CrashWatcher(),
        watcher_crash_limit=2,
        watcher_crash_backoff=0,
    )
    job = await runner.create_and_start_job(1, 1, "u", "Crash me")
    await _drain_watcher(runner, job.job_id)

    stored = await persistence.load_job(job.job_id)
    assert stored.state == PipelineState.FAILED
    assert "crashed" in (stored.error or "").lower()
    notifier.notify_final_result.assert_awaited()
