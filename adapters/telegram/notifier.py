"""
TelegramNotifier implementing NotifierPort interface.
"""
from __future__ import annotations

import logging
from typing import Optional
from aiogram import Dispatcher
from aiogram.fsm.storage.base import StorageKey

from adapters.base import NotifierPort
from adapters.telegram.client import TelegramBotClient
from orchestrator.context import PipelineJob
from orchestrator.states import PipelineState

logger = logging.getLogger(__name__)


class TelegramNotifier(NotifierPort):
    """Outbound adapter for sending updates to Telegram users."""

    def __init__(self, client: TelegramBotClient, dispatcher: Optional[Dispatcher] = None) -> None:
        self._client = client
        self._dp = dispatcher

    async def notify_status_change(self, chat_id: int, job: PipelineJob, message: str) -> None:
        text = (
            f"<b>Pipeline Status Update</b>\n"
            f"<b>Job ID:</b> <code>{job.short_id}</code>\n"
            f"<b>State:</b> {job.state_label}\n\n"
            f"{message}"
        )
        await self._client.send_message(chat_id=chat_id, text=text)

    async def ask_question(self, chat_id: int, job_id: str, question: str) -> None:
        text = (
            f"🤖 <b>GitHub Copilot Question</b>\n"
            f"<b>Job ID:</b> <code>{job_id[:8]}</code>\n\n"
            f"<i>{question}</i>\n\n"
            f"👇 Please reply directly with your answer."
        )
        # Set FSM state for the user to wait for answer if Dispatcher is provided
        if self._dp:
            from telegram_bot.states import CopilotQA
            key = StorageKey(bot_id=self._client._bot.id, chat_id=chat_id, user_id=chat_id)
            state_ctx = self._dp.fsm.get_context(bot=self._client._bot, key=key)
            await state_ctx.set_state(CopilotQA.waiting_for_answer)
            await state_ctx.update_data(active_job_id=job_id)

        await self._client.send_message(chat_id=chat_id, text=text)

    async def notify_pr_opened(self, chat_id: int, job_id: str, pr_url: str) -> None:
        text = (
            f"🔀 <b>Pull Request Created</b>\n"
            f"<b>Job ID:</b> <code>{job_id[:8]}</code>\n\n"
            f"🔗 <a href='{pr_url}'>View Pull Request on GitHub</a>"
        )
        await self._client.send_message(chat_id=chat_id, text=text)

    async def notify_final_result(self, chat_id: int, job: PipelineJob) -> None:
        if job.state == PipelineState.DONE:
            text = (
                f"🎉 <b>Pipeline Succeeded!</b>\n"
                f"<b>Job ID:</b> <code>{job.short_id}</code>\n"
                f"<b>Task:</b> {job.task_description}\n"
            )
            if job.pr_url:
                text += f"🔗 <a href='{job.pr_url}'>Pull Request</a>\n"
        else:
            text = (
                f"❌ <b>Pipeline Failed</b>\n"
                f"<b>Job ID:</b> <code>{job.short_id}</code>\n"
                f"<b>Error:</b> {job.error or 'Unknown error'}"
            )
        await self._client.send_message(chat_id=chat_id, text=text)
