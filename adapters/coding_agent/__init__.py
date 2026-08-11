"""
adapters/coding_agent package.

Exposes the CodingAgentAdapter and its associated event models.
"""
from adapters.coding_agent.models import (
    AgentEventType,
    CodingAgentEvent,
    AgentStatus,
)
from adapters.coding_agent.adapter import CodingAgentAdapter

__all__ = [
    "AgentEventType",
    "AgentStatus",
    "CodingAgentEvent",
    "CodingAgentAdapter",
]
