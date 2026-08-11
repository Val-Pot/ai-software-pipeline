"""
Aiogram FSM State definitions for Telegram bot user flows.
"""
from aiogram.fsm.state import State, StatesGroup


class TaskCreation(StatesGroup):
    """FSM flow for creating a new pipeline task."""
    waiting_for_task = State()
    confirm_task = State()


class CopilotQA(StatesGroup):
    """FSM state when Copilot asks a question and awaits user answer."""
    waiting_for_answer = State()
