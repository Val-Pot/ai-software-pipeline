"""
Unit tests for Telegram bot handlers and state management.
"""
import pytest
from unittest.mock import AsyncMock
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey

from orchestrator.stub import StubOrchestrator
from telegram_bot.handlers.commands import cmd_start, cmd_new, cmd_status
from telegram_bot.handlers.messages import process_task_description
from telegram_bot.states import TaskCreation


@pytest.fixture
def orchestrator():
    return StubOrchestrator()


@pytest.fixture
def fsm_context():
    storage = MemoryStorage()
    key = StorageKey(bot_id=123, chat_id=456, user_id=456)
    return FSMContext(storage=storage, key=key)


@pytest.mark.asyncio
async def test_cmd_start(fsm_context):
    message = AsyncMock()
    await cmd_start(message, fsm_context)
    message.answer.assert_called_once()
    assert "Welcome" in message.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_cmd_new_sets_state(fsm_context):
    message = AsyncMock()
    await cmd_new(message, fsm_context)
    state_name = await fsm_context.get_state()
    assert state_name == TaskCreation.waiting_for_task.state


@pytest.mark.asyncio
async def test_process_task_description(fsm_context):
    await fsm_context.set_state(TaskCreation.waiting_for_task)
    message = AsyncMock()
    message.text = "Build a new payment feature"

    await process_task_description(message, fsm_context)
    
    state_name = await fsm_context.get_state()
    data = await fsm_context.get_data()
    assert state_name == TaskCreation.confirm_task.state
    assert data["task_description"] == "Build a new payment feature"
    message.answer.assert_called_once()


@pytest.mark.asyncio
async def test_cmd_status_no_active_job(orchestrator):
    message = AsyncMock()
    message.chat.id = 456
    await cmd_status(message, orchestrator)
    assert "No active pipeline job" in message.answer.call_args[0][0]
