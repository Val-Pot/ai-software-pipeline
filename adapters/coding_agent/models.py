"""
Event models and DTOs for the GitHub Copilot Coding Agent Adapter.

These models carry all structured data returned by CodingAgentAdapter
to the Orchestrator. No business logic resides here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class AgentEventType(StrEnum):
    """
    Exhaustive taxonomy of events the Coding Agent Adapter can emit
    to the Orchestrator.
    """

    #: Issue successfully assigned to the Copilot coding agent.
    AGENT_ASSIGNED = "agent_assigned"

    #: Copilot has started actively processing the issue (first comment detected).
    AGENT_STARTED = "agent_started"

    #: Copilot posted a clarifying question on the issue or PR.
    COPILOT_QUESTION = "copilot_question"

    #: Copilot opened a Pull Request for the task.
    PR_CREATED = "pr_created"

    #: Copilot appears to have completed the task (PR merged / issue closed).
    AGENT_COMPLETED = "agent_completed"

    #: A fix-iteration comment ("@copilot fix the failing tests") was published.
    FIX_REQUESTED = "fix_requested"

    #: An unexpected error occurred within the adapter layer.
    ADAPTER_ERROR = "adapter_error"


class AgentStatus(StrEnum):
    """Current perceived lifecycle status of the coding agent session."""

    IDLE = "idle"
    ASSIGNED = "assigned"
    RUNNING = "running"
    WAITING_REPLY = "waiting_reply"
    PR_OPEN = "pr_open"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Event DTO
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CodingAgentEvent(BaseModel):
    """
    Structured event produced by the CodingAgentAdapter and consumed by
    the Orchestrator. Carries all context needed to drive FSM transitions.

    Attributes
    ----------
    event_type:
        Discriminator field indicating what happened.
    job_id:
        Opaque pipeline job identifier (may be absent for standalone usage).
    issue_number:
        GitHub issue number the coding agent is working on.
    pr_number:
        Pull Request number (populated for PR_CREATED / AGENT_COMPLETED events).
    pr_url:
        HTML URL of the Pull Request.
    question:
        Raw text of the clarifying question (populated for COPILOT_QUESTION).
    comment_id:
        GitHub comment ID that triggered the event (for idempotency).
    agent_username:
        GitHub login of the coding agent bot (default: ``github-copilot[bot]``).
    message:
        Human-readable description of the event for logging / notification.
    timestamp:
        UTC timestamp when the event was created.
    """

    event_type: AgentEventType
    job_id: Optional[str] = None
    issue_number: int
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    question: Optional[str] = None
    comment_id: Optional[int] = None
    agent_username: str = "github-copilot[bot]"
    message: str
    timestamp: datetime = Field(default_factory=_utcnow)
