"""
Text message handlers (Task description & Copilot Q&A answers).
"""
from __future__ import annotations

import logging
from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from adapters.base import OrchestratorPort
from telegram_bot.states import TaskCreation, CopilotQA
from telegram_bot.keyboards.inline import confirm_task_keyboard

logger = logging.getLogger(__name__)
router = Router(name="messages")


@router.message(TaskCreation.waiting_for_task, F.text)
async def process_task_description(message: Message, state: FSMContext) -> None:
    task_desc = message.text.strip()
    if not task_desc:
        await message.answer("⚠️ Task description cannot be empty. Please enter your task:")
        return

    await state.update_data(task_description=task_desc)
    await state.set_state(TaskCreation.confirm_task)

    text = (
        "📋 <b>Review Task Submission</b>\n\n"
        f"<b>Task:</b>\n{task_desc}\n\n"
        "Would you like to launch the pipeline with this description?"
    )
    await message.answer(text, reply_markup=confirm_task_keyboard())


@router.message(CopilotQA.waiting_for_answer, F.text)
async def process_copilot_answer(
    message: Message,
    state: FSMContext,
    orchestrator: OrchestratorPort,
) -> None:
    data = await state.get_data()
    job_id = data.get("active_job_id")
    answer = message.text.strip()

    if not job_id or not answer:
        await message.answer("⚠️ Unable to associate answer with an active job.")
        await state.clear()
        return

    success = await orchestrator.submit_answer(job_id=job_id, answer=answer)
    await state.clear()

    if success:
        await message.answer("✅ <b>Answer sent to GitHub Copilot!</b>\nThe agent will resume coding.")
    else:
        await message.answer("❌ Failed to forward answer to Orchestrator. Job might be inactive.")
