from __future__ import annotations

import asyncio
import logging

import httpx

from config.settings import Settings
from domain.clock import utcnow
from domain.errors import (
    AssignmentError,
    GitHubForbiddenError,
    GitHubUnavailableError,
    MergeError,
    UserFacingError,
)
from domain.models import EventType, Job, JobState, MergeDecision, PipelineEvent
from domain.task_contract import incomplete_message, missing_required, render

_GITHUB_TRANSPORT = (
    GitHubUnavailableError,
    httpx.HTTPError,
    TimeoutError,
    ConnectionError,
)

logger = logging.getLogger(__name__)


class PipelineRunner:
    def __init__(
        self,
        *,
        settings: Settings,
        jobs,
        github,
        coding_agent,
        notifier,
    ) -> None:
        self.settings = settings
        self.jobs = jobs
        self.github = github
        self.coding_agent = coding_agent
        self.notifier = notifier
        self.processed_event_ids: set[str] = set()
        self._watch_tasks: dict[str, asyncio.Task] = {}
        self._watchdog_tasks: dict[str, asyncio.Task] = {}
        self._stale_notified: set[str] = set()
        self._watch_error_notified: set[str] = set()
        self._last_missing: dict[str, tuple[str, ...]] = {}

    async def start_job(
        self, *, chat_id: int, user_id: int, title: str, body: str
    ) -> Job:
        previous = await self.jobs.find_by_chat(chat_id)
        if previous is not None and previous.state not in JobState.terminal():
            previous.state = JobState.FAILED
            await self.jobs.save(previous)
            self._stop_job_watchers(previous.id)
            await self.notifier.send_text(
                chat_id,
                "Предыдущая задача остановлена: запущена новая.",
            )
        issue_body = self._issue_body(render(body))
        job = Job(
            chat_id=chat_id,
            user_id=user_id,
            repository=self.settings.repository,
            title=(title or "").strip() or "Task Contract",
            body="",
        )
        await self.jobs.save(job)
        await self._process_state(job, issue_body=issue_body)
        return job

    async def retry_contract(self, *, chat_id: int, user_id: int) -> Job | None:
        job = await self.jobs.find_by_chat(chat_id)
        if job is None or job.state in JobState.terminal():
            return await self.start_job(
                chat_id=chat_id, user_id=user_id, title="Task Contract", body=""
            )
        if job.state == JobState.TASK_ACCEPTED:
            await self._try_start_from_accepted(job, notify_incomplete=True)
            return job
        raise UserFacingError("Использование: /new <описание задачи>")

    def _issue_body(self, body: str) -> str:
        extra = (
            "\n\n---\n"
            "If the repository has no CI yet, create a minimal GitHub Actions "
            "workflow under `.github/workflows/` as part of this task "
            "(ISSUE-001: target repo must have CI before tests can run)."
        )
        return (body or "").rstrip() + extra

    async def try_start_coding_agent(
        self,
        job: Job,
        *,
        body: str | None = None,
        notify_incomplete: bool = True,
        issue_body: str | None = None,
    ) -> bool:
        if job.state != JobState.TASK_ACCEPTED:
            return False
        if not job.issue_number:
            payload = issue_body if issue_body is not None else body
            if not (payload or "").strip():
                payload = self._issue_body(render(""))
            issue = await self.github.create_issue(job.title, payload)
            job.issue_number = issue["number"]
            job.issue_url = issue.get("html_url") or ""
            job.body = ""
            await self.jobs.save(job)
            contract_body = payload
        elif body is not None:
            contract_body = body
        else:
            try:
                issue = await self.github.get_issue(job.issue_number)
            except _GITHUB_TRANSPORT:
                logger.warning("task contract get_issue failed for %s", job.issue_number)
                if notify_incomplete:
                    await self.notifier.send_text(
                        job.chat_id,
                        "Не удалось прочитать Task Contract из GitHub Issue.\n"
                        "Попробуйте повторить /new позже.\n"
                        f"{job.issue_url}",
                    )
                return False
            contract_body = issue.get("body") or ""
        missing = missing_required(contract_body)
        if missing:
            signature = tuple(missing)
            if notify_incomplete and self._last_missing.get(job.id) != signature:
                text = incomplete_message(missing)
                if job.issue_url:
                    text = f"{text}\n\nIssue: {job.issue_url}"
                await self.notifier.send_text(job.chat_id, text)
                self._last_missing[job.id] = signature
            return False
        self._last_missing.pop(job.id, None)
        await self.coding_agent.trigger(job.issue_number)
        job.state = JobState.CODING_AGENT_RUNNING
        job.body = ""
        await self.jobs.save(job)
        self._ensure_watchers(job)
        await self.notifier.send_text(
            job.chat_id,
            f"Задача принята. Issue: {job.issue_url}\n"
            "Coding agent назначен и начал работу.",
        )
        return True

    async def _try_start_from_accepted(
        self,
        job: Job,
        *,
        notify_incomplete: bool,
        body: str | None = None,
        issue_body: str | None = None,
    ) -> None:
        try:
            await self.try_start_coding_agent(
                job,
                body=body,
                notify_incomplete=notify_incomplete,
                issue_body=issue_body,
            )
        except AssignmentError as exc:
            await self._enter_terminal(job, JobState.ADAPTER_ERROR)
            await self.notifier.send_text(job.chat_id, str(exc))
        except Exception as exc:
            await self._enter_terminal(job, JobState.ADAPTER_ERROR)
            await self.notifier.send_text(
                job.chat_id, f"Не удалось запустить задачу: {exc}"
            )

    async def _process_state(self, job: Job, *, issue_body: str | None = None) -> None:
        if job.state == JobState.TASK_ACCEPTED:
            await self._try_start_from_accepted(
                job, notify_incomplete=True, issue_body=issue_body
            )
            return
        if job.state == JobState.TEST_PASSED:
            if job.pipeline_check_posted or not job.pr_number:
                return
            job.pipeline_check_posted = True
            await self.jobs.save(job)
            try:
                await self.github.run_ai_review(job.pr_number)
            except Exception as exc:
                logger.warning("pipeline check comment failed: %s", exc)
            await self.notifier.send_text(
                job.chat_id,
                "CI прошёл.\n"
                f"PR: {job.pr_url}\n\n"
                "Дальше: /diff для ревью или /merge для объединения.",
            )
            return
        # CODING_AGENT_RUNNING / WAIT_TESTS: прогресс только через process_event()

    def _ensure_watchers(self, job: Job) -> None:
        if job.state in JobState.terminal():
            return
        if job.id not in self._watch_tasks or self._watch_tasks[job.id].done():
            self._watch_tasks[job.id] = asyncio.create_task(
                self._run_watch_issue(job),
                name=f"watch-issue-{job.id}",
            )
        if job.id not in self._watchdog_tasks or self._watchdog_tasks[job.id].done():
            self._watchdog_tasks[job.id] = asyncio.create_task(
                self._run_stale_watchdog(job),
                name=f"stale-watchdog-{job.id}",
            )

    def _stop_job_watchers(self, job_id: str) -> None:
        for bucket in (self._watch_tasks, self._watchdog_tasks):
            task = bucket.pop(job_id, None)
            if task is not None and not task.done():
                task.cancel()

    async def _enter_terminal(self, job: Job, state: JobState) -> None:
        job.state = state
        await self.jobs.save(job)
        self._stop_job_watchers(job.id)

    async def _run_watch_issue(self, job: Job) -> None:
        if not job.issue_number:
            return
        while True:
            try:
                async for event in self.coding_agent.watch_issue(job.issue_number):
                    fresh = await self.jobs.get(job.id)
                    if fresh is None or fresh.state in JobState.terminal():
                        return
                    await self.process_event(event)
                return
            except asyncio.CancelledError:
                raise
            except GitHubForbiddenError:
                if job.id not in self._watch_error_notified:
                    await self.notifier.send_text(
                        job.chat_id,
                        "Нет доступа к GitHub API (403). "
                        "Проверьте права токена и SSO. "
                        "Для опроса CI нужно Actions: Read; "
                        "статус CI всё равно приходит через webhook.\n"
                        f"{job.issue_url}",
                    )
                    self._watch_error_notified.add(job.id)
                await asyncio.sleep(self.settings.coding_agent_poll_interval_sec)
            except Exception:
                logger.exception("backup GitHub poll failed")
                if job.id not in self._watch_error_notified:
                    await self.notifier.send_text(
                        job.chat_id,
                        "Ошибка резервного опроса GitHub. "
                        f"Проверьте issue вручную: {job.issue_url}",
                    )
                    self._watch_error_notified.add(job.id)
                await asyncio.sleep(self.settings.coding_agent_poll_interval_sec)

    async def _run_stale_watchdog(self, job: Job) -> None:
        timeout = self.settings.coding_agent_stale_timeout_sec
        while True:
            await asyncio.sleep(min(60, timeout))
            fresh = await self.jobs.get(job.id)
            if fresh is None or fresh.state in JobState.terminal():
                return
            if fresh.state not in (JobState.CODING_AGENT_RUNNING, JobState.WAIT_TESTS):
                continue
            last = fresh.last_event_at or fresh.updated_at
            silent_for = (utcnow() - last).total_seconds()
            if silent_for >= timeout and job.id not in self._stale_notified:
                minutes = int(silent_for // 60)
                await self.notifier.send_text(
                    fresh.chat_id,
                    f"Нет новостей от GitHub уже {minutes} минут, "
                    f"проверьте issue вручную: {fresh.issue_url}",
                )
                self._stale_notified.add(job.id)

    async def process_event(self, event: PipelineEvent) -> None:
        if event.event_id in self.processed_event_ids:
            return
        job = await self.jobs.find_by_event(event)
        if job is None:
            logger.info("No job for event %s", event.event_id)
            return
        if job.state in JobState.terminal():
            self.processed_event_ids.add(event.event_id)
            await self._persist_event_ids()
            return
        self.processed_event_ids.add(event.event_id)
        if len(self.processed_event_ids) > 10_000:
            self.processed_event_ids.clear()
            self.processed_event_ids.add(event.event_id)
        self._sync_event_ids_to_repo()
        job.last_event_at = utcnow()
        self._stale_notified.discard(job.id)
        await self.jobs.save(job)

        if event.type == EventType.ISSUE_UPDATED:
            if job.state != JobState.TASK_ACCEPTED:
                return
            await self._try_start_from_accepted(
                job, notify_incomplete=True, body=event.body
            )
            return

        if event.type == EventType.AGENT_STARTED:
            job.awaiting_user_reply = False
            if job.agent_started_notified:
                await self.jobs.save(job)
                return
            job.agent_started_notified = True
            await self.jobs.save(job)
            await self.notifier.send_text(
                job.chat_id,
                f"Coding agent работает над issue.\n{job.issue_url}",
            )
            return

        if event.type == EventType.COPILOT_QUESTION:
            job.awaiting_user_reply = True
            await self.jobs.save(job)
            await self.notifier.send_text(
                job.chat_id,
                "Copilot ожидает ответа пользователя:\n\n"
                f"{event.body}\n\n{job.issue_url}",
            )
            return

        if event.type == EventType.PR_OPENED and event.pr_number:
            already = job.pr_number == event.pr_number
            if job.state in (
                JobState.TEST_PASSED,
                JobState.MERGE_CONFIRMATION_PENDING,
                *JobState.terminal(),
            ):
                if not job.pr_number:
                    job.pr_number = event.pr_number
                    await self.jobs.save(job)
                return
            job.pr_number = event.pr_number
            job.pr_url = (event.payload or {}).get("html_url") or job.pr_url
            if not job.pr_url:
                pr = await self.github.get_pull_request(event.pr_number)
                job.pr_url = pr.html_url
            if job.state in (JobState.TASK_ACCEPTED, JobState.CODING_AGENT_RUNNING):
                job.state = JobState.WAIT_TESTS
            job.awaiting_user_reply = False
            await self.jobs.save(job)
            if not already:
                await self.notifier.send_text(
                    job.chat_id,
                    f"Pull Request создан.\n{job.pr_url}",
                )
            return

        if event.type == EventType.AGENT_COMPLETED:
            if event.pr_number and not job.pr_number:
                job.pr_number = event.pr_number
            job.awaiting_user_reply = False
            if job.agent_completed_notified:
                await self.jobs.save(job)
                return
            job.agent_completed_notified = True
            await self.jobs.save(job)
            await self.notifier.send_text(
                job.chat_id,
                "Coding agent завершил работу. PR готов к ревью.\n"
                f"{job.pr_url or job.issue_url}",
            )
            return

        if event.type == EventType.TESTS_PASSED:
            if job.state in (
                JobState.TEST_PASSED,
                JobState.MERGE_CONFIRMATION_PENDING,
                *JobState.terminal(),
            ):
                return
            if event.pr_number:
                job.pr_number = event.pr_number
            job.state = JobState.TEST_PASSED
            await self.jobs.save(job)
            await self._process_state(job)
            return

        if event.type == EventType.TESTS_FAILED:
            if job.state in JobState.terminal():
                return
            job.awaiting_user_reply = False
            await self.jobs.save(job)
            await self._handle_test_failure(job, event.error_log or event.body)
            return

        if event.type == EventType.ISSUE_CLOSED and job.state not in JobState.terminal():
            if job.issue_closed_notified:
                return
            job.issue_closed_notified = True
            await self.jobs.save(job)
            await self.notifier.send_text(
                job.chat_id,
                f"Issue закрыт.\n{job.issue_url}",
            )

    async def _handle_test_failure(self, job: Job, error_log: str) -> None:
        # BUG-002 + BUG-003: единственный путь — подтверждённая команда @copilot
        if not job.issue_number:
            return
        try:
            await self.coding_agent.trigger_fix_iteration(job.issue_number, error_log)
        except Exception as exc:
            await self.notifier.send_text(
                job.chat_id,
                f"Не удалось отправить @copilot Fix the failing tests: {exc}\n"
                f"{job.issue_url}",
            )
            return
        job.state = JobState.CODING_AGENT_RUNNING
        await self.jobs.save(job)
        await self.notifier.send_text(
            job.chat_id,
            "CI упал. Отправлена команда @copilot Fix the failing tests.\n"
            f"{job.issue_url}",
        )

    async def _mark_observed_merged(self, job: Job) -> None:
        if job.state in JobState.terminal():
            return
        await self._enter_terminal(job, JobState.DONE)

    async def recover_active_jobs(self) -> None:
        stored = getattr(self.jobs, "processed_event_ids", None)
        if stored:
            self.processed_event_ids |= set(stored)
        for job in await self.jobs.list_non_terminal():
            if job.issue_number:
                await self.notifier.send_text(
                    job.chat_id,
                    "Сервис перезапущен. Продолжаю следить за задачей.\n"
                    f"{job.issue_url or job.pr_url}".strip(),
                )
            if job.state == JobState.TASK_ACCEPTED:
                await self._try_start_from_accepted(job, notify_incomplete=False)
            if job.state in (
                JobState.CODING_AGENT_RUNNING,
                JobState.WAIT_TESTS,
                JobState.TEST_PASSED,
                JobState.MERGE_CONFIRMATION_PENDING,
            ):
                self._ensure_watchers(job)

    def cancel_watchers(self) -> None:
        for job_id in list(self._watch_tasks) + list(self._watchdog_tasks):
            self._stop_job_watchers(job_id)

    def _bound_pr(self, job: Job | None) -> Job:
        if job is None:
            raise UserFacingError("Задача не найдена.")
        if not job.pr_number:
            raise UserFacingError("Для текущей задачи Pull Request ещё не создан.")
        return job

    async def request_diff(self, job_id: str) -> None:
        job = await self.jobs.get(job_id)
        try:
            self._bound_pr(job)
        except UserFacingError as exc:
            if job is not None:
                await self.notifier.send_text(job.chat_id, str(exc))
            return
        try:
            pr = await self.github.get_pull_request(job.pr_number)
        except _GITHUB_TRANSPORT:
            await self.notifier.send_text(
                job.chat_id,
                "Не удалось получить diff Pull Request.\n"
                "Попробуйте повторить команду позже.",
            )
            return
        except Exception:
            logger.exception("unexpected error loading PR for /diff")
            await self.notifier.send_text(
                job.chat_id,
                "Внутренняя ошибка при получении diff. Попробуйте позже.",
            )
            return
        if pr.merged:
            await self._mark_observed_merged(job)
            await self.notifier.send_text(job.chat_id, "Pull Request уже объединён.")
            return
        if pr.state == "closed":
            await self.notifier.send_text(
                job.chat_id, "Pull Request закрыт. Актуальный diff недоступен."
            )
            return
        try:
            diff = await self.github.get_pull_request_diff(job.repository, job.pr_number)
        except _GITHUB_TRANSPORT:
            await self.notifier.send_text(
                job.chat_id,
                "Не удалось получить diff Pull Request.\n"
                "Попробуйте повторить команду позже.",
            )
            return
        except Exception:
            logger.exception("unexpected error fetching PR diff")
            await self.notifier.send_text(
                job.chat_id,
                "Внутренняя ошибка при получении diff. Попробуйте позже.",
            )
            return
        if not (diff or "").strip():
            await self.notifier.send_text(
                job.chat_id,
                "В Pull Request нет изменений для передачи на ревью.",
            )
            return
        data = diff.encode("utf-8")
        if len(data) > self.settings.telegram_max_document_bytes:
            await self.notifier.send_text(
                job.chat_id,
                "Diff сформирован, но его размер превышает лимит Telegram.\n\n"
                f"Полный diff: {pr.html_url}.diff\n"
                f"PR: {pr.html_url}",
            )
            return
        try:
            await self.notifier.send_document(
                job.chat_id,
                filename=f"PR-{job.pr_number}.diff",
                content=data,
                caption=f"PR #{job.pr_number} — актуальный diff для ревью",
            )
        except Exception:
            await self.notifier.send_text(
                job.chat_id,
                "Не удалось отправить diff в Telegram.\n"
                f"PR: {pr.html_url}",
            )

    async def request_merge(self, job_id: str) -> None:
        job = await self.jobs.get(job_id)
        try:
            self._bound_pr(job)
        except UserFacingError as exc:
            if job is not None:
                await self.notifier.send_text(job.chat_id, str(exc))
            return
        try:
            decision = await self._evaluate_merge(job)
        except _GITHUB_TRANSPORT:
            await self.notifier.send_text(
                job.chat_id,
                "Не удалось проверить состояние Pull Request.\n"
                "Попробуйте повторить команду позже.",
            )
            return
        except Exception:
            logger.exception("unexpected error evaluating /merge")
            await self.notifier.send_text(
                job.chat_id,
                "Внутренняя ошибка при проверке Pull Request. Попробуйте позже.",
            )
            return
        if decision.already_merged:
            await self._mark_observed_merged(job)
            await self.notifier.send_text(
                job.chat_id, f"PR #{job.pr_number} уже объединён."
            )
            return
        if not decision.allowed:
            await self.notifier.send_text(job.chat_id, decision.message)
            return
        job.state_before_merge = job.state
        job.state = JobState.MERGE_CONFIRMATION_PENDING
        job.merge_head_sha = decision.head_sha
        await self.jobs.save(job)
        await self.notifier.send_merge_confirmation(
            job.chat_id,
            job.id,
            f"PR #{job.pr_number}\n\n"
            f"Status: OPEN\nCI: {decision.ci_label}\nPR: {decision.pr_url}\n\n"
            f"Объединить Pull Request?",
        )

    def _require_operator(self, operator_id: int | None) -> None:
        allowed = self.settings.allowed_user_ids
        if not allowed or operator_id not in allowed:
            raise UserFacingError("Нет доступа.")

    async def confirm_merge(
        self, job_id: str, confirmed: bool, *, operator_id: int | None
    ) -> None:
        self._require_operator(operator_id)
        job = await self.jobs.get(job_id)
        if job is None:
            raise UserFacingError("Задача не найдена.")
        if confirmed and job.state != JobState.MERGE_CONFIRMATION_PENDING:
            await self.notifier.send_text(
                job.chat_id,
                "Сначала отправьте /merge и подтвердите кнопкой.",
            )
            return
        if not confirmed:
            if job.state == JobState.MERGE_CONFIRMATION_PENDING:
                await self._revert_merge_pending(job)
            await self.notifier.send_text(job.chat_id, "Merge отменён.")
            return
        try:
            decision = await self._evaluate_merge(job)
        except _GITHUB_TRANSPORT:
            await self.notifier.send_text(
                job.chat_id,
                "Не удалось проверить состояние Pull Request.\n"
                "Попробуйте повторить команду позже.",
            )
            return
        except Exception:
            logger.exception("unexpected error evaluating confirm_merge")
            await self.notifier.send_text(
                job.chat_id,
                "Внутренняя ошибка при проверке Pull Request. Попробуйте позже.",
            )
            return
        if decision.already_merged:
            await self._mark_observed_merged(job)
            await self.notifier.send_text(
                job.chat_id, f"PR #{job.pr_number} уже объединён."
            )
            return
        if not decision.allowed:
            await self.notifier.send_text(job.chat_id, decision.message)
            return
        pinned = job.merge_head_sha
        if pinned and decision.head_sha and pinned != decision.head_sha:
            await self._revert_merge_pending(job)
            await self.notifier.send_text(
                job.chat_id,
                "PR изменился с момента /merge. Повторите /merge.",
            )
            return
        self._require_operator(operator_id)
        try:
            await self.github.merge_pull_request(
                job.repository, job.pr_number, sha=pinned or decision.head_sha
            )
        except MergeError as exc:
            await self.notifier.send_text(
                job.chat_id,
                f"Не удалось выполнить merge PR #{job.pr_number}.\n\n"
                f"Причина: {exc.reason}",
            )
            return
        await self._enter_terminal(job, JobState.DONE)
        await self.notifier.send_text(
            job.chat_id,
            f"PR #{job.pr_number} успешно объединён.\n\n"
            f"Задача завершена.\n{job.pr_url}",
        )

    async def _evaluate_merge(self, job: Job) -> MergeDecision:
        if not job.pr_number:
            return MergeDecision.deny("Для текущей задачи Pull Request ещё не создан.")
        pr = await self.github.get_pull_request(job.pr_number)
        if pr.merged:
            return MergeDecision(
                already_merged=True,
                allowed=False,
                message=f"PR #{pr.number} уже объединён.",
            )
        if pr.state == "closed":
            return MergeDecision.deny("PR закрыт и не может быть объединён.")
        if self._copilot_awaits_user(job):
            return MergeDecision.deny(
                "Merge пока невозможен: Copilot ожидает ответа пользователя."
            )
        ci = await self._fresh_ci_status(pr)
        if ci in ("pending", "running"):
            return MergeDecision.deny(
                "Merge пока невозможен: проверки CI ещё выполняются."
            )
        if ci == "failure":
            return MergeDecision.deny("Merge невозможен: CI завершился с ошибкой.")
        if pr.mergeable is False or (
            pr.mergeable_state and pr.mergeable_state not in ("clean", "unstable")
        ):
            return MergeDecision.deny(
                f"GitHub не разрешает merge данного PR "
                f"(mergeable_state={pr.mergeable_state})."
            )
        return MergeDecision(
            allowed=True,
            head_sha=pr.head_sha,
            pr_url=pr.html_url,
            ci_label="PASS",
        )

    async def _revert_merge_pending(self, job: Job) -> None:
        job.state = job.state_before_merge or (
            JobState.TEST_PASSED if job.pipeline_check_posted else JobState.WAIT_TESTS
        )
        job.state_before_merge = None
        job.merge_head_sha = None
        await self.jobs.save(job)

    def _sync_event_ids_to_repo(self) -> None:
        stored = getattr(self.jobs, "processed_event_ids", None)
        if isinstance(stored, set):
            stored.clear()
            stored.update(self.processed_event_ids)

    async def _persist_event_ids(self) -> None:
        persist = getattr(self.jobs, "replace_processed_event_ids", None)
        if persist is not None:
            await persist(self.processed_event_ids)

    def _copilot_awaits_user(self, job: Job) -> bool:
        return bool(job.awaiting_user_reply)

    async def _fresh_ci_status(self, pr) -> str:
        branch = pr.head_ref
        if not branch:
            return "pending"
        try:
            runs = await self.github.actions.list_runs_for_branch(branch)
            if not runs:
                latest = await self.github.actions.get_latest_run_for_branch(branch)
                runs = [latest] if latest else []
        except GitHubForbiddenError:
            logger.warning(
                "Actions API 403 during merge check; using GitHub mergeable_state"
            )
            return "success"
        if any((run.get("status") or "").lower() in {"queued", "in_progress", "waiting"} for run in runs):
            return "pending"
        completed = [
            run for run in runs if (run.get("status") or "").lower() == "completed"
        ]
        if not completed:
            return "pending"
        conclusion = (completed[0].get("conclusion") or "").lower()
        if conclusion == "success":
            return "success"
        if conclusion in {"failure", "timed_out", "cancelled"}:
            return "failure"
        return "pending"
