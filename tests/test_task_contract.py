from __future__ import annotations

from types import SimpleNamespace

from adapters.coding_agent.adapter import CodingAgentAdapter
from adapters.telegram.handlers import TelegramHandlers
from config.settings import Settings
from domain.models import EventType, JobState, PipelineEvent
from domain.task_contract import (
    incomplete_message,
    missing_required,
    parse,
    render,
)
from tests.conftest import complete_contract, seed_job


def test_parser_empty_sections_are_missing():
    assert missing_required("") == [
        "Goal",
        "Scope",
        "Expected Behavior",
        "Architecture Constraints",
        "Acceptance Criteria",
        "Verification",
    ]
    assert missing_required(complete_contract()) == []


def test_parser_na_fills_architecture_constraints():
    body = complete_contract(**{"Architecture Constraints": "N/A"})
    assert "Architecture Constraints" not in missing_required(body)


def test_parser_unknown_headings_and_issue001_footer_do_not_break_required():
    body = complete_contract() + (
        "\n\n---\n"
        "If the repository has no CI yet, create a minimal GitHub Actions "
        "workflow under `.github/workflows/` as part of this task "
        "(ISSUE-001: target repo must have CI before tests can run).\n"
    )
    assert missing_required(body) == []
    sections = parse(body + "\n## Unknown Extra\nignore me\n")
    assert sections["Goal"]


def test_unknown_heading_does_not_fill_empty_required_section():
    text = (
        "## Goal\nDone.\n"
        "## Scope\nMap.\n"
        "## Expected Behavior\nTiles.\n"
        "## Architecture Constraints\nN/A\n"
        "## Acceptance Criteria\nPass.\n"
        "## Verification\n\n"
        "## Notes\nonly a screenshot\n"
    )
    assert parse(text)["Verification"] == ""
    assert "Verification" in missing_required(text)


def test_render_free_text_goes_to_additional_context():
    body = render("please add a button")
    sections = parse(body)
    assert sections["Additional Context"] == "please add a button"
    assert sections["Goal"] == ""
    assert "Goal" in missing_required(body)


TELEGRAM_PLAIN_CONTRACT = """
/new Доработать существующую программу сложения чисел: изменить её с текущего сложения 3 чисел на сложение 2 чисел.

Task Contract:

Goal:
Изменить готовую программу так, чтобы она складывала два числа вместо трёх.

Scope:
Изменить только необходимую логику и связанные с ней элементы интерфейса/ввода.

Out of Scope:
Не добавлять новые функции.

Expected Behavior:
Пользователь вводит два числа. Программа вычисляет и отображает их сумму.

Architecture Constraints:
Использовать существующую архитектуру и соглашения репозитория.

Acceptance Criteria:
1. Программа принимает ровно два числа.
2. Результат равен сумме этих двух чисел.

Verification:
Проверить минимум:
- 1 + 2 = 3
- 10 + 20 = 30

Visual References:
Не требуются.

Additional Context:
Это доработка уже готовой программы в репозитории.
""".strip()


def test_plain_telegram_headings_are_recognized():
    assert missing_required(TELEGRAM_PLAIN_CONTRACT) == []
    sections = parse(TELEGRAM_PLAIN_CONTRACT)
    assert "два числа вместо трёх" in sections["Goal"]
    assert "два числа" in sections["Expected Behavior"]
    assert "1 + 2 = 3" in sections["Verification"]
    assert "Не требуются" in sections["Visual References"]


def test_colon_heading_with_inline_body():
    text = "Goal: Improve the map.\nScope: Map screen only.\n"
    sections = parse(text)
    assert sections["Goal"] == "Improve the map."
    assert sections["Scope"] == "Map screen only."


async def test_v1_empty_new_creates_issue_without_agent(harness):
    runner = harness["runner"]
    job = await runner.start_job(chat_id=100, user_id=7, title="", body="")
    assert job.state == JobState.TASK_ACCEPTED
    assert harness["github"].created_issues
    assert "# Task Contract" in harness["github"].created_issues[0]["body"]
    assert "## Goal" in harness["github"].created_issues[0]["body"]
    assert harness["coding_agent"].trigger_calls == []
    assert any(
        "Task Contract is incomplete" in text for _, text in harness["notifier"].texts
    )
    assert any("Coding Agent was not started" in text for _, text in harness["notifier"].texts)


async def test_v2_only_goal_does_not_start_agent(harness):
    runner = harness["runner"]
    body = render("## Goal\nDo the thing\n")
    job = await runner.start_job(chat_id=100, user_id=7, title="Do the thing", body=body)
    assert job.state == JobState.TASK_ACCEPTED
    assert harness["coding_agent"].trigger_calls == []
    texts = [text for _, text in harness["notifier"].texts]
    incomplete = [text for text in texts if "Task Contract is incomplete" in text]
    assert incomplete
    assert "- Goal" not in incomplete[0]
    assert "- Scope" in incomplete[0]
    assert "- Expected Behavior" in incomplete[0]


