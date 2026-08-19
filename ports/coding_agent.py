from collections.abc import AsyncIterator
from typing import Protocol

from domain.models import PipelineEvent


class CodingAgentPort(Protocol):
    async def trigger(self, issue_number: int) -> None: ...

    async def trigger_fix_iteration(self, issue_number: int, error_log: str) -> None: ...

    async def watch_issue(self, issue_number: int) -> AsyncIterator[PipelineEvent]: ...

    async def detect_task_completion(
        self, issue_number: int, pr_number: int | None
    ) -> bool: ...

    def parse_webhook_event(self, event_name: str, payload: dict) -> PipelineEvent | None: ...
