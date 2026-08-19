from __future__ import annotations

from domain.clock import utcnow
from domain.models import EventType, Job, JobState, PipelineEvent


class InMemoryJobRepository:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    async def save(self, job: Job) -> None:
        job.updated_at = utcnow()
        self._jobs[job.id] = job.model_copy(deep=True)

    async def get(self, job_id: str) -> Job | None:
        job = self._jobs.get(job_id)
        return job.model_copy(deep=True) if job else None

    async def find_by_chat(self, chat_id: int) -> Job | None:
        matches = [
            job
            for job in self._jobs.values()
            if job.chat_id == chat_id and job.state not in JobState.terminal()
        ]
        if not matches:
            matches = [job for job in self._jobs.values() if job.chat_id == chat_id]
        if not matches:
            return None
        latest = max(matches, key=lambda item: item.updated_at)
        return latest.model_copy(deep=True)

    def _pick(self, matches: list[Job]) -> Job | None:
        if not matches:
            return None
        live = [job for job in matches if job.state not in JobState.terminal()]
        chosen = max(live or matches, key=lambda item: item.updated_at)
        return chosen.model_copy(deep=True)

    async def find_by_issue(self, issue_number: int) -> Job | None:
        return self._pick(
            [job for job in self._jobs.values() if job.issue_number == issue_number]
        )

    async def find_by_pr(self, pr_number: int) -> Job | None:
        return self._pick(
            [job for job in self._jobs.values() if job.pr_number == pr_number]
        )

    async def find_waiting_for_pr(self) -> Job | None:
        matches = [
            job
            for job in self._jobs.values()
            if job.state == JobState.CODING_AGENT_RUNNING and not job.pr_number
        ]
        if len(matches) != 1:
            return None
        return matches[0].model_copy(deep=True)

    async def find_by_event(self, event: PipelineEvent) -> Job | None:
        if event.issue_number is not None:
            found = await self.find_by_issue(event.issue_number)
            if found:
                return found
        if event.pr_number is not None:
            found = await self.find_by_pr(event.pr_number)
            if found:
                return found
        if event.issue_number is not None:
            return None
        if event.type == EventType.PR_OPENED:
            return None
        if event.pr_number is not None:
            return await self.find_waiting_for_pr()
        return None

    async def list_non_terminal(self) -> list[Job]:
        return [
            job.model_copy(deep=True)
            for job in self._jobs.values()
            if job.state not in JobState.terminal()
        ]
