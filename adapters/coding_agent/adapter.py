"""
GitHub Copilot Coding Agent Adapter.

Responsibilities (adapter layer only — no business logic):
  1. Assign a GitHub Issue to the GitHub Copilot Coding Agent.
  2. Detect when the Coding Agent starts processing (first bot comment).
  3. Detect clarifying questions from the Coding Agent.
  4. Post user replies back to the GitHub Issue.
  5. Detect Pull Request creation by the agent.
  6. Detect task completion (PR merge or issue closure).
  7. Trigger automatic fix iteration by commenting
     "@copilot fix the failing tests" when CI reports failure.
  8. Return structured CodingAgentEvent objects to the Orchestrator.

Design:
  - Pure adapter — receives raw GitHub API data, emits typed events.
  - Async / Python 3.12.
  - Retry + timeout handling via tenacity.
  - Configuration injected via constructor (no global state).
  - All mutations are single-responsibility methods.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import AsyncIterator, List, Optional

import httpx
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from adapters.coding_agent.models import (
    AgentEventType,
    AgentStatus,
    CodingAgentEvent,
)
from adapters.github.client import GitHubHTTPClient, GitHubClientError
from adapters.github.issues import GitHubIssueAdapter
from adapters.github.models import GitHubComment
from adapters.github.pull_requests import GitHubPullRequestAdapter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentinel text injected into fix-iteration comments.
# ---------------------------------------------------------------------------
_FIX_TRIGGER_TEXT = "@copilot fix the failing tests"

# Patterns that indicate the agent is asking a question.
_QUESTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\?", re.IGNORECASE),
    re.compile(r"please\s+clarify", re.IGNORECASE),
    re.compile(r"could\s+you\s+(?:please\s+)?(?:clarify|confirm|explain)", re.IGNORECASE),
    re.compile(r"can\s+you\s+(?:please\s+)?(?:clarify|confirm|explain)", re.IGNORECASE),
    re.compile(r"i\s+need\s+(?:more\s+)?(?:information|clarification|details)", re.IGNORECASE),
]

# Patterns that indicate the agent has finished or is handing off.
_COMPLETION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"pull\s+request\s+(?:has\s+been\s+)?(?:created|opened|submitted)", re.IGNORECASE),
    re.compile(r"i've\s+(?:created|opened|submitted)\s+(?:a\s+)?(?:pull\s+request|pr)", re.IGNORECASE),
    re.compile(r"(?:task|work)\s+(?:is\s+)?(?:complete|completed|done|finished)", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Helper — retry policy factory
# ---------------------------------------------------------------------------

def _make_retry_policy(max_attempts: int, min_wait: float, max_wait: float) -> AsyncRetrying:
    """Return a tenacity AsyncRetrying context configured for GitHub API calls."""
    return AsyncRetrying(
        retry=retry_if_exception_type((GitHubClientError, httpx.RequestError)),
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
        reraise=True,
    )


# ---------------------------------------------------------------------------
# Main Adapter
# ---------------------------------------------------------------------------


class CodingAgentAdapter:
    """
    Adapter that bridges the GitHub Copilot Coding Agent and the Orchestrator.

    All public methods are async and return structured ``CodingAgentEvent``
    objects. No business logic is performed here.

    Parameters
    ----------
    client:
        Configured ``GitHubHTTPClient`` providing authenticated REST access.
    copilot_username:
        GitHub login of the coding agent bot (e.g. ``github-copilot[bot]``).
    max_retries:
        Maximum number of retries for GitHub API calls.
    poll_interval:
        Seconds between polling iterations when watching for events.
    request_timeout:
        Per-request timeout in seconds.
    """

    def __init__(
        self,
        client: GitHubHTTPClient,
        copilot_username: str = "github-copilot[bot]",
        max_retries: int = 3,
        poll_interval: float = 10.0,
        request_timeout: float = 15.0,
    ) -> None:
        self._client = client
        self._copilot_username = copilot_username
        self._max_retries = max_retries
        self._poll_interval = poll_interval
        self._request_timeout = request_timeout

        self._issues = GitHubIssueAdapter(client)
        self._pull_requests = GitHubPullRequestAdapter(client)

        logger.debug(
            "CodingAgentAdapter initialised: agent=%s max_retries=%d poll_interval=%.1fs",
            copilot_username,
            max_retries,
            poll_interval,
        )

    # ------------------------------------------------------------------
    # 1. Assign issue to GitHub Copilot Coding Agent
    # ------------------------------------------------------------------

    async def assign_to_copilot(
        self,
        issue_number: int,
        job_id: Optional[str] = None,
    ) -> CodingAgentEvent:
        """
        Assign ``issue_number`` to the Copilot coding agent and apply the
        ``copilot-agent`` trigger label.

        Returns
        -------
        CodingAgentEvent
            An ``AGENT_ASSIGNED`` event on success or ``ADAPTER_ERROR`` on failure.
        """
        logger.info(
            "Assigning issue #%d to %s (job_id=%s)",
            issue_number,
            self._copilot_username,
            job_id,
        )
        try:
            async for attempt in _make_retry_policy(self._max_retries, 1, 10):
                with attempt:
                    await self._issues.assign_copilot(issue_number, self._copilot_username)

            logger.info("Issue #%d successfully assigned to Copilot agent.", issue_number)
            return CodingAgentEvent(
                event_type=AgentEventType.AGENT_ASSIGNED,
                job_id=job_id,
                issue_number=issue_number,
                agent_username=self._copilot_username,
                message=(
                    f"Issue #{issue_number} assigned to {self._copilot_username}. "
                    "Copilot coding agent will begin processing shortly."
                ),
            )

        except (GitHubClientError, RetryError) as exc:
            logger.error(
                "Failed to assign issue #%d to Copilot after %d retries: %s",
                issue_number,
                self._max_retries,
                exc,
            )
            return CodingAgentEvent(
                event_type=AgentEventType.ADAPTER_ERROR,
                job_id=job_id,
                issue_number=issue_number,
                agent_username=self._copilot_username,
                message=f"Failed to assign issue #{issue_number} to Copilot: {exc}",
            )

    # ------------------------------------------------------------------
    # 2. Detect when the Coding Agent starts processing
    # ------------------------------------------------------------------

    async def detect_agent_start(
        self,
        issue_number: int,
        job_id: Optional[str] = None,
        since_comment_id: Optional[int] = None,
    ) -> Optional[CodingAgentEvent]:
        """
        Poll issue comments and return an ``AGENT_STARTED`` event when the
        first comment from the Copilot bot is detected.

        Parameters
        ----------
        issue_number:
            GitHub issue to monitor.
        job_id:
            Pipeline job identifier for event correlation.
        since_comment_id:
            If provided, only examine comments created after this comment ID.

        Returns
        -------
        CodingAgentEvent or None
            ``AGENT_STARTED`` event if a Copilot comment was found; ``None``
            otherwise.
        """
        logger.debug("Checking for agent-start signal on issue #%d", issue_number)
        comments = await self._fetch_comments(issue_number)

        for comment in comments:
            if since_comment_id and comment.id <= since_comment_id:
                continue
            if self._is_copilot_comment(comment):
                logger.info(
                    "Copilot agent start detected on issue #%d via comment_id=%d",
                    issue_number,
                    comment.id,
                )
                return CodingAgentEvent(
                    event_type=AgentEventType.AGENT_STARTED,
                    job_id=job_id,
                    issue_number=issue_number,
                    comment_id=comment.id,
                    agent_username=self._copilot_username,
                    message=(
                        f"Copilot agent started processing issue #{issue_number}. "
                        f"First agent comment: {comment.body[:120]!r}"
                    ),
                )
        return None

    # ------------------------------------------------------------------
    # 3. Detect questions from the Coding Agent
    # ------------------------------------------------------------------

    async def detect_copilot_question(
        self,
        issue_number: int,
        job_id: Optional[str] = None,
        since_comment_id: Optional[int] = None,
    ) -> Optional[CodingAgentEvent]:
        """
        Scan issue comments for an unanswered clarifying question posted by
        the Copilot agent.

        Parameters
        ----------
        issue_number:
            GitHub issue to scan.
        job_id:
            Pipeline job identifier.
        since_comment_id:
            Only consider comments newer than this ID.

        Returns
        -------
        CodingAgentEvent or None
            ``COPILOT_QUESTION`` event if a question is detected; ``None``
            otherwise.
        """
        logger.debug("Scanning issue #%d for Copilot questions", issue_number)
        comments = await self._fetch_comments(issue_number)

        for comment in reversed(comments):  # latest first
            if since_comment_id and comment.id <= since_comment_id:
                continue
            if not self._is_copilot_comment(comment):
                continue
            if self._comment_contains_question(comment.body):
                logger.info(
                    "Copilot question detected on issue #%d via comment_id=%d",
                    issue_number,
                    comment.id,
                )
                return CodingAgentEvent(
                    event_type=AgentEventType.COPILOT_QUESTION,
                    job_id=job_id,
                    issue_number=issue_number,
                    comment_id=comment.id,
                    question=comment.body,
                    agent_username=self._copilot_username,
                    message=f"Copilot is asking a question on issue #{issue_number}.",
                )
        return None

    # ------------------------------------------------------------------
    # 4. Send user reply back to the GitHub Issue
    # ------------------------------------------------------------------

    async def send_user_reply(
        self,
        issue_number: int,
        reply_text: str,
        job_id: Optional[str] = None,
    ) -> CodingAgentEvent:
        """
        Post a user reply as a comment on the GitHub Issue so the Copilot
        agent can continue its work.

        Parameters
        ----------
        issue_number:
            Target issue number.
        reply_text:
            Plain text reply from the user.
        job_id:
            Pipeline job identifier.

        Returns
        -------
        CodingAgentEvent
            Always an ``ADAPTER_ERROR`` on unrecoverable failure; the comment
            ID is embedded in the success event message for traceability.
        """
        logger.info(
            "Posting user reply to issue #%d (job_id=%s, length=%d chars)",
            issue_number,
            job_id,
            len(reply_text),
        )
        try:
            async for attempt in _make_retry_policy(self._max_retries, 1, 10):
                with attempt:
                    comment = await self._issues.add_comment(issue_number, reply_text)

            logger.info(
                "User reply posted to issue #%d as comment_id=%d",
                issue_number,
                comment.id,
            )
            return CodingAgentEvent(
                event_type=AgentEventType.COPILOT_QUESTION,  # re-use to signal conversation continues
                job_id=job_id,
                issue_number=issue_number,
                comment_id=comment.id,
                agent_username=self._copilot_username,
                message=f"User reply posted to issue #{issue_number} (comment_id={comment.id}).",
            )

        except (GitHubClientError, RetryError) as exc:
            logger.error(
                "Failed to post user reply to issue #%d: %s",
                issue_number,
                exc,
            )
            return CodingAgentEvent(
                event_type=AgentEventType.ADAPTER_ERROR,
                job_id=job_id,
                issue_number=issue_number,
                agent_username=self._copilot_username,
                message=f"Failed to post user reply to issue #{issue_number}: {exc}",
            )

    # ------------------------------------------------------------------
    # 5. Detect Pull Request creation
    # ------------------------------------------------------------------

    async def detect_pull_request(
        self,
        issue_number: int,
        job_id: Optional[str] = None,
    ) -> Optional[CodingAgentEvent]:
        """
        Detect whether the Copilot agent has created a Pull Request linked to
        the tracked issue.

        Strategy: Look for a comment on the issue whose body contains a
        GitHub PR URL authored by the Copilot bot, and also check the
        repo's open PRs for one referencing the issue.

        Parameters
        ----------
        issue_number:
            Tracked issue number.
        job_id:
            Pipeline job identifier.

        Returns
        -------
        CodingAgentEvent or None
            ``PR_CREATED`` event when a PR is found; ``None`` otherwise.
        """
        logger.debug("Checking for PR creation linked to issue #%d", issue_number)

        # Strategy 1: Scan issue comments for a PR link posted by the bot.
        pr_event = await self._detect_pr_from_comments(issue_number, job_id)
        if pr_event:
            return pr_event

        # Strategy 2: List open PRs and match by title/body pattern referencing the issue.
        pr_event = await self._detect_pr_from_pr_list(issue_number, job_id)
        return pr_event

    # ------------------------------------------------------------------
    # 6. Detect task completion
    # ------------------------------------------------------------------

    async def detect_task_completion(
        self,
        issue_number: int,
        job_id: Optional[str] = None,
    ) -> Optional[CodingAgentEvent]:
        """
        Detect task completion signals — either a completion comment from the
        agent or a closed issue state.

        Parameters
        ----------
        issue_number:
            Tracked issue number.
        job_id:
            Pipeline job identifier.

        Returns
        -------
        CodingAgentEvent or None
            ``AGENT_COMPLETED`` event if completion is confirmed; ``None``
            otherwise.
        """
        logger.debug("Checking for task completion on issue #%d", issue_number)

        # Check issue state via REST API.
        try:
            async for attempt in _make_retry_policy(self._max_retries, 1, 10):
                with attempt:
                    issue_data: dict = await self._client.get(f"/issues/{issue_number}")  # type: ignore[assignment]

            if issue_data.get("state") == "closed":
                logger.info("Issue #%d is closed — task completion detected.", issue_number)
                return CodingAgentEvent(
                    event_type=AgentEventType.AGENT_COMPLETED,
                    job_id=job_id,
                    issue_number=issue_number,
                    agent_username=self._copilot_username,
                    message=f"Issue #{issue_number} has been closed. Task is complete.",
                )
        except (GitHubClientError, RetryError) as exc:
            logger.warning("Could not fetch issue #%d state: %s", issue_number, exc)

        # Fallback: scan comments for completion language.
        comments = await self._fetch_comments(issue_number)
        for comment in reversed(comments):
            if not self._is_copilot_comment(comment):
                continue
            if self._comment_indicates_completion(comment.body):
                logger.info(
                    "Task completion detected from agent comment_id=%d on issue #%d",
                    comment.id,
                    issue_number,
                )
                return CodingAgentEvent(
                    event_type=AgentEventType.AGENT_COMPLETED,
                    job_id=job_id,
                    issue_number=issue_number,
                    comment_id=comment.id,
                    agent_username=self._copilot_username,
                    message=(
                        f"Copilot agent signalled task completion on issue #{issue_number}."
                    ),
                )
        return None

    # ------------------------------------------------------------------
    # 7. Trigger automatic fix iteration
    # ------------------------------------------------------------------

    async def trigger_fix_iteration(
        self,
        issue_number: int,
        test_failure_log: str,
        retry_count: int = 0,
        max_retries: int = 3,
        job_id: Optional[str] = None,
    ) -> CodingAgentEvent:
        """
        Publish a fix-trigger comment on the issue/PR containing the failure log
        and the ``@copilot fix the failing tests`` instruction.

        Parameters
        ----------
        issue_number:
            Issue (or PR — GitHub treats PR comments the same as issue comments)
            to comment on.
        test_failure_log:
            Raw CI failure output to embed in the comment body.
        retry_count:
            Current fix iteration count (for display only).
        max_retries:
            Maximum allowed retries (for display only).
        job_id:
            Pipeline job identifier.

        Returns
        -------
        CodingAgentEvent
            ``FIX_REQUESTED`` on success; ``ADAPTER_ERROR`` on unrecoverable
            failure.
        """
        comment_body = (
            f"⚠️ **CI Tests Failed — Fix Iteration {retry_count}/{max_retries}**\n\n"
            f"```text\n{test_failure_log}\n```\n\n"
            f"{_FIX_TRIGGER_TEXT}"
        )

        logger.info(
            "Publishing fix-iteration comment on issue #%d (attempt %d/%d, job_id=%s)",
            issue_number,
            retry_count,
            max_retries,
            job_id,
        )

        try:
            async for attempt in _make_retry_policy(self._max_retries, 1, 10):
                with attempt:
                    comment = await self._issues.add_comment(issue_number, comment_body)

            logger.info(
                "Fix-iteration comment posted on issue #%d as comment_id=%d",
                issue_number,
                comment.id,
            )
            return CodingAgentEvent(
                event_type=AgentEventType.FIX_REQUESTED,
                job_id=job_id,
                issue_number=issue_number,
                comment_id=comment.id,
                agent_username=self._copilot_username,
                message=(
                    f"Fix iteration triggered on issue #{issue_number} "
                    f"(attempt {retry_count}/{max_retries})."
                ),
            )

        except (GitHubClientError, RetryError) as exc:
            logger.error(
                "Failed to post fix-iteration comment on issue #%d: %s",
                issue_number,
                exc,
            )
            return CodingAgentEvent(
                event_type=AgentEventType.ADAPTER_ERROR,
                job_id=job_id,
                issue_number=issue_number,
                agent_username=self._copilot_username,
                message=f"Failed to post fix-iteration comment on issue #{issue_number}: {exc}",
            )

    # ------------------------------------------------------------------
    # 8. Polling loop — yields structured events to the Orchestrator
    # ------------------------------------------------------------------

    async def watch_issue(
        self,
        issue_number: int,
        job_id: Optional[str] = None,
        timeout: float = 3600.0,
    ) -> AsyncIterator[CodingAgentEvent]:
        """
        Continuously poll the issue until a terminal event is detected or
        ``timeout`` is exceeded.

        Emits ``CodingAgentEvent`` objects in real time as each lifecycle
        milestone is detected. Terminal events: ``AGENT_COMPLETED`` or
        ``ADAPTER_ERROR`` after retries exhausted.

        Parameters
        ----------
        issue_number:
            Issue to monitor.
        job_id:
            Pipeline job identifier for event correlation.
        timeout:
            Maximum seconds to poll before giving up (default: 1 hour).

        Yields
        ------
        CodingAgentEvent
            One event per detected lifecycle change.
        """
        logger.info(
            "Starting issue watcher for #%d (job_id=%s, timeout=%.0fs)",
            issue_number,
            job_id,
            timeout,
        )
        status = AgentStatus.ASSIGNED
        last_comment_id: Optional[int] = None
        deadline = asyncio.get_event_loop().time() + timeout

        while asyncio.get_event_loop().time() < deadline:
            try:
                # --- Detect agent start ---
                if status == AgentStatus.ASSIGNED:
                    start_event = await self.detect_agent_start(
                        issue_number, job_id, last_comment_id
                    )
                    if start_event:
                        last_comment_id = start_event.comment_id
                        status = AgentStatus.RUNNING
                        yield start_event

                # --- Detect questions ---
                if status in (AgentStatus.RUNNING, AgentStatus.WAITING_REPLY):
                    q_event = await self.detect_copilot_question(
                        issue_number, job_id, last_comment_id
                    )
                    if q_event:
                        last_comment_id = q_event.comment_id
                        status = AgentStatus.WAITING_REPLY
                        yield q_event

                # --- Detect PR creation ---
                if status == AgentStatus.RUNNING:
                    pr_event = await self.detect_pull_request(issue_number, job_id)
                    if pr_event:
                        status = AgentStatus.PR_OPEN
                        yield pr_event

                # --- Detect completion ---
                if status in (AgentStatus.RUNNING, AgentStatus.PR_OPEN):
                    done_event = await self.detect_task_completion(issue_number, job_id)
                    if done_event:
                        status = AgentStatus.COMPLETED
                        yield done_event
                        return  # terminal — stop the watcher

            except Exception as exc:  # pylint: disable=broad-except
                logger.error(
                    "Unexpected error in watch_issue for #%d: %s",
                    issue_number,
                    exc,
                    exc_info=True,
                )
                yield CodingAgentEvent(
                    event_type=AgentEventType.ADAPTER_ERROR,
                    job_id=job_id,
                    issue_number=issue_number,
                    agent_username=self._copilot_username,
                    message=f"Watcher error on issue #{issue_number}: {exc}",
                )
                return  # stop on unexpected failure

            await asyncio.sleep(self._poll_interval)

        # Timeout path
        logger.warning(
            "watch_issue timed out after %.0fs for issue #%d (job_id=%s)",
            timeout,
            issue_number,
            job_id,
        )
        yield CodingAgentEvent(
            event_type=AgentEventType.ADAPTER_ERROR,
            job_id=job_id,
            issue_number=issue_number,
            agent_username=self._copilot_username,
            message=(
                f"Watcher timed out after {timeout:.0f}s monitoring issue #{issue_number}. "
                "No completion signal was received."
            ),
        )

    # ------------------------------------------------------------------
    # Webhook event parser — stateless, no I/O
    # ------------------------------------------------------------------

    def parse_webhook_event(
        self,
        event_type: str,
        payload: dict,
        job_id: Optional[str] = None,
    ) -> Optional[CodingAgentEvent]:
        """
        Convert a raw GitHub webhook payload into a ``CodingAgentEvent``.

        This method is synchronous and performs no I/O — it is safe to call
        from a request handler on the hot path.

        Supported webhook event types
        ------------------------------
        * ``issue_comment`` — Detects agent start, questions, and completion hints.
        * ``pull_request``  — Detects PR creation by the agent.
        * ``issues``        — Detects issue closure (task completion).

        Parameters
        ----------
        event_type:
            Value of the ``X-GitHub-Event`` header.
        payload:
            Decoded JSON payload dict.
        job_id:
            Optional pipeline job identifier for event correlation.

        Returns
        -------
        CodingAgentEvent or None
            Structured event if the webhook is relevant; ``None`` otherwise.
        """
        logger.debug("Parsing webhook event_type=%r for job_id=%s", event_type, job_id)

        if event_type == "issue_comment":
            return self._parse_issue_comment_webhook(payload, job_id)

        if event_type == "pull_request":
            return self._parse_pull_request_webhook(payload, job_id)

        if event_type == "issues":
            return self._parse_issues_webhook(payload, job_id)

        logger.debug("Webhook event_type=%r not handled by CodingAgentAdapter", event_type)
        return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _fetch_comments(self, issue_number: int) -> List[GitHubComment]:
        """Fetch all comments for an issue with retry."""
        try:
            async for attempt in _make_retry_policy(self._max_retries, 1, 10):
                with attempt:
                    return await self._issues.get_comments(issue_number)
        except (GitHubClientError, RetryError) as exc:
            logger.warning("Failed to fetch comments for issue #%d: %s", issue_number, exc)
            return []
        return []  # unreachable — satisfies mypy

    def _is_copilot_comment(self, comment: GitHubComment) -> bool:
        """Return True if ``comment`` was authored by the Copilot agent."""
        login = comment.user.login.lower()
        target = self._copilot_username.lower()
        return login == target or "copilot" in login

    @staticmethod
    def _comment_contains_question(body: str) -> bool:
        """Return True if the comment body matches any question pattern."""
        return any(p.search(body) for p in _QUESTION_PATTERNS)

    @staticmethod
    def _comment_indicates_completion(body: str) -> bool:
        """Return True if the comment body signals task completion."""
        return any(p.search(body) for p in _COMPLETION_PATTERNS)

    async def _detect_pr_from_comments(
        self,
        issue_number: int,
        job_id: Optional[str],
    ) -> Optional[CodingAgentEvent]:
        """Scan issue comments for a PR link authored by the Copilot bot."""
        pr_url_pattern = re.compile(
            r"https://github\.com/[^/]+/[^/]+/pull/(\d+)", re.IGNORECASE
        )
        comments = await self._fetch_comments(issue_number)
        for comment in comments:
            if not self._is_copilot_comment(comment):
                continue
            match = pr_url_pattern.search(comment.body)
            if match:
                pr_number = int(match.group(1))
                pr_url = match.group(0)
                logger.info(
                    "PR #%d detected via comment_id=%d on issue #%d",
                    pr_number,
                    comment.id,
                    issue_number,
                )
                return CodingAgentEvent(
                    event_type=AgentEventType.PR_CREATED,
                    job_id=job_id,
                    issue_number=issue_number,
                    pr_number=pr_number,
                    pr_url=pr_url,
                    comment_id=comment.id,
                    agent_username=self._copilot_username,
                    message=f"Copilot agent created PR #{pr_number} for issue #{issue_number}.",
                )
        return None

    async def _detect_pr_from_pr_list(
        self,
        issue_number: int,
        job_id: Optional[str],
    ) -> Optional[CodingAgentEvent]:
        """Scan open PRs for one that references the tracked issue."""
        try:
            async for attempt in _make_retry_policy(self._max_retries, 1, 10):
                with attempt:
                    prs_data: list = await self._client.get(  # type: ignore[assignment]
                        "/pulls",
                        params={"state": "open", "per_page": 30},
                    )
        except (GitHubClientError, RetryError) as exc:
            logger.warning("Could not list open PRs for issue #%d detection: %s", issue_number, exc)
            return None

        issue_ref_patterns = [
            re.compile(rf"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#?{issue_number}", re.IGNORECASE),
            re.compile(rf"#{issue_number}\b"),
        ]

        for pr_data in prs_data:
            # Only consider PRs opened by the Copilot bot.
            user_login: str = pr_data.get("user", {}).get("login", "").lower()
            if "copilot" not in user_login and user_login != self._copilot_username.lower():
                continue

            body: str = pr_data.get("body") or ""
            title: str = pr_data.get("title") or ""
            combined = f"{title}\n{body}"

            if any(p.search(combined) for p in issue_ref_patterns):
                pr_number = pr_data["number"]
                pr_url = pr_data["html_url"]
                logger.info(
                    "PR #%d matched issue #%d via PR list scan",
                    pr_number,
                    issue_number,
                )
                return CodingAgentEvent(
                    event_type=AgentEventType.PR_CREATED,
                    job_id=job_id,
                    issue_number=issue_number,
                    pr_number=pr_number,
                    pr_url=pr_url,
                    agent_username=self._copilot_username,
                    message=f"Copilot agent created PR #{pr_number} referencing issue #{issue_number}.",
                )
        return None

    # ------------------------------------------------------------------
    # Webhook sub-parsers (synchronous, no I/O)
    # ------------------------------------------------------------------

    def _parse_issue_comment_webhook(
        self,
        payload: dict,
        job_id: Optional[str],
    ) -> Optional[CodingAgentEvent]:
        """Parse ``issue_comment`` webhook payloads."""
        action = payload.get("action")
        if action != "created":
            return None

        comment_data = payload.get("comment", {})
        sender = payload.get("sender", {}).get("login", "")
        body: str = comment_data.get("body", "")
        comment_id: int = comment_data.get("id", 0)
        issue_number: int = payload.get("issue", {}).get("number", 0)

        is_copilot = (
            sender.lower() == self._copilot_username.lower()
            or "copilot" in sender.lower()
        )

        if not is_copilot:
            return None

        # Determine event flavour from comment content.
        if self._comment_contains_question(body):
            logger.debug(
                "Webhook: Copilot question detected on issue #%d comment_id=%d",
                issue_number,
                comment_id,
            )
            return CodingAgentEvent(
                event_type=AgentEventType.COPILOT_QUESTION,
                job_id=job_id,
                issue_number=issue_number,
                comment_id=comment_id,
                question=body,
                agent_username=self._copilot_username,
                message=f"Copilot asked a question on issue #{issue_number}.",
            )

        if self._comment_indicates_completion(body):
            logger.debug(
                "Webhook: Copilot completion signal on issue #%d comment_id=%d",
                issue_number,
                comment_id,
            )
            return CodingAgentEvent(
                event_type=AgentEventType.AGENT_COMPLETED,
                job_id=job_id,
                issue_number=issue_number,
                comment_id=comment_id,
                agent_username=self._copilot_username,
                message=f"Copilot signalled completion on issue #{issue_number}.",
            )

        # Generic start signal — any other Copilot comment.
        logger.debug(
            "Webhook: Copilot agent start/activity on issue #%d comment_id=%d",
            issue_number,
            comment_id,
        )
        return CodingAgentEvent(
            event_type=AgentEventType.AGENT_STARTED,
            job_id=job_id,
            issue_number=issue_number,
            comment_id=comment_id,
            agent_username=self._copilot_username,
            message=f"Copilot agent activity detected on issue #{issue_number}.",
        )

    def _parse_pull_request_webhook(
        self,
        payload: dict,
        job_id: Optional[str],
    ) -> Optional[CodingAgentEvent]:
        """Parse ``pull_request`` webhook payloads for PR creation by Copilot."""
        action = payload.get("action")
        if action not in {"opened", "reopened"}:
            return None

        pr_data = payload.get("pull_request", {})
        user_login: str = pr_data.get("user", {}).get("login", "")

        is_copilot = (
            user_login.lower() == self._copilot_username.lower()
            or "copilot" in user_login.lower()
        )
        if not is_copilot:
            return None

        pr_number: int = pr_data.get("number", 0)
        pr_url: str = pr_data.get("html_url", "")

        # Attempt to extract the linked issue number from the PR body.
        body: str = pr_data.get("body") or ""
        issue_match = re.search(r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#?(\d+)", body, re.IGNORECASE)
        issue_number = int(issue_match.group(1)) if issue_match else 0

        logger.debug(
            "Webhook: PR #%d opened by Copilot (linked issue #%s)",
            pr_number,
            issue_number or "unknown",
        )
        return CodingAgentEvent(
            event_type=AgentEventType.PR_CREATED,
            job_id=job_id,
            issue_number=issue_number,
            pr_number=pr_number,
            pr_url=pr_url,
            agent_username=self._copilot_username,
            message=f"Copilot agent opened PR #{pr_number}.",
        )

    def _parse_issues_webhook(
        self,
        payload: dict,
        job_id: Optional[str],
    ) -> Optional[CodingAgentEvent]:
        """Parse ``issues`` webhook payloads for issue closure (completion signal)."""
        action = payload.get("action")
        if action != "closed":
            return None

        issue_data = payload.get("issue", {})
        issue_number: int = issue_data.get("number", 0)

        logger.debug("Webhook: Issue #%d closed — signalling AGENT_COMPLETED", issue_number)
        return CodingAgentEvent(
            event_type=AgentEventType.AGENT_COMPLETED,
            job_id=job_id,
            issue_number=issue_number,
            agent_username=self._copilot_username,
            message=f"Issue #{issue_number} closed by GitHub — task complete.",
        )
