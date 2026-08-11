"""
Core Finite State Machine (FSM) Engine for Pipeline Execution.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from orchestrator.states import PipelineState, TERMINAL_STATES
from orchestrator.transitions import can_transition
from orchestrator.context import PipelineJob

logger = logging.getLogger(__name__)


class InvalidTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""
    pass


class FSMEngine:
    """FSM state Machine engine managing state transitions safely."""

    @staticmethod
    def transition(job: PipelineJob, target_state: PipelineState, error_msg: Optional[str] = None) -> PipelineJob:
        """
        Transition job state if allowed. Returns new updated PipelineJob copy.
        """
        current_state = job.state
        if current_state == target_state:
            logger.debug("Job %s is already in state %s", job.short_id, target_state)
            return job

        if not can_transition(current_state, target_state):
            msg = f"Cannot transition job {job.short_id} from {current_state} to {target_state}"
            logger.error(msg)
            raise InvalidTransitionError(msg)

        update_kwargs = {"error": error_msg} if error_msg else {}
        updated_job = job.with_state(target_state, **update_kwargs)
        logger.info("Job %s state transition: %s -> %s", job.short_id, current_state, target_state)
        return updated_job
