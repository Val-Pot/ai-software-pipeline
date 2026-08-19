"""Review follow-up: disk jobs, frozen merge SHA, callback always answers."""

from __future__ import annotations

from types import SimpleNamespace

from adapters.jobs.file_store import FileJobRepository
from adapters.telegram.handlers import TelegramHandlers
from config.settings import Settings
from domain.models import EventType, Job, JobState, PipelineEvent
from orchestrator.pipeline_runner import PipelineRunner
from tests.conftest import FakeCodingAgent, FakeGitHub, FakeNotifier, FakePRStore, open_pr, seed_job


async def test_file_store_survives_reload(tmp_path):
    path = tmp_path / "jobs.json"
    repo = FileJobRepository(path)
    job = Job(
        chat_id=100,
        user_id=7,
        repository="acme/repo",
        title="task",
        body="do the thing",
        state=JobState.WAIT_TESTS,
        issue_number=3,
        issue_url="https://github.com/acme/repo/issues/3",
        pr_number=12,
        merge_head_sha="abc123",
    )
    await repo.save(job)
    await repo.replace_processed_event_ids({"comment-started-99"})

    reloaded = FileJobRepository(path)
    loaded = await reloaded.get(job.id)
    assert loaded is not None
    assert loaded.state == JobState.WAIT_TESTS
    assert loaded.pr_number == 12
    assert loaded.merge_head_sha == "abc123"
    assert "comment-started-99" in reloaded.processed_event_ids
    live = await reloaded.list_non_terminal()
    assert [item.id for item in live] == [job.id]


async def test_file_store_does_not_persist_task_contract(tmp_path):
    path = tmp_path / "jobs.json"
    repo = FileJobRepository(path)
    from tests.conftest import complete_contract

    job = Job(
        chat_id=100,
        user_id=7,
        repository="acme/repo",
        title="task",
        body=complete_contract(),
        state=JobState.TASK_ACCEPTED,
        issue_number=3,
        issue_url="https://github.com/acme/repo/issues/3",
    )
    await repo.save(job)
    raw = path.read_text(encoding="utf-8")
    assert "Improve the map" not in raw
    loaded = await repo.get(job.id)
    assert loaded is not None
    assert loaded.body == ""
    reloaded = FileJobRepository(path)
    again = await reloaded.get(job.id)
    assert again is not None
    assert again.body == ""


async def test_file_store_skips_corrupt_file(tmp_path):
    path = tmp_path / "jobs.json"
    path.write_text("{not-json", encoding="utf-8")
    repo = FileJobRepository(path)
    assert await repo.list_non_terminal() == []
    job = Job(
        chat_id=1,
        user_id=1,
        repository="acme/repo",
        title="t",
        body="b",
    )
    await repo.save(job)
    assert (await FileJobRepository(path).get(job.id)) is not None


async def test_recover_after_restart_reloads_jobs_and_notifies(tmp_path, settings):
    path = tmp_path / "jobs.json"
    first = FileJobRepository(path)
    job = await seed_job(first, state=JobState.CODING_AGENT_RUNNING)
    store = FakePRStore()
    notifier = FakeNotifier()
    runner = PipelineRunner(
        settings=settings,
        jobs=first,
        github=FakeGitHub(store),
        coding_agent=FakeCodingAgent(),
        notifier=notifier,
    )
    await runner.process_event(
        PipelineEvent(
            event_id="comment-started-1",
            type=EventType.AGENT_STARTED,
            issue_number=3,
        )
    )

    second_jobs = FileJobRepository(path)
    second_notifier = FakeNotifier()
    restarted = PipelineRunner(
        settings=settings,
        jobs=second_jobs,
        github=FakeGitHub(FakePRStore()),
        coding_agent=FakeCodingAgent(),
        notifier=second_notifier,
    )
    await restarted.recover_active_jobs()

    live = await second_jobs.list_non_terminal()
    assert len(live) == 1
    assert live[0].id == job.id
    assert live[0].state == JobState.CODING_AGENT_RUNNING
    assert "comment-started-1" in restarted.processed_event_ids
    assert any("перезапущен" in text for _, text in second_notifier.texts)
    assert job.id in restarted._watch_tasks


