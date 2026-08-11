"""
GitHub Issue operations (Creation, Assigning Copilot Agent, Commenting).
"""
from __future__ import annotations

import logging
from typing import List, Dict, Any, Optional
from adapters.github.client import GitHubHTTPClient
from adapters.github.models import GitHubIssue, GitHubComment

logger = logging.getLogger(__name__)


class GitHubIssueAdapter:
    """Adapter for GitHub Issue operations."""

    def __init__(self, client: GitHubHTTPClient) -> None:
        self.client = client

    async def create_issue(self, title: str, body: str, labels: Optional[List[str]] = None) -> GitHubIssue:
        """Create new GitHub issue."""
        payload: Dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        res = await self.client.post("/issues", payload)
        logger.info("Created GitHub issue #%s", res["number"])
        return GitHubIssue.model_validate(res)

    async def assign_copilot(self, issue_number: int, copilot_username: str = "github-copilot[bot]") -> bool:
        """Assign issue to GitHub Copilot coding agent and add trigger label."""
        await self.client.patch(f"/issues/{issue_number}", {"assignees": [copilot_username]})
        await self.client.post(f"/issues/{issue_number}/labels", {"labels": ["copilot-agent"]})
        logger.info("Assigned issue #%s to Copilot agent", issue_number)
        return True

    async def add_comment(self, issue_number: int, comment_body: str) -> GitHubComment:
        """Publish comment to an issue."""
        res = await self.client.post(f"/issues/{issue_number}/comments", {"body": comment_body})
        logger.info("Added comment to issue #%s", issue_number)
        return GitHubComment.model_validate(res)

    async def get_comments(self, issue_number: int) -> List[GitHubComment]:
        """Fetch comments for an issue."""
        res = await self.client.get(f"/issues/{issue_number}/comments")
        return [GitHubComment.model_validate(c) for c in res]