async def test_v3_plain_telegram_contract_starts_agent(harness):
    runner = harness["runner"]
    job = await runner.start_job(
        chat_id=100, user_id=7, title="Sum", body=TELEGRAM_PLAIN_CONTRACT
    )
    assert job.state == JobState.CODING_AGENT_RUNNING
    assert harness["coding_agent"].trigger_calls
    created = harness["github"].created_issues[0]["body"]
    assert "## Goal" in created
    assert "два числа вместо трёх" in created


async def test_v3_complete_contract_starts_agent(harness):
    runner = harness["runner"]
    job = await runner.start_job(
        chat_id=100, user_id=7, title="Map", body=complete_contract()
    )
    assert job.state == JobState.CODING_AGENT_RUNNING
    assert harness["coding_agent"].trigger_calls == [job.issue_number]
    assert any("Coding agent назначен" in text for _, text in harness["notifier"].texts)


async def test_v4_visual_references_alone_do_not_start_agent(harness):
    runner = harness["runner"]
    body = (
        "# Task Contract\n\n"
        "## Visual References\n\n"
        "![screenshot](https://example.com/ui.png)\n"
    )
    job = await runner.start_job(chat_id=100, user_id=7, title="UI", body=body)
    assert job.state == JobState.TASK_ACCEPTED
    assert harness["coding_agent"].trigger_calls == []
    assert any("Missing:" in text for _, text in harness["notifier"].texts)


async def test_v5_empty_architecture_constraints_blocks_code_task(harness):
    runner = harness["runner"]
    body = complete_contract(**{"Architecture Constraints": ""})
    job = await runner.start_job(chat_id=100, user_id=7, title="Refactor", body=body)
    assert job.state == JobState.TASK_ACCEPTED
    assert harness["coding_agent"].trigger_calls == []
    assert any(
        "- Architecture Constraints" in text for _, text in harness["notifier"].texts
    )


async def test_v5_na_architecture_constraints_allows_start(harness):
    runner = harness["runner"]
    job = await runner.start_job(
        chat_id=100,
        user_id=7,
        title="Org",
        body=complete_contract(**{"Architecture Constraints": "N/A"}),
    )
    assert job.state == JobState.CODING_AGENT_RUNNING
    assert harness["coding_agent"].trigger_calls


async def test_v6_empty_visual_references_still_starts(harness):
    runner = harness["runner"]
    job = await runner.start_job(
        chat_id=100,
        user_id=7,
        title="Map",
        body=complete_contract(**{"Visual References": ""}),
    )
    assert job.state == JobState.CODING_AGENT_RUNNING
    assert harness["coding_agent"].trigger_calls


async def test_v7_retry_empty_new_starts_after_issue_filled(harness):
    runner = harness["runner"]
    job = await runner.start_job(chat_id=100, user_id=7, title="", body="")
    assert job.state == JobState.TASK_ACCEPTED
    filled = complete_contract()
    harness["store"].issues[job.issue_number]["body"] = filled
    await runner.retry_contract(chat_id=100, user_id=7)
    fresh = await harness["jobs"].get(job.id)
    assert fresh.state == JobState.CODING_AGENT_RUNNING
    assert harness["coding_agent"].trigger_calls == [job.issue_number]


async def test_v7_issue_edited_webhook_starts_agent(harness):
    runner = harness["runner"]
    job = await runner.start_job(chat_id=100, user_id=7, title="", body="")
    filled = complete_contract()
    event = PipelineEvent(
        event_id="issue-edited-1-aaa",
        type=EventType.ISSUE_UPDATED,
        issue_number=job.issue_number,
        body=filled,
    )
    await runner.process_event(event)
    fresh = await harness["jobs"].get(job.id)
    assert fresh.state == JobState.CODING_AGENT_RUNNING
    assert harness["coding_agent"].trigger_calls == [job.issue_number]


async def test_unknown_heading_does_not_start_agent(harness):
    runner = harness["runner"]
    body = (
        "# Task Contract\n\n"
        "## Goal\nDone.\n\n"
        "## Scope\nMap.\n\n"
        "## Expected Behavior\nTiles.\n\n"
        "## Architecture Constraints\nN/A\n\n"
        "## Acceptance Criteria\nPass.\n\n"
        "## Verification\n\n"
        "## Notes\nonly a screenshot\n"
    )
    job = await runner.start_job(chat_id=100, user_id=7, title="Bypass", body=body)
    assert job.state == JobState.TASK_ACCEPTED
    assert harness["coding_agent"].trigger_calls == []
    assert any("- Verification" in text for _, text in harness["notifier"].texts)


