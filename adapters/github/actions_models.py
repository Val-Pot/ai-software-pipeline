"""
GitHub Actions Workflow status and conclusion Enums, Models, and Event DTOs.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class WorkflowStatus(StrEnum):
    """Workflow run status lifecycle states."""
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    UNKNOWN = "unknown"


class WorkflowConclusion(StrEnum):
    """Workflow run execution outcome conclusions."""
    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    NEUTRAL = "neutral"
    SKIPPED = "skipped"
    ACTION_REQUIRED = "action_required"
    UNKNOWN = "unknown"


class ActionsWorkflowRun(BaseModel):
    """Detailed model representing a GitHub Actions workflow run."""
    id: int
    name: str
    status: WorkflowStatus
    conclusion: Optional[WorkflowConclusion] = None
    html_url: str
    head_branch: str
    head_sha: str
    event: str
    created_at: str
    updated_at: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ActionsWorkflowRun:
        status_raw = data.get("status", "unknown")
        conclusion_raw = data.get("conclusion")

        status = WorkflowStatus(status_raw) if status_raw in WorkflowStatus._value2member_map_ else WorkflowStatus.UNKNOWN
        conclusion = WorkflowConclusion(conclusion_raw) if conclusion_raw in WorkflowConclusion._value2member_map_ else (WorkflowConclusion.UNKNOWN if conclusion_raw else None)

        return cls(
            id=data["id"],
            name=data.get("name", "CI Workflow"),
            status=status,
            conclusion=conclusion,
            html_url=data.get("html_url", ""),
            head_branch=data.get("head_branch", ""),
            head_sha=data.get("head_sha", ""),
            event=data.get("event", "push"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


class ActionsStatusEvent(BaseModel):
    """Structured event returned by the GitHub Actions Adapter to the Orchestrator."""
    run_id: int
    workflow_name: str
    status: WorkflowStatus
    conclusion: Optional[WorkflowConclusion] = None
    branch: str
    sha: str
    html_url: str
    event_type: str  # e.g., "tests_passed", "tests_failed", "tests_running"
    message: str
