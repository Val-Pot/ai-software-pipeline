"""
GitHub Issue operations (Creation, Assigning Copilot Agent, Commenting).
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional

from adapters.github.client import GitHubClientError, GitHubHTTPClient
from adapters.github.models import GitHubComment, GitHubIssue

logger = logging.getLogger(__name__)

# Copilot comments as github-copilot[bot], but the Issues assignees API only
# accepts copilot-swe-agent[bot] (see GitHub "Assign Copilot via REST API").
_COPILOT_ASSIGNEE = "copilot-swe-agent[bot]"
_COPILOT_ASSIGNEE_ALIASES = {
    "github-copilot[bot]",
    "github-copilot",
    "copilot-swe-agent",
    "copilot-swe-agent[bot]",
    "copilot",
}


def resolve_copilot_assignee(username: str) -> str:
    """Map commenter / alias logins to the REST assignee identity."""
    if username.lower() in _COPILOT_ASSIGNEE_ALIASES:
        return _COPILOT_ASSIGNEE
    return username


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

    async def assign_copilot(
        self,
        issue_number: int,
        copilot_username: str = "github-copilot[bot]",
        default_branch: str = "main",
    ) -> bool:
        """Assign issue to GitHub Copilot coding agent and add trigger label."""
        assignee = resolve_copilot_assignee(copilot_username)
        base_branch = await self._ensure_base_branch(default_branch)
        payload: Dict[str, Any] = {
            "assignees": [assignee],
            "agent_assignment": {
                "target_repo": f"{self.client.owner}/{self.client.repo}",
                "base_branch": base_branch,
            },
        }
        logger.info(
            "Assigning issue #%s to Copilot via /assignees (requested=%s assignee=%s branch=%s)",
            issue_number,
            copilot_username,
            assignee,
            base_branch,
        )
        await self.client.post(f"/issues/{issue_number}/assignees", payload)
        await self.client.post(f"/issues/{issue_number}/labels", {"labels": ["copilot-agent"]})
        logger.info("Assigned issue #%s to Copilot agent %s", issue_number, assignee)
        return True

    async def _ensure_base_branch(self, fallback: str) -> str:
        """Resolve the repo default branch; bootstrap an empty repo so Copilot can start."""
        repo = await self.client.get("")
        if not isinstance(repo, dict):
            return fallback
        default_branch = str(repo.get("default_branch") or fallback)

        try:
            await self.client.get(f"/git/ref/heads/{default_branch}")
            return default_branch
        except GitHubClientError as exc:
            detail = str(exc).lower()
            if "409" not in detail and "404" not in detail and "empty" not in detail:
                raise
            logger.warning(
                "Repository %s/%s has no commits on %s — creating bootstrap README so Copilot can start.",
                self.client.owner,
                self.client.repo,
                default_branch,
            )
            await self._bootstrap_empty_repo(default_branch)
            return default_branch

    async def _bootstrap_empty_repo(self, branch: str) -> None:
        readme = (
            f"# {self.client.repo}\n\n"
            "Initial commit so GitHub Copilot coding agent has a base branch to work from.\n"
        )
        await self.client.put(
            "/contents/README.md",
            {
                "message": "chore: bootstrap empty repository for Copilot coding agent",
                "content": base64.b64encode(readme.encode("utf-8")).decode("ascii"),
                "branch": branch,
            },
        )

    async def add_comment(self, issue_number: int, comment_body: str) -> GitHubComment:
        """Publish comment to an issue."""
        res = await self.client.post(f"/issues/{issue_number}/comments", {"body": comment_body})
        logger.info("Added comment to issue #%s", issue_number)
        return GitHubComment.model_validate(res)

    async def get_comments(self, issue_number: int) -> List[GitHubComment]:
        """Fetch comments for an issue."""
        res = await self.client.get(f"/issues/{issue_number}/comments")
        return [GitHubComment.model_validate(c) for c in res]