async def test_issue_edited_sends_updated_missing_list(harness):
    runner = harness["runner"]
    job = await runner.start_job(chat_id=100, user_id=7, title="", body="")
    harness["notifier"].texts.clear()
    partial = render("## Goal\nDo the thing\n")
    await runner.process_event(
        PipelineEvent(
            event_id="issue-edited-partial",
            type=EventType.ISSUE_UPDATED,
            issue_number=job.issue_number,
            body=partial,
        )
    )
    fresh = await harness["jobs"].get(job.id)
    assert fresh.state == JobState.TASK_ACCEPTED
    assert harness["coding_agent"].trigger_calls == []
    incomplete = [
        text for _, text in harness["notifier"].texts if "Task Contract is incomplete" in text
    ]
    assert incomplete
    assert "- Goal" not in incomplete[-1]
    assert "- Verification" in incomplete[-1]


async def test_issue_edited_ignored_after_agent_started(harness):
    runner = harness["runner"]
    job = await runner.start_job(
        chat_id=100, user_id=7, title="Map", body=complete_contract()
    )
    assert job.state == JobState.CODING_AGENT_RUNNING
    await runner.process_event(
        PipelineEvent(
            event_id="issue-edited-later",
            type=EventType.ISSUE_UPDATED,
            issue_number=job.issue_number,
            body=complete_contract(Goal="changed"),
        )
    )
    assert harness["coding_agent"].trigger_calls == [job.issue_number]
    fresh = await harness["jobs"].get(job.id)
    assert fresh.state == JobState.CODING_AGENT_RUNNING


def test_parse_webhook_issues_edited():
    adapter = CodingAgentAdapter(github=None, settings=Settings())
    event = adapter.parse_webhook_event(
        "issues",
        {
            "action": "edited",
            "issue": {
                "number": 9,
                "body": complete_contract(),
                "updated_at": "2026-08-18T12:00:00Z",
            },
        },
    )
    assert event is not None
    assert event.type == EventType.ISSUE_UPDATED
    assert event.issue_number == 9
    assert event.body.startswith("# Task Contract")
    again = adapter.parse_webhook_event(
        "issues",
        {
            "action": "edited",
            "issue": {
                "number": 9,
                "body": complete_contract(Goal="other"),
                "updated_at": "2026-08-18T12:01:00Z",
            },
        },
    )
    assert again is not None
    assert again.event_id != event.event_id


async def test_free_text_new_does_not_start_agent(harness):
    runner = harness["runner"]
    job = await runner.start_job(
        chat_id=100, user_id=7, title="button", body="please add a button"
    )
    assert job.state == JobState.TASK_ACCEPTED
    assert "please add a button" in harness["github"].created_issues[0]["body"]
    assert "## Additional Context" in harness["github"].created_issues[0]["body"]
    assert harness["coding_agent"].trigger_calls == []


async def test_incomplete_message_lists_only_missing_sections():
    missing = ["Expected Behavior", "Verification"]
    text = incomplete_message(missing)
    assert "- Expected Behavior" in text
    assert "- Verification" in text
    assert "- Goal" not in text


class _Message:
    def __init__(self, text: str, *, user_id: int = 7, chat_id: int = 100) -> None:
        self.text = text
        self.chat = SimpleNamespace(id=chat_id)
        self.from_user = SimpleNamespace(id=user_id)
        self.reply_to_message = None
        self.answers: list[str] = []

    async def answer(self, text: str) -> None:
        self.answers.append(text)


async def test_telegram_empty_new_retries_incomplete_job(harness):
    runner = harness["runner"]
    job = await runner.start_job(chat_id=100, user_id=7, title="", body="")
    harness["store"].issues[job.issue_number]["body"] = complete_contract()
    handlers = TelegramHandlers(runner, harness["jobs"], harness["settings"])
    await handlers.on_new(_Message("/new"))
    fresh = await harness["jobs"].get(job.id)
    assert fresh.state == JobState.CODING_AGENT_RUNNING


async def test_telegram_empty_new_rejected_when_job_already_running(harness):
    jobs = harness["jobs"]
    await seed_job(jobs, state=JobState.CODING_AGENT_RUNNING, pr_number=None)
    handlers = TelegramHandlers(harness["runner"], jobs, harness["settings"])
    message = _Message("/new")
    await handlers.on_new(message)
    assert any("Использование: /new" in text for text in message.answers)
    assert harness["coding_agent"].trigger_calls == []


async def test_job_record_does_not_keep_contract(harness):
    runner = harness["runner"]
    job = await runner.start_job(
        chat_id=100, user_id=7, title="Map", body=complete_contract()
    )
    fresh = await harness["jobs"].get(job.id)
    assert fresh.body == ""
    assert "Improve the map" in harness["github"].created_issues[0]["body"]
