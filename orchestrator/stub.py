"""
In-memory Stub implementation of OrchestratorPort for development and testing.
"""
from __future__ import annotations

import logging
from typing import Optional, Dict
from adapters.base import OrchestratorPort
from orchestrator.context import PipelineJob
from orchestrator.states import PipelineState

logger = logging.getLogger(__name__)


class StubOrchestrator(OrchestratorPort):
    """In-memory stub orchestrator satisfying OrchestratorPort interface."""

    def __init__(self) -> None:
        self._jobs: Dict[str, PipelineJob] = {}

    async def submit_task(
        self,
        chat_id: int,
        user_id: int,
        username: Optional[str],
        task_description: str,
    ) -> PipelineJob:
        job = PipelineJob(
            chat_id=chat_id,
            user_id=user_id,
            username=username,
            task_description=task_description,
            state=PipelineState.NEW,
        )
        self._jobs[job.job_id] = job
        logger.info("Submitted task to StubOrchestrator: job_id=%s, chat_id=%s", job.job_id, chat_id)
        return job

    async def get_job_status(self, job_id: str) -> Optional[PipelineJob]:
        return self._jobs.get(job_id)

    async def get_active_job_for_chat(self, chat_id: int) -> Optional[PipelineJob]:
        for job in reversed(list(self._jobs.values())):
            if job.chat_id == chat_id and not job.is_terminal:
                return job
        return None

    async def submit_answer(self, job_id: str, answer: str) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        logger.info("StubOrchestrator received answer for job_id=%s: %s", job_id, answer)
        return True

    async def cancel_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job or job.is_terminal:
            return False
        self._jobs[job_id] = job.with_state(PipelineState.CANCELLED)
        logger.info("Cancelled job in StubOrchestrator: job_id=%s", job_id)
        return True
