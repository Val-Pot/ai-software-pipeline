"""
Command handlers (/start, /new, /status, /cancel, /help).
"""
from __future__ import annotations

import logging
from aiogram import Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from adapters.base import OrchestratorPort
from telegram_bot.states import TaskCreation

logger = logging.getLogger(__name__)
router = Router(name="commands")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    text = (
        "👋 <b>Welcome to AI Software Development Pipeline!</b>\n\n"
        "I automate code changes end-to-end:\n"
        "<code>Telegram → GitHub Issue → Copilot Coding Agent → PR → CI → Telegram</code>\n\n"
        "<b>Available Commands:</b>\n"
        "/new - Create a new coding task\n"
        "/status - Check active pipeline status\n"
        "/cancel - Cancel current flow / active job\n"
        "/help - Show help message"
    )
    await message.answer(text)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        "ℹ️ <b>Pipeline Bot Help</b>\n\n"
        "1. Run /new and describe the feature or bugfix you want.\n"
        "2. Confirm your submission to open a GitHub Issue.\n"
        "3. GitHub Copilot will work on the issue and open a PR.\n"
        "4. If Copilot needs clarification, I will ask you here.\n"
        "5. Once CI checks pass and AI review completes, you'll receive the PR link!"
    )
    await message.answer(text)


@router.message(Command("new"))
async def cmd_new(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(TaskCreation.waiting_for_task)
    await message.answer(
        "📝 <b>Describe your task</b>\n\n"
        "Please send a detailed description of the feature or bug fix you want to implement:"
    )


@router.message(Command("status"))
async def cmd_status(message: Message, orchestrator: OrchestratorPort) -> None:
    chat_id = message.chat.id
    job = await orchestrator.get_active_job_for_chat(chat_id)
    if not job:
        await message.answer("ℹ️ No active pipeline job found for this chat.")
        return

    text = (
        f"📊 <b>Active Pipeline Status</b>\n\n"
        f"<b>Job ID:</b> <code>{job.short_id}</code>\n"
        f"<b>State:</b> {job.state_label}\n"
        f"<b>Task:</b> {job.task_description}\n"
    )
    if job.issue_url:
        text += f"📋 <a href='{job.issue_url}'>GitHub Issue #{job.issue_number}</a>\n"
    if job.pr_url:
        text += f"🔀 <a href='{job.pr_url}'>Pull Request #{job.pr_number}</a>\n"

    await message.answer(text)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, orchestrator: OrchestratorPort) -> None:
    current_state = await state.get_state()
    await state.clear()

    chat_id = message.chat.id
    job = await orchestrator.get_active_job_for_chat(chat_id)
    if job:
        await orchestrator.cancel_job(job.job_id)
        await message.answer(f"🚫 Active job <code>{job.short_id}</code> has been cancelled.")
    elif current_state:
        await message.answer("🚫 Current action cancelled.")
    else:
        await message.answer("ℹ️ Nothing to cancel.")
