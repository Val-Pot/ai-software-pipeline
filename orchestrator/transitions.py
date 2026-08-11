"""
Allowed transitions map and guard validation logic.
"""
from typing import Dict, Set
from orchestrator.states import PipelineState

ALLOWED_TRANSITIONS: Dict[PipelineState, Set[PipelineState]] = {
    PipelineState.NEW: {PipelineState.TASK_ACCEPTED, PipelineState.CANCELLED, PipelineState.FAILED},
    PipelineState.TASK_ACCEPTED: {PipelineState.CODING_AGENT_RUNNING, PipelineState.PR_CREATED, PipelineState.CANCELLED, PipelineState.FAILED},
    PipelineState.CODING_AGENT_RUNNING: {PipelineState.PR_CREATED, PipelineState.CANCELLED, PipelineState.FAILED},
    PipelineState.PR_CREATED: {PipelineState.WAIT_TESTS, PipelineState.CANCELLED, PipelineState.FAILED},
    PipelineState.WAIT_TESTS: {PipelineState.TEST_PASSED, PipelineState.TEST_FAILED, PipelineState.CANCELLED, PipelineState.FAILED},
    PipelineState.TEST_FAILED: {PipelineState.FIX_ITERATION, PipelineState.FAILED, PipelineState.CANCELLED},
    PipelineState.FIX_ITERATION: {PipelineState.WAIT_TESTS, PipelineState.CANCELLED, PipelineState.FAILED},
    PipelineState.TEST_PASSED: {PipelineState.AI_REVIEW, PipelineState.DONE, PipelineState.CANCELLED, PipelineState.FAILED},
    PipelineState.AI_REVIEW: {PipelineState.DONE, PipelineState.CANCELLED, PipelineState.FAILED},
    PipelineState.DONE: set(),
    PipelineState.FAILED: set(),
    PipelineState.CANCELLED: set(),
}


def can_transition(current_state: PipelineState, target_state: PipelineState) -> bool:
    """Validate whether transitioning from current_state to target_state is permitted."""
    allowed = ALLOWED_TRANSITIONS.get(current_state, set())
    return target_state in allowed
