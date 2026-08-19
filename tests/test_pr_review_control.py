"""PR-001 … PR-010 — /diff and /merge control channel."""

from __future__ import annotations

from domain.models import JobState
from tests.conftest import open_pr, seed_job


async def test_pr001_diff_sends_file_and_does_not_mutate_pr(harness):
    jobs, store, runner, notifier = (
        harness["jobs"],
        harness["store"],
        harness["runner"],
        harness["notifier"],
    )
    job = await seed_job(jobs)
    store.prs[12] = open_pr()
    store.diffs[12] = "diff --git a/x b/x\n+hello\n"

    await runner.request_diff(job.id)

    assert store.diff_calls == [12]
    assert store.merge_calls == []
    assert store.prs[12].merged is False
    assert store.prs[12].state == "open"
    assert len(notifier.documents) == 1
    doc = notifier.documents[0]
    assert doc["filename"] == "PR-12.diff"
    assert doc["content"] == b"diff --git a/x b/x\n+hello\n"
    assert "актуальный diff" in doc["caption"]
    fresh = await jobs.get(job.id)
    assert fresh.state == JobState.WAIT_TESTS


async def test_pr002_second_diff_fetches_new_commit(harness):
    jobs, store, runner = harness["jobs"], harness["store"], harness["runner"]
    job = await seed_job(jobs)
    store.prs[12] = open_pr()
    store.diffs[12] = "diff v1\n"
    await runner.request_diff(job.id)
    store.diffs[12] = "diff v2 with new commit\n"
    await runner.request_diff(job.id)

    assert store.diff_calls == [12, 12]
    assert harness["notifier"].documents[-1]["content"] == b"diff v2 with new commit\n"


async def test_pr003_merge_asks_confirmation_and_does_not_merge(harness):
    jobs, store, runner, notifier = (
        harness["jobs"],
        harness["store"],
        harness["runner"],
        harness["notifier"],
    )
    job = await seed_job(jobs)
    store.prs[12] = open_pr()
    store.runs["copilot/fix-12"] = [
        {"id": 1, "status": "completed", "conclusion": "success"}
    ]

    await runner.request_merge(job.id)

    assert store.merge_calls == []
    assert len(notifier.confirmations) == 1
    assert "CI: PASS" in notifier.confirmations[0]["text"]
    fresh = await jobs.get(job.id)
    assert fresh.state == JobState.MERGE_CONFIRMATION_PENDING
    assert fresh.merge_head_sha == "abc123"


async def test_pr004_confirm_merge_sets_done(harness):
    jobs, store, runner, notifier = (
        harness["jobs"],
        harness["store"],
        harness["runner"],
        harness["notifier"],
    )
    job = await seed_job(
        jobs,
        state=JobState.MERGE_CONFIRMATION_PENDING,
        merge_head_sha="abc123",
        state_before_merge=JobState.WAIT_TESTS,
    )
    store.prs[12] = open_pr()
    store.runs["copilot/fix-12"] = [
        {"id": 1, "status": "completed", "conclusion": "success"}
    ]

    await runner.confirm_merge(job.id, True, operator_id=7)

    assert store.merge_calls == [{"repository": "acme/repo", "number": 12, "sha": "abc123"}]
    fresh = await jobs.get(job.id)
    assert fresh.state == JobState.DONE
    assert any("успешно объединён" in text for _, text in notifier.texts)


async def test_pr005_ci_failure_blocks_merge(harness):
    jobs, store, runner = harness["jobs"], harness["store"], harness["runner"]
    job = await seed_job(jobs)
    store.prs[12] = open_pr()
    store.runs["copilot/fix-12"] = [
        {"id": 1, "status": "completed", "conclusion": "failure"}
    ]

    await runner.request_merge(job.id)

    assert store.merge_calls == []
    assert any("CI завершился с ошибкой" in text for _, text in harness["notifier"].texts)
    fresh = await jobs.get(job.id)
    assert fresh.state != JobState.DONE


