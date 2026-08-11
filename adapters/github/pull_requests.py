"""
GitHub Pull Request operations adapter.
"""
from __future__ import annotations

import logging
from typing import List
from adapters.github.client import GitHubHTTPClient
from adapters.github.models import GitHubPullRequest, GitHubComment

logger = logging.getLogger(__name__)


class GitHubPullRequestAdapter:
    """Adapter for GitHub Pull Request operations."""

    def __init__(self, client: GitHubHTTPClient) -> None:
        self.client = client

    async def get_pull_request(self, pr_number: int) -> GitHubPullRequest:
        """Read Pull Request status."""
        res = await self.client.get(f"/pulls/{pr_number}")
        return GitHubPullRequest.model_validate(res)

    async def add_pr_comment(self, pr_number: int, comment_body: str) -> GitHubComment:
        """Publish comment to Pull Request."""
        res = await self.client.post(f"/issues/{pr_number}/comments", {"body": comment_body})
        logger.info("Added comment to PR #%s", pr_number)
        return GitHubComment.model_validate(res)

    async def get_pr_comments(self, pr_number: int) -> List[GitHubComment]:
        """Read comments on Pull Request."""
        res = await self.client.get(f"/issues/{pr_number}/comments")
        return [GitHubComment.model_validate(c) for c in res]
