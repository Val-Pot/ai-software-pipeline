"""
In-memory persistence adapter implementing PersistencePort.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional
from orchestrator.context import PipelineJob
from orchestrator.ports import PersistencePort

logger = logging.getLogger(__name__)


class InMemoryPersistenceAdapter(PersistencePort):
    """In-memory job persistence adapter."""

    def __init__(self) -> None:
        self._store: Dict[str, PipelineJob] = {}

    async def save_job(self, job: PipelineJob) -> None:
        self._store[job.job_id] = job

    async def load_job(self, job_id: str) -> Optional[PipelineJob]:
        return self._store.get(job_id)

    async def load_all_active_jobs(self) -> List[PipelineJob]:
        return [job for job in self._store.values() if not job.is_terminal]
