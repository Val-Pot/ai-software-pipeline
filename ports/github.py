from typing import Any, Protocol

from adapters.github.models import GitHubPullRequest


class GitHubPort(Protocol):
    async def create_issue(self, title: str, body: str) -> dict[str, Any]: ...

    async def get_issue(self, issue_number: int) -> dict[str, Any]: ...

    async def get_pull_request(self, pull_request_number: int) -> GitHubPullRequest: ...

    async def get_pull_request_diff(
        self, repository: str, pull_request_number: int
    ) -> str: ...

    async def merge_pull_request(
        self, repository: str, pull_request_number: int, *, sha: str | None = None
    ) -> dict: ...

    async def run_ai_review(self, pull_request_number: int) -> None: ...