async def test_pr006_ci_running_blocks_merge(harness):
    jobs, store, runner = harness["jobs"], harness["store"], harness["runner"]
    job = await seed_job(jobs)
    store.prs[12] = open_pr()
    store.runs["copilot/fix-12"] = [
        {"id": 1, "status": "in_progress", "conclusion": None}
    ]

    await runner.request_merge(job.id)

    assert store.merge_calls == []
    assert any("ещё выполняются" in text for _, text in harness["notifier"].texts)


async def test_pr007_already_merged_is_idempotent(harness):
    jobs, store, runner = harness["jobs"], harness["store"], harness["runner"]
    job = await seed_job(jobs)
    store.prs[12] = open_pr(merged=True, state="closed")

    await runner.request_merge(job.id)

    assert store.merge_calls == []
    assert any("уже объединён" in text for _, text in harness["notifier"].texts)


async def test_pr008_no_pr_no_github_mutation(harness):
    jobs, store, runner, notifier = (
        harness["jobs"],
        harness["store"],
        harness["runner"],
        harness["notifier"],
    )
    job = await seed_job(jobs, pr_number=None, pr_url="")

    await runner.request_diff(job.id)
    await runner.request_merge(job.id)

    assert store.diff_calls == []
    assert store.merge_calls == []
    assert any("ещё не создан" in text for _, text in notifier.texts)


async def test_pr009_telegram_document_failure_does_not_change_job(harness):
    jobs, store, runner, notifier = (
        harness["jobs"],
        harness["store"],
        harness["runner"],
        harness["notifier"],
    )
    job = await seed_job(jobs)
    store.prs[12] = open_pr()
    store.diffs[12] = "diff --git a/x b/x\n"
    notifier.fail_document = True

    await runner.request_diff(job.id)

    fresh = await jobs.get(job.id)
    assert fresh.state == JobState.WAIT_TESTS
    assert store.prs[12].merged is False
    assert any("Не удалось отправить diff" in text for _, text in notifier.texts)


async def test_pr010_github_merge_failure_does_not_mark_done(harness):
    jobs, store, runner = harness["jobs"], harness["store"], harness["runner"]
    job = await seed_job(
        jobs,
        state=JobState.MERGE_CONFIRMATION_PENDING,
        merge_head_sha="abc123",
        state_before_merge=JobState.WAIT_TESTS,
    )
    store.prs[12] = open_pr()
    store.runs["copilot/fix-12"] = [
        {"id": 1, "status": "completed", "conclusion": "success"}
    ]
    store.fail_merge = "required status check is pending"

    await runner.confirm_merge(job.id, True, operator_id=7)

    fresh = await jobs.get(job.id)
    assert fresh.state != JobState.DONE
    assert any("Не удалось выполнить merge" in text for _, text in harness["notifier"].texts)
    assert any("required status check" in text for _, text in harness["notifier"].texts)


async def test_pr011_closed_pr_blocks_diff(harness):
    jobs, store, runner, notifier = (
        harness["jobs"],
        harness["store"],
        harness["runner"],
        harness["notifier"],
    )
    job = await seed_job(jobs)
    store.prs[12] = open_pr(merged=False, state="closed")
    store.diffs[12] = "should-not-send\n"

    await runner.request_diff(job.id)

    assert store.diff_calls == []
    assert store.merge_calls == []
    assert notifier.documents == []
    assert any("закрыт" in text and "недоступен" in text for _, text in notifier.texts)


async def test_pr012_empty_diff_is_not_sent_as_file(harness):
    jobs, store, runner, notifier = (
        harness["jobs"],
        harness["store"],
        harness["runner"],
        harness["notifier"],
    )
    job = await seed_job(jobs)
    store.prs[12] = open_pr()
    store.diffs[12] = "   \n"

    await runner.request_diff(job.id)

    assert store.diff_calls == [12]
    assert notifier.documents == []
    assert any("нет изменений" in text for _, text in notifier.texts)