async def test_confirm_merge_uses_frozen_sha_and_rejects_head_move(harness):
    jobs, store, runner, notifier = (
        harness["jobs"],
        harness["store"],
        harness["runner"],
        harness["notifier"],
    )
    job = await seed_job(jobs)
    store.prs[12] = open_pr(head_sha="abc123")
    store.runs["copilot/fix-12"] = [
        {"id": 1, "status": "completed", "conclusion": "success"}
    ]

    await runner.request_merge(job.id)
    store.prs[12].head_sha = "fff999"
    await runner.confirm_merge(job.id, True, operator_id=7)

    assert store.merge_calls == []
    fresh = await jobs.get(job.id)
    assert fresh.state == JobState.WAIT_TESTS
    assert fresh.merge_head_sha is None
    assert any("изменился" in text and "/merge" in text for _, text in notifier.texts)


async def test_confirm_merge_sends_frozen_sha_not_later_head(harness):
    jobs, store, runner = harness["jobs"], harness["store"], harness["runner"]
    job = await seed_job(
        jobs,
        state=JobState.MERGE_CONFIRMATION_PENDING,
        merge_head_sha="pinned-sha",
        state_before_merge=JobState.TEST_PASSED,
    )
    store.prs[12] = open_pr(head_sha="pinned-sha")
    store.runs["copilot/fix-12"] = [
        {"id": 1, "status": "completed", "conclusion": "success"}
    ]

    await runner.confirm_merge(job.id, True, operator_id=7)

    assert store.merge_calls == [
        {"repository": "acme/repo", "number": 12, "sha": "pinned-sha"}
    ]


class _Callback:
    def __init__(self, *, data: str, chat_id: int = 100, user_id: int = 7) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = SimpleNamespace(chat=SimpleNamespace(id=chat_id), answers=[])
        self.answers: list[dict] = []

        async def _message_answer(text: str) -> None:
            self.message.answers.append(text)

        self.message.answer = _message_answer

    async def answer(self, text: str = "", show_alert: bool = False) -> None:
        self.answers.append({"text": text, "alert": show_alert})


async def test_merge_callback_answers_when_job_id_lookup_fails():
    from adapters.jobs.memory import InMemoryJobRepository

    jobs = InMemoryJobRepository()
    settings = Settings(telegram_allowed_user_ids="7")
    calls: list[tuple] = []

    class _Orch:
        async def confirm_merge(
            self, job_id: str, confirmed: bool, *, operator_id: int | None = None
        ) -> None:
            calls.append((job_id, confirmed, operator_id))

    handlers = TelegramHandlers(_Orch(), jobs, settings)
    callback = _Callback(data="merge:confirm")
    await handlers.on_merge_callback(callback)

    assert calls == []
    assert callback.answers
    assert callback.answers[0]["alert"] is True
    assert "Нет активной задачи" in callback.answers[0]["text"]


class _Message:
    def __init__(self, text: str, *, user_id: int = 7, chat_id: int = 100) -> None:
        self.text = text
        self.chat = SimpleNamespace(id=chat_id)
        self.from_user = SimpleNamespace(id=user_id)
        self.reply_to_message = None
        self.answers: list[str] = []

    async def answer(self, text: str) -> None:
        self.answers.append(text)


async def test_pr020_merge_rejects_user_supplied_pr_number():
    from adapters.jobs.memory import InMemoryJobRepository

    jobs = InMemoryJobRepository()
    await seed_job(jobs)
    settings = Settings(telegram_allowed_user_ids="7")
    calls: list[str] = []

    class _Orch:
        async def request_merge(self, job_id: str) -> None:
            calls.append(job_id)

    handlers = TelegramHandlers(_Orch(), jobs, settings)
    message = _Message("/merge 123")
    await handlers.on_merge(message)

    assert calls == []
    assert any("не принимает номер PR" in text for text in message.answers)
    assert any("текущей задачи" in text for text in message.answers)


async def test_pr020_diff_rejects_user_supplied_pr_number():
    from adapters.jobs.memory import InMemoryJobRepository

    jobs = InMemoryJobRepository()
    await seed_job(jobs)
    settings = Settings(telegram_allowed_user_ids="7")
    calls: list[str] = []

    class _Orch:
        async def request_diff(self, job_id: str) -> None:
            calls.append(job_id)

    handlers = TelegramHandlers(_Orch(), jobs, settings)
    message = _Message("/diff 99")
    await handlers.on_diff(message)

    assert calls == []
    assert any("не принимает номер PR" in text for text in message.answers)


