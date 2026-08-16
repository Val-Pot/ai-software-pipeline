"""
Pipeline Runner Orchestrator implementing complete FSM workflow logic.
Supports retry bounds, timeouts, restart recovery, and idempotent events.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict

from orchestrator.states import PipelineState
from orchestrator.context import PipelineJob
from orchestrator.fsm import FSMEngine, InvalidTransitionError
from orchestrator.ports import GitHubPort, IssueWatcherPort, NotifierPort, PersistencePort

logger = logging.getLogger(__name__)

_SENTINEL_JOB_IDS = {"", "active_job", "n/a", "unresolved"}

_WATCHER_TO_ORCHESTRATOR = {
    "pr_created": "pr_opened",
    "copilot_question": "copilot_question",
    "agent_completed": "agent_completed",
}


def _optional_int(value: object) -> Optional[int]:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


class PipelineRunner:
    """
    Main Orchestrator driving pipeline execution state transitions.
    """

    def __init__(
        self,
        persistence: PersistencePort,
        notifier: Optional[NotifierPort] = None,
        github: Optional[GitHubPort] = None,
        issue_watcher: Optional[IssueWatcherPort] = None,
        max_retries: int = 3,
        enable_ai_review: bool = True,
        watcher_timeout: float = 3600.0,
        watcher_crash_limit: int = 3,
        watcher_crash_backoff: float = 2.0,
    ) -> None:
        self.persistence = persistence
        self.notifier = notifier
        self.github = github
        self.issue_watcher = issue_watcher
        self.max_retries = max_retries
        self.enable_ai_review = enable_ai_review
        self.watcher_timeout = watcher_timeout
        self.watcher_crash_limit = watcher_crash_limit
        self.watcher_crash_backoff = watcher_crash_backoff
        self._watch_tasks: Dict[str, asyncio.Task] = {}

    async def recover_active_jobs(self) -> None:
        """Restart recovery: inspect and resume all unfinished jobs."""
        active_jobs = await self.persistence.load_all_active_jobs()
        logger.info("Recovering %d active pipeline jobs...", len(active_jobs))
        for job in active_jobs:
            logger.info("Resuming recovered job_id=%s in state=%s", job.short_id, job.state)
            if job.state == PipelineState.TASK_ACCEPTED:
                await self._process_state(job)
                job = await self.persistence.load_job(job.job_id) or job
            if job.state == PipelineState.CODING_AGENT_RUNNING:
                self._start_issue_watcher(job)

    async def submit_task(
        self,
        chat_id: int,
        user_id: int,
        username: Optional[str],
        task_description: str,
    ) -> PipelineJob:
        """Submit a new task to start a pipeline job (OrchestratorPort implementation)."""
        return await self.create_and_start_job(chat_id, user_id, username, task_description)

    async def get_job_status(self, job_id: str) -> Optional[PipelineJob]:
        """Fetch status of a specific job by ID (OrchestratorPort implementation)."""
        return await self.persistence.load_job(job_id)

    async def get_active_job_for_chat(self, chat_id: int) -> Optional[PipelineJob]:
        """Fetch the active non-terminal job for a Telegram chat (OrchestratorPort implementation)."""
        all_jobs = await self.persistence.load_all_active_jobs()
        for job in reversed(all_jobs):
            if job.chat_id == chat_id:
                return job
        return None

    async def submit_answer(self, job_id: str, answer: str) -> bool:
        """Forward the user's Telegram reply to the GitHub issue (not a fix-iteration)."""
        job = await self.persistence.load_job(job_id)
        if not job or job.is_terminal or not job.issue_number:
            return False
        send_reply = getattr(self.issue_watcher, "send_user_reply", None)
        if send_reply is None:
            logger.error(
                "Cannot forward user reply for job_id=%s — no issue watcher with send_user_reply",
                job.short_id,
            )
            return False
        event = await send_reply(job.issue_number, answer, job_id=job.job_id)
        raw_type = getattr(event, "event_type", "")
        raw_type = getattr(raw_type, "value", raw_type)
        if str(raw_type) == "adapter_error":
            logger.error("Failed to post user reply for job_id=%s", job.short_id)
            return False
        logger.info("Posted user reply to issue #%s for job_id=%s", job.issue_number, job.short_id)
        note = getattr(self.issue_watcher, "note_watcher_activity", None)
        if callable(note):
            maybe = note(job.issue_number)
            if asyncio.iscoroutine(maybe):
                await maybe
        return True

    async def create_and_start_job(
        self,
        chat_id: int,
        user_id: int,
        username: Optional[str],
        task_description: str,
    ) -> PipelineJob:
        """Create new job in state NEW and start FSM execution."""
        job = PipelineJob(
            chat_id=chat_id,
            user_id=user_id,
            username=username,
            task_description=task_description,
            state=PipelineState.NEW,
            max_retries=self.max_retries,
        )
        await self.persistence.save_job(job)
        logger.info("Created new pipeline job_id=%s", job.short_id)

        # Transition to TASK_ACCEPTED and process synchronously for predictable state flow
        job = await self._transition_and_save(job, PipelineState.TASK_ACCEPTED)
        await self._process_state(job)
        updated = await self.persistence.load_job(job.job_id) or job
        if updated.state == PipelineState.CODING_AGENT_RUNNING:
            self._start_issue_watcher(updated)
        return updated

    async def process_event(self, job_id: str, event_id: str, event_type: str, payload: Dict) -> bool:
        """
        Idempotent event consumer for external webhooks / user actions.
        """
        job = await self._resolve_job(job_id, payload)
        if not job or job.is_terminal:
            logger.warning(
                "Received event for missing or terminal job job_id=%s issue=%s pr=%s",
                job_id or "-",
                payload.get("issue_number"),
                payload.get("pr_number"),
            )
            return False

        # Idempotency check
        if event_id in job.processed_event_ids:
            logger.info("Skipping already processed event_id=%s for job_id=%s", event_id, job_id)
            return True

        # Mark event processed
        job.processed_event_ids[event_id] = datetime.now(timezone.utc).isoformat()
        await self.persistence.save_job(job)

        # Route event to FSM transition
        if event_type == "pr_opened":
            await self._handle_pr_opened(job, payload)

        elif event_type == "tests_passed":
            await self._handle_ci_result(job, "tests_passed", payload)

        elif event_type == "tests_failed":
            await self._handle_ci_result(job, "tests_failed", payload)

        elif event_type == "copilot_question":
            question = payload.get("question", "GitHub Copilot has a clarifying question.")
            if self.notifier:
                await self.notifier.ask_question(job.chat_id, job.job_id, question)

        elif event_type == "agent_completed":
            await self._handle_agent_completed(job, payload)

        elif event_type == "watcher_timeout":
            timeout_msg = payload.get("message") or "No news from GitHub; polling timed out."
            if self.notifier:
                await self.notifier.notify_status_change(
                    job.chat_id,
                    job,
                    timeout_msg,
                )
            job = await self._transition_and_save(
                job,
                PipelineState.FAILED,
                error=timeout_msg,
            )
            if self.notifier:
                await self.notifier.notify_final_result(job.chat_id, job)
            await self._stop_issue_watcher(job.job_id)

        return True

    async def _resolve_job(self, job_id: str, payload: Dict) -> Optional[PipelineJob]:
        """Find the pipeline job for a webhook: explicit job_id, then GitHub issue/PR refs."""
        if job_id and job_id.strip().lower() not in _SENTINEL_JOB_IDS:
            job = await self.persistence.load_job(job_id)
            if job is not None:
                return job
            logger.info("job_id=%s not found, falling back to GitHub issue/PR refs", job_id)

        issue_number = _optional_int(payload.get("issue_number"))
        pr_number = _optional_int(payload.get("pr_number"))
        job = await self.persistence.find_active_job_by_github_refs(
            issue_number=issue_number,
            pr_number=pr_number,
        )
        if job is not None:
            logger.info(
                "Resolved webhook to job_id=%s via GitHub refs issue=%s pr=%s",
                job.short_id,
                issue_number,
                pr_number,
            )
        return job

    async def _handle_pr_opened(self, job: PipelineJob, payload: Dict) -> PipelineJob:
        """Record a PR and enter WAIT_TESTS, then apply any CI result that arrived early."""
        if job.state == PipelineState.CODING_AGENT_RUNNING:
            pr_url = self._resolve_pr_url(job, payload)
            pr_number = payload.get("pr_number") or job.pr_number
            job = await self._transition_and_save(
                job, PipelineState.PR_CREATED, pr_url=pr_url, pr_number=pr_number
            )
            if self.notifier and pr_url:
                await self.notifier.notify_pr_opened(job.chat_id, job.job_id, str(pr_url))
            job = await self._transition_and_save(job, PipelineState.WAIT_TESTS)
        elif job.state == PipelineState.PR_CREATED:
            job = await self._transition_and_save(job, PipelineState.WAIT_TESTS)
        else:
            logger.info(
                "Skipping pr_opened for job_id=%s in state=%s (already advanced or webhook duplicate)",
                job.short_id,
                job.state,
            )
        return await self._apply_pending_ci(job)

    async def _handle_ci_result(self, job: PipelineJob, result: str, payload: Dict) -> PipelineJob:
        """Apply CI now, or stash it until the job reaches WAIT_TESTS."""
        job = await self._advance_to_wait_tests_if_possible(job, payload)
        if job.state != PipelineState.WAIT_TESTS:
            logger.info(
                "Stashing %s for job_id=%s in state=%s (PR not known yet)",
                result,
                job.short_id,
                job.state,
            )
            job = job.model_copy(
                update={
                    "pending_ci_event": result,
                    "pending_ci_failure_log": payload.get("failure_log"),
                }
            )
            await self.persistence.save_job(job)
            return job
        return await self._apply_ci_in_wait_tests(job, result, payload.get("failure_log"))

    async def _advance_to_wait_tests_if_possible(self, job: PipelineJob, payload: Dict) -> PipelineJob:
        """If CI named a PR, jump CODING_AGENT_RUNNING → WAIT_TESTS so the result is not dropped."""
        pr_url = self._resolve_pr_url(job, payload)
        pr_number = payload.get("pr_number") or job.pr_number
        if job.state == PipelineState.CODING_AGENT_RUNNING:
            if not pr_number and not pr_url:
                return job
            job = await self._transition_and_save(
                job, PipelineState.PR_CREATED, pr_url=pr_url, pr_number=pr_number
            )
            if self.notifier and pr_url:
                await self.notifier.notify_pr_opened(job.chat_id, job.job_id, str(pr_url))
            return await self._transition_and_save(job, PipelineState.WAIT_TESTS)
        if job.state == PipelineState.PR_CREATED:
            return await self._transition_and_save(job, PipelineState.WAIT_TESTS)
        return job

    async def _apply_pending_ci(self, job: PipelineJob) -> PipelineJob:
        pending = job.pending_ci_event
        if not pending or job.state != PipelineState.WAIT_TESTS:
            return job
        failure_log = job.pending_ci_failure_log
        job = job.model_copy(update={"pending_ci_event": None, "pending_ci_failure_log": None})
        await self.persistence.save_job(job)
        return await self._apply_ci_in_wait_tests(job, pending, failure_log)

    async def _apply_ci_in_wait_tests(
        self,
        job: PipelineJob,
        result: str,
        failure_log: Optional[str],
    ) -> PipelineJob:
        if job.state != PipelineState.WAIT_TESTS:
            return job
        if result == "tests_passed":
            job = await self._transition_and_save(job, PipelineState.TEST_PASSED)
            await self._process_state(job)
            return await self.persistence.load_job(job.job_id) or job
        if result == "tests_failed":
            return await self._handle_test_failure(job, failure_log or "CI tests failed")
        return job

    @staticmethod
    def _resolve_pr_url(job: PipelineJob, payload: Dict) -> Optional[str]:
        pr_url = payload.get("pr_url") or job.pr_url
        if pr_url:
            return str(pr_url)
        pr_number = payload.get("pr_number") or job.pr_number
        if pr_number and job.issue_url and "/issues/" in job.issue_url:
            return f"{job.issue_url.rsplit('/issues/', 1)[0]}/pull/{pr_number}"
        return None

    async def _handle_agent_completed(self, job: PipelineJob, payload: Dict) -> None:
        """Advance a job when polling (or a webhook) sees the agent/PR finished."""
        if job.state == PipelineState.CODING_AGENT_RUNNING:
            pr_url = payload.get("pr_url")
            pr_number = payload.get("pr_number")
            job = await self._transition_and_save(
                job, PipelineState.PR_CREATED, pr_url=pr_url, pr_number=pr_number
            )
            if self.notifier and pr_url:
                await self.notifier.notify_pr_opened(job.chat_id, job.job_id, str(pr_url))
            job = await self._transition_and_save(job, PipelineState.WAIT_TESTS)

        if job.state == PipelineState.PR_CREATED:
            job = await self._transition_and_save(job, PipelineState.WAIT_TESTS)

        if job.state == PipelineState.WAIT_TESTS:
            job = await self._transition_and_save(job, PipelineState.TEST_PASSED)
            await self._process_state(job)

    def _start_issue_watcher(self, job: PipelineJob) -> None:
        if self.issue_watcher is None or not job.issue_number:
            return
        existing = self._watch_tasks.get(job.job_id)
        if existing and not existing.done():
            return
        task = asyncio.create_task(
            self._run_issue_watcher(job),
            name=f"watch-issue-{job.short_id}",
        )
        self._watch_tasks[job.job_id] = task
        logger.info(
            "Started issue watcher for job_id=%s issue=#%s timeout=%.0fs",
            job.short_id,
            job.issue_number,
            self.watcher_timeout,
        )

    async def _stop_issue_watcher(self, job_id: str) -> None:
        task = self._watch_tasks.pop(job_id, None)
        if task is None or task.done():
            return
        if task is asyncio.current_task():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def shutdown_watchers(self) -> None:
        """Cancel all background issue watchers (application shutdown)."""
        job_ids = list(self._watch_tasks)
        for job_id in job_ids:
            await self._stop_issue_watcher(job_id)

    async def _run_issue_watcher(self, job: PipelineJob) -> None:
        """Poll GitHub as a fallback when webhooks are missing or delayed."""
        assert self.issue_watcher is not None
        assert job.issue_number is not None
        crashes = 0
        try:
            while True:
                try:
                    await self._consume_issue_events(job)
                    return
                except asyncio.CancelledError:
                    logger.info("Issue watcher cancelled for job_id=%s", job.short_id)
                    raise
                except Exception:
                    crashes += 1
                    logger.exception(
                        "Issue watcher crashed for job_id=%s (attempt %d/%d)",
                        job.short_id,
                        crashes,
                        self.watcher_crash_limit,
                    )
                    current = await self.persistence.load_job(job.job_id)
                    if not current or current.is_terminal:
                        return
                    if crashes >= self.watcher_crash_limit:
                        await self.process_event(
                            job_id=job.job_id,
                            event_id=f"poll:crash:{job.issue_number}:{crashes}",
                            event_type="watcher_timeout",
                            payload={
                                "message": (
                                    "Issue watcher crashed repeatedly. "
                                    "Check the bot logs and the GitHub issue manually."
                                ),
                                "issue_number": job.issue_number,
                            },
                        )
                        return
                    await asyncio.sleep(
                        self.watcher_crash_backoff * (2 ** (crashes - 1))
                    )
        finally:
            self._watch_tasks.pop(job.job_id, None)

    async def _consume_issue_events(self, job: PipelineJob) -> None:
        """Run one watch_issue generator until it ends or the job is terminal."""
        assert self.issue_watcher is not None
        assert job.issue_number is not None
        async for event in self.issue_watcher.watch_issue(
            job.issue_number,
            job_id=job.job_id,
            timeout=self.watcher_timeout,
        ):
            current = await self.persistence.load_job(job.job_id)
            if not current or current.is_terminal:
                return

            raw_type = getattr(event, "event_type", "")
            raw_type = getattr(raw_type, "value", raw_type)
            raw_type = str(raw_type)
            if raw_type == "adapter_error":
                message = getattr(event, "message", "") or (
                    f"No news from GitHub after {self.watcher_timeout:.0f}s. "
                    "Check the issue manually — the webhook may be unreachable."
                )
                await self.process_event(
                    job_id=job.job_id,
                    event_id=f"poll:timeout:{job.issue_number}",
                    event_type="watcher_timeout",
                    payload={"message": message, "issue_number": job.issue_number},
                )
                return

            mapped = _WATCHER_TO_ORCHESTRATOR.get(raw_type)
            if not mapped:
                continue

            event_id = (
                f"poll:{raw_type}:"
                f"{getattr(event, 'comment_id', None) or getattr(event, 'pr_number', None) or job.issue_number}"
            )
            await self.process_event(
                job_id=job.job_id,
                event_id=event_id,
                event_type=mapped,
                payload={
                    "pr_number": getattr(event, "pr_number", None),
                    "pr_url": getattr(event, "pr_url", None),
                    "issue_number": getattr(event, "issue_number", job.issue_number),
                    "question": getattr(event, "question", None),
                    "message": getattr(event, "message", None),
                },
            )
            current = await self.persistence.load_job(job.job_id)
            if not current or current.is_terminal:
                return

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel pipeline job."""
        job = await self.persistence.load_job(job_id)
        if not job or job.is_terminal:
            return False
        job = await self._transition_and_save(job, PipelineState.CANCELLED)
        if self.notifier:
            await self.notifier.notify_status_change(job.chat_id, job, "Pipeline cancelled by user.")
        await self._stop_issue_watcher(job_id)
        return True

    # ------------------------------------------------------------------
    # Internal FSM state processor
    # ------------------------------------------------------------------

    async def _process_state(self, job: PipelineJob) -> None:
        """Process current state actions and trigger next transitions."""
        try:
            if job.state == PipelineState.TASK_ACCEPTED:
                if self.github:
                    job = await self.github.create_issue(job)
                job = await self._transition_and_save(job, PipelineState.CODING_AGENT_RUNNING)
                if self.github:
                    await self.github.trigger_coding_agent(job)
                if self.notifier:
                    await self.notifier.notify_status_change(job.chat_id, job, "Coding agent started working.")

            elif job.state == PipelineState.TEST_PASSED:
                if self.enable_ai_review:
                    job = await self._transition_and_save(job, PipelineState.AI_REVIEW)
                    if self.github:
                        await self.github.run_ai_review(job)
                    job = await self._transition_and_save(job, PipelineState.DONE)
                else:
                    job = await self._transition_and_save(job, PipelineState.DONE)

                if self.notifier:
                    await self.notifier.notify_final_result(job.chat_id, job)

        except Exception as e:
            logger.error("Error processing job_id=%s in state=%s: %s", job.short_id, job.state, e, exc_info=True)
            job = await self._transition_and_save(job, PipelineState.FAILED, error=str(e))
            if self.notifier:
                await self.notifier.notify_final_result(job.chat_id, job)

        if job.is_terminal:
            await self._stop_issue_watcher(job.job_id)

    async def _handle_test_failure(self, job: PipelineJob, failure_log: str) -> PipelineJob:
        """Handle test failures with configurable retry limits."""
        current_retries = job.retry_count + 1
        if current_retries >= job.max_retries:
            logger.warning("Max retries (%d) reached for job_id=%s", job.max_retries, job.short_id)
            job = await self._transition_and_save(
                job,
                PipelineState.TEST_FAILED,
                retry_count=current_retries,
            )
            job = await self._transition_and_save(
                job,
                PipelineState.FAILED,
                error=f"Max retries ({job.max_retries}) exceeded due to test failures.",
            )
            if self.notifier:
                await self.notifier.notify_final_result(job.chat_id, job)
            await self._stop_issue_watcher(job.job_id)
            return job

        job = await self._transition_and_save(
            job,
            PipelineState.TEST_FAILED,
            retry_count=current_retries,
        )
        job = await self._transition_and_save(job, PipelineState.FIX_ITERATION)
        
        if self.github:
            await self.github.trigger_fix(job, failure_log)
        if self.notifier:
            await self.notifier.notify_status_change(
                job.chat_id,
                job,
                f"Tests failed. Retrying fix iteration ({job.retry_count}/{job.max_retries})...",
            )
        
        job = await self._transition_and_save(job, PipelineState.WAIT_TESTS)
        return job

    async def _transition_and_save(
        self,
        job: PipelineJob,
        target_state: PipelineState,
        **kwargs: object,
    ) -> PipelineJob:
        """Helper to transition state and save to persistence atomically."""
        error_msg = str(kwargs.get("error")) if "error" in kwargs else None
        updated_job = FSMEngine.transition(job, target_state, error_msg=error_msg)
        if kwargs:
            updated_job = updated_job.model_copy(update=kwargs)
        await self.persistence.save_job(updated_job)
        return updated_job
