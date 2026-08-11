"""
Event contracts and DTOs passed between Telegram, Webhooks, and Orchestrator.
"""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field

from app.models.pipeline import PipelineState


class TelegramUserMessage(BaseModel):
    """Event sent when user submits a new task via Telegram."""
    chat_id: int
    user_id: int
    username: Optional[str] = None
    text: str


class CopilotQuestion(BaseModel):
    """Event triggered when Copilot asks a clarifying question on an issue/PR."""
    job_id: str
    chat_id: int
    question: str
    context: Optional[str] = None


class UserAnswer(BaseModel):
    """Event sent when user replies to Copilot's question via Telegram."""
    job_id: str
    chat_id: int
    user_id: int
    answer: str


class PipelineStatusUpdate(BaseModel):
    """Event emitted when the pipeline transitions state."""
    job_id: str
    chat_id: int
    new_state: PipelineState
    message: str
    pr_url: Optional[str] = None
    error: Optional[str] = None