async def test_diff_and_merge_fail_closed_without_allowlist():
    from adapters.jobs.memory import InMemoryJobRepository

    jobs = InMemoryJobRepository()
    settings = Settings(telegram_allowed_user_ids="")
    called = {"diff": 0, "merge": 0}

    class _Orch:
        async def request_diff(self, job_id: str) -> None:
            called["diff"] += 1

        async def request_merge(self, job_id: str) -> None:
            called["merge"] += 1

    handlers = TelegramHandlers(_Orch(), jobs, settings)
    diff_msg = _Message("/diff")
    merge_msg = _Message("/merge")
    await handlers.on_diff(diff_msg)
    await handlers.on_merge(merge_msg)

    assert called == {"diff": 0, "merge": 0}
    assert diff_msg.answers == ["Нет доступа."]
    assert merge_msg.answers == ["Нет доступа."]


async def test_pr021_status_fail_closed_without_allowlist():
    from adapters.jobs.memory import InMemoryJobRepository

    jobs = InMemoryJobRepository()
    await seed_job(jobs)
    handlers = TelegramHandlers(None, jobs, Settings(telegram_allowed_user_ids=""))
    message = _Message("/status")
    await handlers.on_status(message)
    assert message.answers == ["Нет доступа."]


async def test_pr025_unauthorized_callback_does_not_confirm():
    from adapters.jobs.memory import InMemoryJobRepository

    jobs = InMemoryJobRepository()
    job = await seed_job(jobs, state=JobState.MERGE_CONFIRMATION_PENDING)
    settings = Settings(telegram_allowed_user_ids="7")
    calls: list[tuple] = []

    class _Orch:
        async def confirm_merge(
            self, job_id: str, confirmed: bool, *, operator_id: int | None = None
        ) -> None:
            calls.append((job_id, confirmed, operator_id))

    handlers = TelegramHandlers(_Orch(), jobs, settings)
    callback = _Callback(data=f"merge:confirm:{job.id}", user_id=99)
    await handlers.on_merge_callback(callback)

    assert calls == []
    assert callback.answers
    assert callback.answers[0]["alert"] is True
    assert callback.answers[0]["text"] == "Нет доступа."


async def test_pr026_unauthorized_cancel_callback_does_not_confirm():
    from adapters.jobs.memory import InMemoryJobRepository

    jobs = InMemoryJobRepository()
    job = await seed_job(jobs, state=JobState.MERGE_CONFIRMATION_PENDING)
    settings = Settings(telegram_allowed_user_ids="7")
    calls: list[tuple] = []

    class _Orch:
        async def confirm_merge(
            self, job_id: str, confirmed: bool, *, operator_id: int | None = None
        ) -> None:
            calls.append((job_id, confirmed, operator_id))

    handlers = TelegramHandlers(_Orch(), jobs, settings)
    callback = _Callback(data=f"merge:cancel:{job.id}", user_id=99)
    await handlers.on_merge_callback(callback)

    assert calls == []
    assert any(item["text"] == "Нет доступа." for item in callback.answers)


async def test_pr028_allowlisted_teammate_callback_reaches_confirm():
    from adapters.jobs.memory import InMemoryJobRepository

    jobs = InMemoryJobRepository()
    job = await seed_job(jobs, user_id=7, state=JobState.MERGE_CONFIRMATION_PENDING)
    settings = Settings(telegram_allowed_user_ids="7,8")
    calls: list[tuple] = []

    class _Orch:
        async def confirm_merge(
            self, job_id: str, confirmed: bool, *, operator_id: int | None = None
        ) -> None:
            calls.append((job_id, confirmed, operator_id))

    handlers = TelegramHandlers(_Orch(), jobs, settings)
    callback = _Callback(data=f"merge:confirm:{job.id}", user_id=8)
    await handlers.on_merge_callback(callback)

    assert calls == [(job.id, True, 8)]


async def test_pr020_rejects_tab_and_newline_pr_number():
    from adapters.jobs.memory import InMemoryJobRepository
    from adapters.telegram.handlers import _command_args

    assert _command_args("/merge\t123") == "123"
    assert _command_args("/merge\n123") == "123"
    assert _command_args("/merge@bot   123") == "123"
    assert _command_args("/diff") == ""

    jobs = InMemoryJobRepository()
    await seed_job(jobs)
    settings = Settings(telegram_allowed_user_ids="7")
    calls: list[str] = []

    class _Orch:
        async def request_merge(self, job_id: str) -> None:
            calls.append(job_id)

    handlers = TelegramHandlers(_Orch(), jobs, settings)
    message = _Message("/merge\t123")
    await handlers.on_merge(message)
    assert calls == []
    assert any("не принимает номер PR" in text for text in message.answers)
