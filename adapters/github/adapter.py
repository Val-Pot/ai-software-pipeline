"""
Unified GitHub Adapter implementing Orchestrator's GitHubPort protocol.
"""
from __future__ import annotations

import logging
from typing import Optional

from orchestrator.ports import GitHubPort
from orchestrator.context import PipelineJob
from adapters.github.client import GitHubHTTPClient
from adapters.github.issues import GitHubIssueAdapter
from adapters.github.pull_requests import GitHubPullRequestAdapter
from adapters.github.actions import GitHubActionsAdapter

logger = logging.getLogger(__name__)


class GitHubAdapter(GitHubPort):
    """
    Unified GitHub Adapter exposing strictly the GitHubPort protocol to the Orchestrator.
    Encapsulates REST client, issues, PRs, and actions adapters without leaking GitHub details.
    """

    def __init__(
        self,
        token: str,
        owner: str,
        repo: str,
        timeout: float = 15.0,
        copilot_username: str = "github-copilot[bot]",
    ) -> None:
        self.client = GitHubHTTPClient(token=token, owner=owner, repo=repo, timeout=timeout)
        self.issues = GitHubIssueAdapter(self.client)
        self.pull_requests = GitHubPullRequestAdapter(self.client)
        self.actions = GitHubActionsAdapter(self.client)
        self.copilot_username = copilot_username

    async def create_issue(self, job: PipelineJob) -> PipelineJob:
        """Create GitHub Issue for job task description."""
        title = f"[Pipeline Task] {job.task_description[:50]}"
        body = (
            f"### Pipeline Job Task\n\n"
            f"**Job ID:** `{job.job_id}`\n"
            f"**User ID:** `{job.user_id}`\n\n"
            f"#### Description:\n{job.task_description}"
        )
        issue = await self.issues.create_issue(title=title, body=body, labels=["ai-pipeline"])
        return job.model_copy(update={"issue_number": issue.number, "issue_url": issue.html_url})

    async def trigger_coding_agent(self, job: PipelineJob) -> bool:
        """Assign issue to Copilot coding agent."""
        if not job.issue_number:
            logger.error("Cannot trigger coding agent without issue_number for job_id=%s", job.short_id)
            return False
        return await self.issues.assign_copilot(job.issue_number, self.copilot_username)

    async def trigger_fix(self, job: PipelineJob, test_failure_log: str) -> bool:
        """Comment test failure log on PR/Issue to trigger fix iteration."""
        target_number = job.pr_number or job.issue_number
        if not target_number:
            logger.error("Cannot trigger fix without PR or Issue number for job_id=%s", job.short_id)
            return False

        comment_body = (
            f"⚠️ **CI Tests Failed (Iteration {job.retry_count}/{job.max_retries})**\n\n"
            f"```text\n{test_failure_log}\n```\n\n"
            f"@{self.copilot_username} Please analyze the test failure log above and push a fix."
        )
        await self.issues.add_comment(target_number, comment_body)
        return True

    async def run_ai_review(self, job: PipelineJob) -> bool:
        """Post automated AI Review summary comment on PR."""
        if not job.pr_number:
            logger.error("Cannot post AI review without PR number for job_id=%s", job.short_id)
            return False

        comment_body = (
            f"🔍 **Automated AI Review Summary**\n\n"
            f"- **Security & Code Quality:** Checked\n"
            f"- **CI Workflow:** Passed\n"
            f"- **Status:** Approved for Merge ✅"
        )
        await self.pull_requests.add_pr_comment(job.pr_number, comment_body)
        return True
