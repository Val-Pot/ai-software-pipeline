"""
Port interfaces (Protocols) establishing clean architecture boundaries.
"""
from __future__ import annotations

from typing import Protocol, Optional, runtime_checkable
from orchestrator.context import PipelineJob
from orchestrator.states import PipelineState


@runtime_checkable
class OrchestratorPort(Protocol):
    """Abstract port for the pipeline Orchestrator."""

    async def submit_task(self, chat_id: int, user_id: int, username: Optional[str], task_description: str) -> PipelineJob:
        """Submit a new task to start a pipeline job."""
        ...

    async def get_job_status(self, job_id: str) -> Optional[PipelineJob]:
        """Fetch status of a specific job by ID."""
        ...

    async def get_active_job_for_chat(self, chat_id: int) -> Optional[PipelineJob]:
        """Fetch the active non-terminal job for a Telegram chat."""
        ...

    async def submit_answer(self, job_id: str, answer: str) -> bool:
        """Forward user's answer back to Copilot / GitHub."""
        ...

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel an ongoing pipeline job."""
        ...


@runtime_checkable
class NotifierPort(Protocol):
    """Abstract port for outbound Telegram notifications."""

    async def notify_status_change(self, chat_id: int, job: PipelineJob, message: str) -> None:
        """Notify user about a state update in the pipeline."""
        ...

    async def ask_question(self, chat_id: int, job_id: str, question: str) -> None:
        """Send a question from Copilot to the user and await response."""
        ...

    async def notify_pr_opened(self, chat_id: int, job_id: str, pr_url: str) -> None:
        """Send Pull Request link to the user."""
        ...

    async def notify_final_result(self, chat_id: int, job: PipelineJob) -> None:
        """Send final status (DONE/FAILED) to the user."""
        ...
