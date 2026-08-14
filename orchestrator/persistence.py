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

    async def find_active_job_by_github_refs(
        self,
        *,
        issue_number: Optional[int] = None,
        pr_number: Optional[int] = None,
    ) -> Optional[PipelineJob]:
        """
        Match an active job to a GitHub webhook.

        Prefer an exact issue/PR number match. If nothing matches and there is
        exactly one active job (MVP: one repo, one in-flight task), use it.
        """
        active = await self.load_all_active_jobs()
        if not active:
            return None

        matched = [job for job in active if _job_matches_github_refs(job, issue_number, pr_number)]
        if len(matched) == 1:
            return matched[0]
        if len(matched) > 1:
            return max(matched, key=lambda job: job.updated_at)

        if len(active) == 1:
            logger.info(
                "No GitHub ref match (issue=%s pr=%s); using sole active job_id=%s",
                issue_number,
                pr_number,
                active[0].short_id,
            )
            return active[0]

        logger.warning(
            "Could not uniquely resolve job from GitHub refs issue=%s pr=%s (%d active jobs)",
            issue_number,
            pr_number,
            len(active),
        )
        return None


def _job_matches_github_refs(
    job: PipelineJob,
    issue_number: Optional[int],
    pr_number: Optional[int],
) -> bool:
    if issue_number:
        if job.issue_number == issue_number or job.pr_number == issue_number:
            return True
    if pr_number:
        if job.pr_number == pr_number:
            return True
    return False
