"""
Abstract Ports for external services used by the Orchestrator.
Includes GitHubPort, NotifierPort, and PersistencePort.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Optional, Protocol, List, runtime_checkable
from orchestrator.context import PipelineJob


@runtime_checkable
class GitHubPort(Protocol):
    """Abstract port for GitHub operations required by Orchestrator."""

    async def create_issue(self, job: PipelineJob) -> PipelineJob:
        """Create GitHub issue for task."""
        ...

    async def trigger_coding_agent(self, job: PipelineJob) -> bool:
        """Assign/Label issue to trigger coding agent."""
        ...

    async def trigger_fix(self, job: PipelineJob, test_failure_log: str) -> bool:
        """Trigger coding agent to fix test failure."""
        ...

    async def run_ai_review(self, job: PipelineJob) -> bool:
        """Post AI review on Pull Request."""
        ...


@runtime_checkable
class IssueWatcherPort(Protocol):
    """Optional polling fallback when GitHub webhooks do not arrive."""

    def watch_issue(
        self,
        issue_number: int,
        job_id: Optional[str] = None,
        timeout: float = 3600.0,
    ) -> AsyncIterator[Any]:
        """Yield coding-agent lifecycle events for an issue."""
        ...

    async def send_user_reply(
        self,
        issue_number: int,
        reply_text: str,
        job_id: Optional[str] = None,
    ) -> Any:
        """Post the user's Telegram answer as an issue comment."""
        ...


@runtime_checkable
class PersistencePort(Protocol):
    """Abstract storage port for job persistence and recovery."""

    async def save_job(self, job: PipelineJob) -> None:
        """Persist pipeline job state."""
        ...

    async def load_job(self, job_id: str) -> Optional[PipelineJob]:
        """Load pipeline job state by ID."""
        ...

    async def load_all_active_jobs(self) -> List[PipelineJob]:
        """Load all active (non-terminal) pipeline jobs for restart recovery."""
        ...

    async def find_active_job_by_github_refs(
        self,
        *,
        issue_number: Optional[int] = None,
        pr_number: Optional[int] = None,
    ) -> Optional[PipelineJob]:
        """Resolve an active job from GitHub issue/PR numbers when webhook has no job_id."""
        ...


@runtime_checkable
class NotifierPort(Protocol):
    """Abstract port for outbound user notifications."""

    async def notify_status_change(self, chat_id: int, job: PipelineJob, message: str) -> None:
        """Notify status change."""
        ...

    async def ask_question(self, chat_id: int, job_id: str, question: str) -> None:
        """Ask user a question."""
        ...

    async def notify_pr_opened(self, chat_id: int, job_id: str, pr_url: str) -> None:
        """Notify PR creation."""
        ...

    async def notify_final_result(self, chat_id: int, job: PipelineJob) -> None:
        """Notify final execution result."""
        ...