async def test_pr013_oversized_diff_is_not_truncated(harness, settings):
    settings.telegram_max_document_bytes = 8
    jobs, store, runner, notifier = (
        harness["jobs"],
        harness["store"],
        harness["runner"],
        harness["notifier"],
    )
    job = await seed_job(jobs)
    store.prs[12] = open_pr()
    store.diffs[12] = "diff --git a/x b/x\n+too-big\n"

    await runner.request_diff(job.id)

    assert notifier.documents == []
    assert store.prs[12].merged is False
    text = next(t for _, t in notifier.texts if "превышает лимит" in t)
    assert "Полный diff: https://github.com/acme/repo/pull/12.diff" in text
    assert "PR: https://github.com/acme/repo/pull/12" in text


async def test_pr014_github_down_on_diff(harness):
    jobs, store, runner, notifier = (
        harness["jobs"],
        harness["store"],
        harness["runner"],
        harness["notifier"],
    )
    job = await seed_job(jobs)
    store.fail_get_pr = True

    await runner.request_diff(job.id)

    assert store.diff_calls == []
    assert store.merge_calls == []
    assert any("Не удалось получить diff" in text for _, text in notifier.texts)


async def test_pr015_copilot_waiting_blocks_merge(harness):
    jobs, store, runner = harness["jobs"], harness["store"], harness["runner"]
    job = await seed_job(jobs, awaiting_user_reply=True)
    store.prs[12] = open_pr()
    store.runs["copilot/fix-12"] = [
        {"id": 1, "status": "completed", "conclusion": "success"}
    ]

    await runner.request_merge(job.id)

    assert store.merge_calls == []
    assert any("ожидает ответа" in text for _, text in harness["notifier"].texts)


async def test_pr016_closed_unmerged_pr_blocks_merge(harness):
    jobs, store, runner = harness["jobs"], harness["store"], harness["runner"]
    job = await seed_job(jobs)
    store.prs[12] = open_pr(merged=False, state="closed")

    await runner.request_merge(job.id)

    assert store.merge_calls == []
    assert any("закрыт и не может быть объединён" in text for _, text in harness["notifier"].texts)


async def test_pr017_ci_flips_after_confirmation_screen(harness):
    jobs, store, runner = harness["jobs"], harness["store"], harness["runner"]
    job = await seed_job(jobs)
    store.prs[12] = open_pr()
    store.runs["copilot/fix-12"] = [
        {"id": 1, "status": "completed", "conclusion": "success"}
    ]

    await runner.request_merge(job.id)
    assert store.merge_calls == []

    store.runs["copilot/fix-12"] = [
        {"id": 2, "status": "completed", "conclusion": "failure"}
    ]
    await runner.confirm_merge(job.id, True, operator_id=7)

    assert store.merge_calls == []
    fresh = await jobs.get(job.id)
    assert fresh.state != JobState.DONE
    assert any("CI завершился с ошибкой" in text for _, text in harness["notifier"].texts)


async def test_pr018_second_merge_after_done_is_idempotent(harness):
    jobs, store, runner = harness["jobs"], harness["store"], harness["runner"]
    job = await seed_job(jobs, state=JobState.DONE)
    store.prs[12] = open_pr(merged=True, state="closed")

    await runner.request_merge(job.id)

    assert store.merge_calls == []
    assert any("уже объединён" in text for _, text in harness["notifier"].texts)


async def test_pr019_github_down_on_merge_check(harness):
    jobs, store, runner = harness["jobs"], harness["store"], harness["runner"]
    job = await seed_job(jobs)
    store.fail_get_pr = True

    await runner.request_merge(job.id)

    assert store.merge_calls == []
    assert any("Не удалось проверить состояние" in text for _, text in harness["notifier"].texts)


async def test_pr022_confirm_after_head_moved(harness):
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


