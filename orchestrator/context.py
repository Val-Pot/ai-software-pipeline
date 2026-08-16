"""
Data models and Context for pipeline state execution.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

from orchestrator.states import PipelineState, STATE_LABELS, TERMINAL_STATES


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PipelineJob(BaseModel):
    """Immutable job context holding execution state."""
    job_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    chat_id: int
    user_id: int
    username: Optional[str] = None
    task_description: str
    state: PipelineState = PipelineState.NEW

    # Pipeline Artifacts & Tracking
    issue_number: Optional[int] = None
    issue_url: Optional[str] = None
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    ci_run_id: Optional[int] = None
    ci_run_url: Optional[str] = None
    
    # Retry & Iteration tracking
    retry_count: int = 0
    max_retries: int = 3
    processed_event_ids: Dict[str, str] = Field(default_factory=dict)  # event_id -> processed_at timestamp
    #: CI webhook that arrived before WAIT_TESTS (``tests_passed`` / ``tests_failed``).
    pending_ci_event: Optional[str] = None
    pending_ci_failure_log: Optional[str] = None

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    error: Optional[str] = None

    @property
    def state_label(self) -> str:
        return STATE_LABELS.get(self.state, self.state.value)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def short_id(self) -> str:
        return self.job_id[:8]

    def with_state(self, state: PipelineState, **kwargs: Any) -> PipelineJob:
        """Create copy with updated state and timestamp."""
        return self.model_copy(
            update={"state": state, "updated_at": _utcnow(), **kwargs}
        )
