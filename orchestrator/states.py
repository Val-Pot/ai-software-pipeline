"""
Exhaustive FSM Pipeline states as specified for the Orchestrator module.
"""
from enum import StrEnum
from typing import Dict, FrozenSet


class PipelineState(StrEnum):
    """
    State taxonomy for the FSM Orchestrator.
    """
    NEW = "NEW"
    TASK_ACCEPTED = "TASK_ACCEPTED"
    CODING_AGENT_RUNNING = "CODING_AGENT_RUNNING"
    PR_CREATED = "PR_CREATED"
    WAIT_TESTS = "WAIT_TESTS"
    TEST_FAILED = "TEST_FAILED"
    FIX_ITERATION = "FIX_ITERATION"
    TEST_PASSED = "TEST_PASSED"
    AI_REVIEW = "AI_REVIEW"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


#: Human-readable labels for user notifications
STATE_LABELS: Dict[PipelineState, str] = {
    PipelineState.NEW: "🆕 Task Created",
    PipelineState.TASK_ACCEPTED: "📋 Task Accepted",
    PipelineState.CODING_AGENT_RUNNING: "⚙️ Coding Agent Running",
    PipelineState.PR_CREATED: "🔀 Pull Request Created",
    PipelineState.WAIT_TESTS: "🏗️ Waiting for CI Tests",
    PipelineState.TEST_FAILED: "❌ Tests Failed",
    PipelineState.FIX_ITERATION: "🔄 Fix Iteration",
    PipelineState.TEST_PASSED: "✅ Tests Passed",
    PipelineState.AI_REVIEW: "🔍 AI Reviewing Code",
    PipelineState.DONE: "🎉 Completed Successfully",
    PipelineState.FAILED: "❌ Pipeline Failed",
    PipelineState.CANCELLED: "🚫 Cancelled",
}

#: Terminal states where no further transitions occur
TERMINAL_STATES: FrozenSet[PipelineState] = frozenset({
    PipelineState.DONE,
    PipelineState.FAILED,
    PipelineState.CANCELLED,
})
