from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import AsyncIterator

from adapters.coding_agent.issue_refs import extract_issue_number
from adapters.github.copilot_login import is_copilot_login
from config.settings import Settings
from domain.errors import GitHubForbiddenError, GitHubUnavailableError
from domain.models import EventType, PipelineEvent

_FIX_TRIGGER_TEXT = "@copilot Fix the failing tests"
_MAX_FIX_LOG_CHARS = 12_000
logger = logging.getLogger(__name__)

_COMPLETION_RE = re.compile(
    r"(task (is )?complete|ready for review|i('ve| have) completed)",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(
    r"(please (confirm|clarify|choose|reply)|waiting for (your )?(reply|answer)|"
    r"could you|which (option|approach))",
    re.IGNORECASE,
)


class CodingAgentAdapter:
    def __init__(self, github, settings: Settings) -> None:
        self.github = github
        self.settings = settings
        self._seen_comment_ids: set[str] = set()
        self._seen_pr_ids: set[int] = set()
        self._seen_run_ids: set[int] = set()
        self._draft_seen: set[int] = set()
        self._actions_forbidden = False

    def _is_coding_agent_login(self, login: str) -> bool:
        return is_copilot_login(login, self.settings.copilot_username)

    async def trigger(self, issue_number: int) -> None:
        await self.github.issues.assign_copilot(issue_number)

    async def trigger_fix_iteration(self, issue_number: int, error_log: str) -> None:
        log = (error_log or "").strip()
        if len(log) > _MAX_FIX_LOG_CHARS:
            log = log[:_MAX_FIX_LOG_CHARS] + "\n...[truncated]"
        body = f"{_FIX_TRIGGER_TEXT}\n\n```\n{log}\n```"
        await self.github.comments.create_issue_comment(issue_number, body)

    async def detect_task_completion(
        self, issue_number: int, pr_number: int | None
    ) -> bool:
        if pr_number:
            pr = await self.github.pull_requests.get_pull_request(pr_number)
            if pr.requested_reviewers:
                return True
            if pr.draft:
                self._draft_seen.add(pr_number)
            elif pr_number in self._draft_seen:
                return True
        if await self._issue_is_closed(issue_number):
            return True
        comments = await self.github.comments.list_issue_comments(issue_number)
        return any(
            self._looks_like_completion_text(self._comment_body(c))
            for c in comments
            if self._is_coding_agent_login(self._comment_login(c))
        )

    async def _issue_is_closed(self, issue_number: int) -> bool:
        issue = await self.github.issues.get_issue(issue_number)
        return (issue.get("state") or "").lower() == "closed"

    def _looks_like_completion_text(self, body: str) -> bool:
        return bool(_COMPLETION_RE.search(body or ""))

    def _looks_like_question(self, body: str) -> bool:
        return bool(_QUESTION_RE.search(body or ""))

    def _comment_login(self, comment) -> str:
        if hasattr(comment, "user"):
            return getattr(comment.user, "login", "") or ""
        user = comment.get("user") if isinstance(comment, dict) else None
        if isinstance(user, dict):
            return user.get("login") or ""
        return ""

    def _comment_body(self, comment) -> str:
        if hasattr(comment, "body"):
            return comment.body or ""
        return (comment.get("body") if isinstance(comment, dict) else "") or ""

    def _comment_id(self, comment) -> str:
        if hasattr(comment, "id"):
            return str(comment.id)
        return str((comment.get("id") if isinstance(comment, dict) else "") or "")

    async def watch_issue(self, issue_number: int) -> AsyncIterator[PipelineEvent]:
        interval = self.settings.coding_agent_poll_interval_sec
        while True:
            async for event in self._poll_once(issue_number):
                yield event
            await asyncio.sleep(interval)

    async def _poll_once(self, issue_number: int) -> AsyncIterator[PipelineEvent]:
        comments = await self.github.comments.list_issue_comments(issue_number)
        for comment in comments:
            cid = self._comment_id(comment)
            if not cid or cid in self._seen_comment_ids:
                continue
            self._seen_comment_ids.add(cid)
            event = self._event_from_comment(issue_number, comment)
            if event:
                yield event

        pulls = await self.github.pull_requests.list_pulls_for_issue(issue_number)
        for raw in pulls:
            number = raw.get("number")
            if not number:
                continue
            pr = await self.github.pull_requests.get_pull_request(number)
            if pr.draft:
                self._draft_seen.add(number)
            first_seen = number not in self._seen_pr_ids
            if first_seen:
                self._seen_pr_ids.add(number)
                yield PipelineEvent(
                    event_id=f"pr-opened-{number}",
                    type=EventType.PR_OPENED,
                    issue_number=issue_number,
                    pr_number=number,
                    payload={"html_url": pr.html_url, "head_ref": pr.head_ref},
                )
            if await self.detect_task_completion(issue_number, number):
                yield PipelineEvent(
                    event_id=f"agent-completed-{number}",
                    type=EventType.AGENT_COMPLETED,
                    issue_number=issue_number,
                    pr_number=number,
                )
            if pr.head_ref:
                async for event in self._poll_actions(issue_number, number, pr.head_ref):
                    yield event

        if await self._issue_is_closed(issue_number):
            yield PipelineEvent(
                event_id=f"issue-closed-{issue_number}",
                type=EventType.ISSUE_CLOSED,
                issue_number=issue_number,
            )

    async def _poll_actions(
        self, issue_number: int, pr_number: int, branch: str
    ) -> AsyncIterator[PipelineEvent]:
        if self._actions_forbidden:
            return
        try:
            runs = await self.github.actions.list_runs_for_branch(branch)
        except GitHubForbiddenError:
            self._actions_forbidden = True
            logger.warning(
                "GitHub Actions API forbidden; backup poll skips CI, webhook still works"
            )
            return
        except GitHubUnavailableError:
            logger.warning("GitHub Actions API unavailable during backup poll")
            return
        except Exception:
            logger.exception("GitHub Actions backup poll failed")
            return
        for run in runs:
            run_id = run.get("id")
            status = (run.get("status") or "").lower()
            conclusion = (run.get("conclusion") or "").lower()
            if not run_id or status != "completed" or run_id in self._seen_run_ids:
                continue
            self._seen_run_ids.add(run_id)
            if conclusion == "success":
                yield PipelineEvent(
                    event_id=f"run-success-{run_id}",
                    type=EventType.TESTS_PASSED,
                    issue_number=issue_number,
                    pr_number=pr_number,
                )
            elif conclusion in {"failure", "timed_out"}:
                yield PipelineEvent(
                    event_id=f"run-failure-{run_id}",
                    type=EventType.TESTS_FAILED,
                    issue_number=issue_number,
                    pr_number=pr_number,
                    error_log=run.get("html_url") or f"workflow run {run_id} failed",
                )

    def _event_from_comment(
        self, issue_number: int, comment
    ) -> PipelineEvent | None:
        login = self._comment_login(comment)
        if not self._is_coding_agent_login(login):
            return None
        body = self._comment_body(comment)
        cid = self._comment_id(comment)
        if self._looks_like_completion_text(body):
            return PipelineEvent(
                event_id=f"comment-complete-{cid}",
                type=EventType.AGENT_COMPLETED,
                issue_number=issue_number,
                body=body,
            )
        if self._looks_like_question(body):
            return PipelineEvent(
                event_id=f"comment-question-{cid}",
                type=EventType.COPILOT_QUESTION,
                issue_number=issue_number,
                body=body,
            )
        return PipelineEvent(
            event_id=f"comment-started-{cid}",
            type=EventType.AGENT_STARTED,
            issue_number=issue_number,
            body=body,
        )

    def parse_webhook_event(
        self, event_name: str, payload: dict
    ) -> PipelineEvent | None:
        name = (event_name or "").lower()
        if name in {"issues", "issue_comment"}:
            return self._parse_issue_webhook(name, payload)
        if name == "pull_request":
            return self._parse_pr_webhook(payload)
        if name == "workflow_run":
            return self._parse_actions_webhook(payload)
        return None

    def _stable_id(self, *parts: object) -> str:
        raw = json.dumps(parts, default=str, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def _parse_issue_webhook(
        self, name: str, payload: dict
    ) -> PipelineEvent | None:
        issue = payload.get("issue") or {}
        issue_number = issue.get("number")
        if not issue_number:
            return None
        if name == "issues" and payload.get("action") == "closed":
            return PipelineEvent(
                event_id=f"issue-closed-{issue_number}",
                type=EventType.ISSUE_CLOSED,
                issue_number=issue_number,
            )
        if name == "issues" and payload.get("action") == "edited":
            body = issue.get("body") or ""
            return PipelineEvent(
                event_id=f"issue-edited-{issue_number}-{self._stable_id(issue.get('updated_at'), body)}",
                type=EventType.ISSUE_UPDATED,
                issue_number=issue_number,
                body=body,
            )
        comment = payload.get("comment") or {}
        login = ((comment.get("user") or {}).get("login")) or ""
        if not self._is_coding_agent_login(login):
            return None
        body = comment.get("body") or ""
        cid = comment.get("id") or self._stable_id(issue_number, body)
        if self._looks_like_completion_text(body):
            return PipelineEvent(
                event_id=f"comment-complete-{cid}",
                type=EventType.AGENT_COMPLETED,
                issue_number=issue_number,
                body=body,
            )
        if self._looks_like_question(body):
            return PipelineEvent(
                event_id=f"comment-question-{cid}",
                type=EventType.COPILOT_QUESTION,
                issue_number=issue_number,
                body=body,
            )
        return PipelineEvent(
            event_id=f"comment-started-{cid}",
            type=EventType.AGENT_STARTED,
            issue_number=issue_number,
            body=body,
        )

    def _parse_pr_webhook(self, payload: dict) -> PipelineEvent | None:
        pr = payload.get("pull_request") or {}
        number = pr.get("number")
        if not number:
            return None
        issue_number = extract_issue_number(
            pr.get("body"),
            pr.get("title"),
            (pr.get("head") or {}).get("ref"),
        ) or (payload.get("issue") or {}).get("number")
        action = payload.get("action")
        if action == "opened":
            if pr.get("draft"):
                self._draft_seen.add(number)
            return PipelineEvent(
                event_id=f"pr-opened-{number}",
                type=EventType.PR_OPENED,
                issue_number=issue_number,
                pr_number=number,
                payload={
                    "html_url": pr.get("html_url") or "",
                    "head_ref": (pr.get("head") or {}).get("ref") or "",
                },
            )
        if action in {"ready_for_review", "review_requested"}:
            return PipelineEvent(
                event_id=f"agent-completed-{number}",
                type=EventType.AGENT_COMPLETED,
                issue_number=issue_number,
                pr_number=number,
                payload={"html_url": pr.get("html_url") or ""},
            )
        return None

    def _parse_actions_webhook(self, payload: dict) -> PipelineEvent | None:
        run = payload.get("workflow_run") or {}
        if (run.get("status") or "").lower() != "completed":
            return None
        run_id = run.get("id")
        conclusion = (run.get("conclusion") or "").lower()
        prs = run.get("pull_requests") or []
        pr_number = prs[0]["number"] if prs else None
        issue_number = extract_issue_number(run.get("head_branch"))
        if conclusion == "success":
            return PipelineEvent(
                event_id=f"run-success-{run_id}",
                type=EventType.TESTS_PASSED,
                issue_number=issue_number,
                pr_number=pr_number,
            )
        if conclusion in {"failure", "timed_out"}:
            return PipelineEvent(
                event_id=f"run-failure-{run_id}",
                type=EventType.TESTS_FAILED,
                issue_number=issue_number,
                pr_number=pr_number,
                error_log=run.get("html_url") or f"workflow run {run_id} failed",
            )
        return None
