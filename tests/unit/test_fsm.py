"""
Comprehensive unit tests for Orchestrator FSM Engine & Pipeline Runner.
"""
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
