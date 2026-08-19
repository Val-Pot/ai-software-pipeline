from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from domain.clock import utcnow


class JobState(str, Enum):
    TASK_ACCEPTED = "TASK_ACCEPTED"
    CODING_AGENT_RUNNING = "CODING_AGENT_RUNNING"
    WAIT_TESTS = "WAIT_TESTS"
    TEST_PASSED = "TEST_PASSED"
    MERGE_CONFIRMATION_PENDING = "MERGE_CONFIRMATION_PENDING"
    DONE = "DONE"
    FAILED = "FAILED"
    ADAPTER_ERROR = "ADAPTER_ERROR"

    @classmethod
    def terminal(cls) -> frozenset[JobState]:
        return frozenset({cls.DONE, cls.FAILED, cls.ADAPTER_ERROR})


class EventType(str, Enum):
    AGENT_STARTED = "AGENT_STARTED"
    COPILOT_QUESTION = "COPILOT_QUESTION"
    PR_OPENED = "PR_OPENED"
    AGENT_COMPLETED = "AGENT_COMPLETED"
    TESTS_PASSED = "TESTS_PASSED"
    TESTS_FAILED = "TESTS_FAILED"
    ISSUE_CLOSED = "ISSUE_CLOSED"
    ISSUE_UPDATED = "ISSUE_UPDATED"


class PipelineEvent(BaseModel):
    event_id: str
    type: EventType
    issue_number: int | None = None
    pr_number: int | None = None
    body: str = ""
    error_log: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class Job(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    chat_id: int
    user_id: int
    repository: str
    title: str
    body: str
    state: JobState = JobState.TASK_ACCEPTED
    issue_number: int | None = None
    issue_url: str = ""
    pr_number: int | None = None
    pr_url: str = ""
    merge_head_sha: str | None = None
    last_event_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    awaiting_user_reply: bool = False
    agent_started_notified: bool = False
    agent_completed_notified: bool = False
    pipeline_check_posted: bool = False
    issue_closed_notified: bool = False
    state_before_merge: JobState | None = None


class MergeDecision(BaseModel):
    allowed: bool = False
    already_merged: bool = False
    message: str = ""
    head_sha: str | None = None
    pr_url: str = ""
    ci_label: str = ""

    @classmethod
    def deny(cls, message: str) -> MergeDecision:
        return cls(allowed=False, message=message)