async def test_pr025_unauthorized_confirm_does_not_merge(harness):
    jobs, store, runner, notifier = (
        harness["jobs"],
        harness["store"],
        harness["runner"],
        harness["notifier"],
    )
    job = await seed_job(jobs)
    store.prs[12] = open_pr()
    store.runs["copilot/fix-12"] = [
        {"id": 1, "status": "completed", "conclusion": "success"}
    ]
    await runner.request_merge(job.id)

    from domain.errors import UserFacingError

    try:
        await runner.confirm_merge(job.id, True, operator_id=99)
        raised = None
    except UserFacingError as exc:
        raised = exc

    assert raised is not None
    assert str(raised) == "Нет доступа."
    assert store.merge_calls == []
    fresh = await jobs.get(job.id)
    assert fresh.state == JobState.MERGE_CONFIRMATION_PENDING
    assert fresh.state != JobState.DONE


async def test_pr026_unauthorized_cancel_does_not_revert(harness):
    jobs, store, runner = harness["jobs"], harness["store"], harness["runner"]
    job = await seed_job(jobs)
    store.prs[12] = open_pr()
    store.runs["copilot/fix-12"] = [
        {"id": 1, "status": "completed", "conclusion": "success"}
    ]
    await runner.request_merge(job.id)

    from domain.errors import UserFacingError
    import pytest

    with pytest.raises(UserFacingError, match="Нет доступа"):
        await runner.confirm_merge(job.id, False, operator_id=99)

    fresh = await jobs.get(job.id)
    assert fresh.state == JobState.MERGE_CONFIRMATION_PENDING
    assert store.merge_calls == []


async def test_pr027_empty_allowlist_blocks_confirm(harness, settings):
    settings.telegram_allowed_user_ids = ""
    jobs, store, runner = harness["jobs"], harness["store"], harness["runner"]
    job = await seed_job(
        jobs,
        state=JobState.MERGE_CONFIRMATION_PENDING,
        merge_head_sha="abc123",
        state_before_merge=JobState.WAIT_TESTS,
    )
    store.prs[12] = open_pr()
    store.runs["copilot/fix-12"] = [
        {"id": 1, "status": "completed", "conclusion": "success"}
    ]

    from domain.errors import UserFacingError
    import pytest

    with pytest.raises(UserFacingError, match="Нет доступа"):
        await runner.confirm_merge(job.id, True, operator_id=7)

    assert store.merge_calls == []
    fresh = await jobs.get(job.id)
    assert fresh.state != JobState.DONE


async def test_unexpected_diff_error_is_not_masked_as_github_down(harness):
    jobs, store, runner, github, notifier = (
        harness["jobs"],
        harness["store"],
        harness["runner"],
        harness["github"],
        harness["notifier"],
    )
    job = await seed_job(jobs)
    store.prs[12] = open_pr()

    async def _boom(_number: int):
        raise AttributeError("broken mapper")

    github.get_pull_request = _boom  # type: ignore[method-assign]
    await runner.request_diff(job.id)
    assert any("Внутренняя ошибка" in text for _, text in notifier.texts)
    assert not any("Не удалось получить diff" in text for _, text in notifier.texts)


async def test_pr028_allowlisted_teammate_can_confirm(harness, settings):
    settings.telegram_allowed_user_ids = "7,8"
    jobs, store, runner, notifier = (
        harness["jobs"],
        harness["store"],
        harness["runner"],
        harness["notifier"],
    )
    job = await seed_job(jobs, user_id=7)
    store.prs[12] = open_pr()
    store.runs["copilot/fix-12"] = [
        {"id": 1, "status": "completed", "conclusion": "success"}
    ]
    await runner.request_merge(job.id)

    await runner.confirm_merge(job.id, True, operator_id=8)

    assert store.merge_calls == [
        {"repository": "acme/repo", "number": 12, "sha": "abc123"}
    ]
    fresh = await jobs.get(job.id)
    assert fresh.state == JobState.DONE
    assert any("успешно объединён" in text for _, text in notifier.texts)
