from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from config.settings import Settings
from domain.errors import UserFacingError

logger = logging.getLogger(__name__)

router = Router()

_COMMAND_ARGS_RE = re.compile(r"^/\w+(?:@\w+)?(?:\s+|$)(.*)$", re.DOTALL)


def _command_args(text: str | None) -> str:
    raw = (text or "").strip()
    match = _COMMAND_ARGS_RE.match(raw)
    if match:
        return match.group(1).strip()
    parts = raw.split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""


def _authorized(user_id: int | None, settings: Settings) -> bool:
    allowed = settings.allowed_user_ids
    if not allowed:
        return False
    return user_id in allowed


class TelegramHandlers:
    """Command surface. Telegram never talks to GitHub directly."""

    def __init__(self, orchestrator, jobs, settings: Settings) -> None:
        self.orchestrator = orchestrator
        self.jobs = jobs
        self.settings = settings

    async def _job_id_for_chat(self, chat_id: int) -> str:
        job = await self.jobs.find_by_chat(chat_id)
        if job is None:
            raise UserFacingError("Нет активной задачи. Отправьте /new с описанием.")
        return job.id

    async def on_start(self, message: Message) -> None:
        if not _authorized(message.from_user.id if message.from_user else None, self.settings):
            await message.answer("Нет доступа.")
            return
        await message.answer(
            "AI Software Pipeline\n\n"
            "/new <задача> — создать Issue с Task Contract и назначить Copilot\n"
            "/new — повторить проверку контракта текущей задачи\n"
            "/diff — актуальный unified diff текущего PR\n"
            "/merge — проверка CI и подтверждение merge\n"
            "/status — состояние текущей задачи\n\n"
            "Coding agent запускается только при полном Task Contract."
        )

    async def on_new(self, message: Message) -> None:
        if not _authorized(message.from_user.id if message.from_user else None, self.settings):
            await message.answer("Нет доступа.")
            return
        text = _command_args(message.text)
        if not text and message.reply_to_message:
            text = (message.reply_to_message.text or "").strip()
        if not text:
            try:
                await self.orchestrator.retry_contract(
                    chat_id=message.chat.id,
                    user_id=message.from_user.id if message.from_user else 0,
                )
            except UserFacingError as exc:
                await message.answer(str(exc))
            return
        title = text.splitlines()[0][:80]
        await self.orchestrator.start_job(
            chat_id=message.chat.id,
            user_id=message.from_user.id if message.from_user else 0,
            title=title,
            body=text,
        )

    def _reject_pr_number_arg(self, message: Message, command: str) -> str | None:
        extra = _command_args(message.text)
        if not extra:
            return None
        return (
            f"Команда {command} не принимает номер PR.\n"
            "Используется Pull Request текущей задачи."
        )

    async def on_diff(self, message: Message) -> None:
        if not _authorized(message.from_user.id if message.from_user else None, self.settings):
            await message.answer("Нет доступа.")
            return
        hint = self._reject_pr_number_arg(message, "/diff")
        if hint:
            await message.answer(hint)
            return
        try:
            job_id = await self._job_id_for_chat(message.chat.id)
            await self.orchestrator.request_diff(job_id=job_id)
        except UserFacingError as exc:
            await message.answer(str(exc))

    async def on_merge(self, message: Message) -> None:
        if not _authorized(message.from_user.id if message.from_user else None, self.settings):
            await message.answer("Нет доступа.")
            return
        hint = self._reject_pr_number_arg(message, "/merge")
        if hint:
            await message.answer(hint)
            return
        try:
            job_id = await self._job_id_for_chat(message.chat.id)
            await self.orchestrator.request_merge(job_id=job_id)
        except UserFacingError as exc:
            await message.answer(str(exc))

    async def on_status(self, message: Message) -> None:
        if not _authorized(message.from_user.id if message.from_user else None, self.settings):
            await message.answer("Нет доступа.")
            return
        job = await self.jobs.find_by_chat(message.chat.id)
        if job is None:
            await message.answer("Нет задач.")
            return
        lines = [
            f"Job: {job.id}",
            f"State: {job.state.value}",
            f"Issue: {job.issue_url or '—'}",
            f"PR: {job.pr_url or '—'}",
        ]
        await message.answer("\n".join(lines))

    async def on_merge_callback(self, callback: CallbackQuery) -> None:
        user_id = callback.from_user.id if callback.from_user else None
        if not _authorized(user_id, self.settings):
            await callback.answer("Нет доступа.", show_alert=True)
            return
        data = callback.data or ""
        parts = data.split(":")
        confirmed = len(parts) >= 2 and parts[1] == "confirm"
        job_id = parts[2] if len(parts) >= 3 else ""
        answered = False
        try:
            if not job_id and callback.message:
                job_id = await self._job_id_for_chat(callback.message.chat.id)
            job = await self.jobs.get(job_id) if job_id else None
            chat_id = callback.message.chat.id if callback.message else None
            if job is None or (chat_id is not None and job.chat_id != chat_id):
                await callback.answer("Нет доступа.", show_alert=True)
                answered = True
                return
            await self.orchestrator.confirm_merge(
                job_id, confirmed, operator_id=user_id
            )
        except UserFacingError as exc:
            try:
                await callback.answer(str(exc)[:180], show_alert=True)
                answered = True
            except Exception:
                if callback.message:
                    await callback.message.answer(str(exc))
        except Exception:
            logger.exception("merge callback failed")
            try:
                await callback.answer(
                    "Не удалось обработать подтверждение.", show_alert=True
                )
                answered = True
            except Exception:
                if callback.message:
                    await callback.message.answer(
                        "Не удалось обработать подтверждение."
                    )
        finally:
            if not answered:
                try:
                    await callback.answer()
                except Exception:
                    pass


def register_handlers(dp, handlers: TelegramHandlers) -> None:
    dp.include_router(router)

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        await handlers.on_start(message)

    @router.message(Command("help"))
    async def help_cmd(message: Message) -> None:
        await handlers.on_start(message)

    @router.message(Command("new"))
    async def new_cmd(message: Message) -> None:
        await handlers.on_new(message)

    @router.message(Command("diff"))
    async def diff_cmd(message: Message) -> None:
        await handlers.on_diff(message)

    @router.message(Command("merge"))
    async def merge_cmd(message: Message) -> None:
        await handlers.on_merge(message)

    @router.message(Command("status"))
    async def status_cmd(message: Message) -> None:
        await handlers.on_status(message)

    @router.callback_query(F.data.startswith("merge:"))
    async def merge_cb(callback: CallbackQuery) -> None:
        await handlers.on_merge_callback(callback)
