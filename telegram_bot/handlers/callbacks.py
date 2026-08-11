"""
Inline callback query handlers.
"""
from __future__ import annotations

import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from adapters.base import OrchestratorPort
from telegram_bot.states import TaskCreation

logger = logging.getLogger(__name__)
router = Router(name="callbacks")


@router.callback_query(F.data == "confirm_task", TaskCreation.confirm_task)
async def callback_confirm_task(
    callback: CallbackQuery,
    state: FSMContext,
    orchestrator: OrchestratorPort,
) -> None:
    data = await state.get_data()
    task_desc = data.get("task_description")
    user = callback.from_user

    if not task_desc:
        await callback.answer("Error: Task description missing.", show_alert=True)
        await state.clear()
        return

    job = await orchestrator.submit_task(
        chat_id=callback.message.chat.id,
        user_id=user.id,
        username=user.username,
        task_description=task_desc,
    )
    await state.clear()

    await callback.message.edit_text(
        f"🚀 <b>Pipeline Launched!</b>\n\n"
        f"<b>Job ID:</b> <code>{job.short_id}</code>\n"
        f"<b>State:</b> {job.state_label}\n\n"
        f"I will keep you updated on progress!"
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_task")
async def callback_cancel_task(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("🚫 Task creation cancelled.")
    await callback.answer()
