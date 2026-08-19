from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from adapters.github.models import GitHubPullRequest, GitHubUser
from adapters.jobs.memory import InMemoryJobRepository
from config.settings import Settings
from domain.errors import GitHubUnavailableError, MergeError
from domain.models import Job, JobState
from domain.task_contract import format_template
from orchestrator.pipeline_runner import PipelineRunner


def complete_contract(**overrides) -> str:
    sections = {
        "Goal": "Improve the map.",
        "Scope": "Map screen only.",
        "Expected Behavior": "User sees tiles.",
        "Architecture Constraints": "N/A",
        "Acceptance Criteria": "Tiles render.",
        "Verification": "Open the map screen.",
    }
    sections.update(overrides)
    return format_template(sections)


class FakeNotifier:
    def __init__(self) -> None:
        self.texts: list[tuple[int, str]] = []
        self.documents: list[dict] = []
        self.confirmations: list[dict] = []
        self.fail_document = False

    async def send_text(self, chat_id: int, text: str) -> None:
        self.texts.append((chat_id, text))

    async def send_document(
        self, chat_id: int, filename: str, content: bytes, caption: str = ""
    ) -> None:
        if self.fail_document:
            raise RuntimeError("telegram send_document failed")
        self.documents.append(
            {
                "chat_id": chat_id,
                "filename": filename,
                "content": content,
                "caption": caption,
            }
        )

    async def send_merge_confirmation(
        self, chat_id: int, job_id: str, text: str
    ) -> None:
        self.confirmations.append({"chat_id": chat_id, "job_id": job_id, "text": text})


@dataclass
class FakePRStore:
    prs: dict[int, GitHubPullRequest] = field(default_factory=dict)
    diffs: dict[int, str] = field(default_factory=dict)
    runs: dict[str, list[dict]] = field(default_factory=dict)
    issues: dict[int, dict] = field(default_factory=dict)
    merge_calls: list[dict] = field(default_factory=list)
    diff_calls: list[int] = field(default_factory=list)
    fail_diff = False
    fail_get_pr = False
    fail_get_issue = False
    fail_merge: str | None = None


class FakeGitHub:
    def __init__(self, store: FakePRStore) -> None:
        self.store = store
        self.actions = self
        self.review_comments: list[str] = []
        self.created_issues: list[dict] = []

    async def create_issue(self, title: str, body: str) -> dict:
        number = len(self.created_issues) + 1
        issue = {
            "number": number,
            "html_url": f"https://github.com/acme/repo/issues/{number}",
            "title": title,
            "body": body,
        }
        self.created_issues.append(issue)
        self.store.issues[number] = issue
        return issue

    async def get_issue(self, issue_number: int) -> dict:
        if self.store.fail_get_issue:
            raise GitHubUnavailableError("github unavailable")
        if issue_number in self.store.issues:
            return self.store.issues[issue_number]
        for issue in self.created_issues:
            if issue["number"] == issue_number:
                return issue
        return {"number": issue_number, "body": ""}

    async def get_pull_request(self, pull_request_number: int) -> GitHubPullRequest:
        if self.store.fail_get_pr:
            raise GitHubUnavailableError("github unavailable")
        return self.store.prs[pull_request_number]

    async def get_pull_request_diff(
        self, repository: str, pull_request_number: int
    ) -> str:
        self.store.diff_calls.append(pull_request_number)
        if self.store.fail_diff:
            raise GitHubUnavailableError("github unavailable")
        return self.store.diffs.get(pull_request_number, "")

    async def merge_pull_request(
        self, repository: str, pull_request_number: int, *, sha: str | None = None
    ) -> dict:
        if self.store.fail_merge:
            raise MergeError(self.store.fail_merge)
        self.store.merge_calls.append(
            {"repository": repository, "number": pull_request_number, "sha": sha}
        )
        pr = self.store.prs[pull_request_number]
        pr.merged = True
        pr.state = "closed"
        return {"merged": True}

    async def run_ai_review(self, pull_request_number: int) -> None:
        self.review_comments.append(
            "Pipeline check: CI passed, no automated review configured."
        )

    async def list_runs_for_branch(self, branch: str, per_page: int = 10) -> list[dict]:
        return list(self.store.runs.get(branch, []))

    async def get_latest_run_for_branch(self, branch: str):
        runs = self.store.runs.get(branch, [])
        for run in runs:
            if (run.get("status") or "").lower() == "completed":
                return run
        return None


class FakeCodingAgent:
    def __init__(self) -> None:
        self.fix_calls: list[tuple[int, str]] = []
        self.trigger_calls: list[int] = []

    async def trigger(self, issue_number: int) -> None:
        self.trigger_calls.append(issue_number)

    async def trigger_fix_iteration(self, issue_number: int, error_log: str) -> None:
        self.fix_calls.append((issue_number, error_log))

    async def watch_issue(self, issue_number: int):
        if False:
            yield None

    async def detect_task_completion(
        self, issue_number: int, pr_number: int | None
    ) -> bool:
        return False

    def parse_webhook_event(self, event_name: str, payload: dict):
        return None


def open_pr(number: int = 12, **overrides) -> GitHubPullRequest:
    data = dict(
        number=number,
        html_url=f"https://github.com/acme/repo/pull/{number}",
        state="open",
        draft=False,
        requested_reviewers=[GitHubUser(login="owner")],
        merged=False,
        mergeable=True,
        mergeable_state="clean",
        head_sha="abc123",
        head_ref="copilot/fix-12",
    )
    data.update(overrides)
    return GitHubPullRequest(**data)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        github_owner="acme",
        github_repo="repo",
        telegram_allowed_user_ids="7",
        telegram_max_document_bytes=50 * 1024 * 1024,
        coding_agent_stale_timeout_sec=1800,
    )


@pytest.fixture
def harness(settings):
    jobs = InMemoryJobRepository()
    store = FakePRStore()
    github = FakeGitHub(store)
    coding_agent = FakeCodingAgent()
    notifier = FakeNotifier()
    runner = PipelineRunner(
        settings=settings,
        jobs=jobs,
        github=github,
        coding_agent=coding_agent,
        notifier=notifier,
    )
    return {
        "jobs": jobs,
        "store": store,
        "github": github,
        "coding_agent": coding_agent,
        "notifier": notifier,
        "runner": runner,
        "settings": settings,
    }


async def seed_job(jobs, **overrides) -> Job:
    data = dict(
        chat_id=100,
        user_id=7,
        repository="acme/repo",
        title="task",
        body="do the thing",
        state=JobState.WAIT_TESTS,
        issue_number=3,
        issue_url="https://github.com/acme/repo/issues/3",
        pr_number=12,
        pr_url="https://github.com/acme/repo/pull/12",
    )
    data.update(overrides)
    job = Job(**data)
    await jobs.save(job)
    return job
