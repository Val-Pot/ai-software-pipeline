"""
Data models and Event DTOs for the Coding Agent Adapter.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Optional
from pydantic import BaseModel, Field


class AgentEventType(StrEnum):
    """Event types produced by the Coding Agent Adapter."""
    AGENT_ASSIGNED = "agent_assigned"
    AGENT_STARTED = "agent_started"
    COPILOT_QUESTION = "copilot_question"
    PR_CREATED = "pr_created"
    AGENT_COMPLETED = "agent_completed"
    FIX_REQUESTED = "fix_requested"


class CodingAgentEvent(BaseModel):
    """Structured event returned to the Orchestrator by CodingAgentAdapter."""
    event_type: AgentEventType
    job_id: Optional[str] = None
    issue_number: int
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    question: Optional[str] = None
    agent_username: str = "github-copilot[bot]"
    message: str
