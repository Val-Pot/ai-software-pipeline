from __future__ import annotations

from adapters.github.actions import ActionsClient
from adapters.github.comments import CommentsClient
from adapters.github.graphql import GitHubGraphQL
from adapters.github.http import GitHubHttp
from adapters.github.issues import IssuesClient
from adapters.github.models import GitHubPullRequest
from adapters.github.pull_requests import PullRequestsClient
from config.settings import Settings


class GitHubAdapter:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._http = GitHubHttp(settings.github_token)
        self._graphql = GitHubGraphQL(settings.github_token)
        self.issues = IssuesClient(
            self._http,
            settings.github_owner,
            settings.github_repo,
            self._graphql,
            copilot_username=settings.copilot_username,
        )
        self.comments = CommentsClient(
            self._http, settings.github_owner, settings.github_repo
        )
        self.pull_requests = PullRequestsClient(
            self._http, settings.github_owner, settings.github_repo
        )
        self.actions = ActionsClient(
            self._http, settings.github_owner, settings.github_repo
        )

    async def create_issue(self, title: str, body: str) -> dict:
        return await self.issues.create_issue(title, body)

    async def get_issue(self, issue_number: int) -> dict:
        return await self.issues.get_issue(issue_number)

    async def get_pull_request(self, pull_request_number: int) -> GitHubPullRequest:
        return await self.pull_requests.get_pull_request(pull_request_number)

    # trigger_fix удалён (BUG-002 / BUG-003).
    # Единственный путь: coding_agent.trigger_fix_iteration
    # с текстом "@copilot Fix the failing tests".

    async def run_ai_review(self, pull_request_number: int) -> None:
        await self.comments.create_pr_comment(
            pull_request_number,
            "Pipeline check: CI passed, no automated review configured.",
        )

    async def get_pull_request_diff(
        self, repository: str, pull_request_number: int
    ) -> str:
        return await self.pull_requests.get_pull_request_diff(pull_request_number)

    async def merge_pull_request(
        self, repository: str, pull_request_number: int, *, sha: str | None = None
    ) -> dict:
        return await self.pull_requests.merge_pull_request(pull_request_number, sha=sha)

    async def aclose(self) -> None:
        await self._http.aclose()
        await self._graphql.aclose()
