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
from orchestrator.ports import GitHubPort, NotifierPort, PersistencePort

logger = logging.getLogger(__name__)


class PipelineRunner:
    """
    Main Orchestrator driving pipeline execution state transitions.
    """

    def __init__(
        self,
        persistence: PersistencePort,
        notifier: Optional[NotifierPort] = None,
        github: Optional[GitHubPort] = None,
        max_retries: int = 3,
        enable_ai_review: bool = True,
    ) -> None:
        self.persistence = persistence
        self.notifier = notifier
        self.github = github
        self.max_retries = max_retries
        self.enable_ai_review = enable_ai_review

    async def recover_active_jobs(self) -> None:
        """Restart recovery: inspect and resume all unfinished jobs."""
        active_jobs = await self.persistence.load_all_active_jobs()
        logger.info("Recovering %d active pipeline jobs...", len(active_jobs))
        for job in active_jobs:
            logger.info("Resuming recovered job_id=%s in state=%s", job.short_id, job.state)
            await self._process_state(job)

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
        """Forward user's answer back to Copilot / GitHub (OrchestratorPort implementation)."""
        job = await self.persistence.load_job(job_id)
        if not job or job.is_terminal or not job.issue_number:
            return False
        if self.github:
            await self.github.trigger_fix(job, answer)
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
        return job

    async def process_event(self, job_id: str, event_id: str, event_type: str, payload: Dict) -> bool:
        """
        Idempotent event consumer for external webhooks / user actions.
        """
        job = await self.persistence.load_job(job_id)
        if not job or job.is_terminal:
            logger.warning("Received event for missing or terminal job_id=%s", job_id)
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
            pr_url = payload.get("pr_url")
            pr_number = payload.get("pr_number")
            job = await self._transition_and_save(job, PipelineState.PR_CREATED, pr_url=pr_url, pr_number=pr_number)
            if self.notifier and pr_url:
                await self.notifier.notify_pr_opened(job.chat_id, job.job_id, pr_url)
            job = await self._transition_and_save(job, PipelineState.WAIT_TESTS)

        elif event_type == "tests_passed":
            job = await self._transition_and_save(job, PipelineState.TEST_PASSED)
            await self._process_state(job)

        elif event_type == "tests_failed":
            failure_log = payload.get("failure_log", "CI tests failed")
            job = await self._handle_test_failure(job, failure_log)

        elif event_type == "copilot_question":
            question = payload.get("question", "GitHub Copilot has a clarifying question.")
            if self.notifier:
                await self.notifier.ask_question(job.chat_id, job.job_id, question)

        return True

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel pipeline job."""
        job = await self.persistence.load_job(job_id)
        if not job or job.is_terminal:
            return False
        job = await self._transition_and_save(job, PipelineState.CANCELLED)
        if self.notifier:
            await self.notifier.notify_status_change(job.chat_id, job, "Pipeline cancelled by user.")
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
