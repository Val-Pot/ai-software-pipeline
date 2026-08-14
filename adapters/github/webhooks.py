"""
GitHub Webhook HMAC signature verifier and Webhook event parser.
"""
from __future__ import annotations

import hmac
import hashlib
import json
import logging
import re
from typing import Any, Dict, FrozenSet, Optional, TypedDict
from urllib.parse import parse_qs

logger = logging.getLogger(__name__)


def parse_webhook_payload(body: bytes) -> Dict[str, Any]:
    """
    Parse a GitHub webhook body.

    GitHub can deliver either raw JSON or ``application/x-www-form-urlencoded``
    with a ``payload`` field. Always parse the already-read bytes — calling
    ``request.json()`` after ``request.body()`` yields an empty document.
    """
    if not body:
        raise ValueError("Empty webhook body")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        form = parse_qs(body.decode("utf-8"), keep_blank_values=True)
        raw = form.get("payload", [None])[0]
        if raw is None:
            raise
        payload = json.loads(raw)

    if not isinstance(payload, dict):
        raise ValueError("Webhook JSON payload must be an object")
    return payload


_JOB_ID_IN_BODY = re.compile(
    r"\*\*Job ID:\*\*\s*`([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})`"
)
_ISSUE_REF_IN_BODY = re.compile(
    r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)",
    re.IGNORECASE,
)
_SENTINEL_JOB_IDS = {"", "active_job", "n/a", "unresolved"}


class GitHubEventRefs(TypedDict, total=False):
    job_id: str
    issue_number: int
    pr_number: int


def _as_positive_int(value: Any) -> Optional[int]:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def extract_github_event_refs(payload: Dict[str, Any]) -> GitHubEventRefs:
    """
    Pull job/issue/PR identifiers out of a raw GitHub webhook payload.

    GitHub never sends our pipeline ``job_id``. We recover it from the issue
    body (stamped at create time) and/or from issue and pull-request numbers.
    """
    refs: GitHubEventRefs = {}

    raw_job_id = payload.get("job_id")
    if isinstance(raw_job_id, str) and raw_job_id.strip().lower() not in _SENTINEL_JOB_IDS:
        refs["job_id"] = raw_job_id.strip()

    issue = payload.get("issue") if isinstance(payload.get("issue"), dict) else {}
    pr = payload.get("pull_request") if isinstance(payload.get("pull_request"), dict) else {}
    run = payload.get("workflow_run") if isinstance(payload.get("workflow_run"), dict) else {}

    issue_number = _as_positive_int(issue.get("number"))
    pr_number = _as_positive_int(pr.get("number"))

    # Comments on a PR use issue.number == PR number and set issue.pull_request.
    if pr_number is None and issue.get("pull_request") and issue_number is not None:
        pr_number = issue_number

    if pr_number is None:
        pull_requests = run.get("pull_requests") or []
        if pull_requests and isinstance(pull_requests[0], dict):
            pr_number = _as_positive_int(pull_requests[0].get("number"))

    bodies = [issue.get("body") or "", pr.get("body") or ""]
    for body in bodies:
        if "job_id" not in refs:
            job_match = _JOB_ID_IN_BODY.search(body)
            if job_match:
                refs["job_id"] = job_match.group(1)
        if issue_number is None:
            issue_match = _ISSUE_REF_IN_BODY.search(body)
            if issue_match:
                issue_number = int(issue_match.group(1))

    if issue_number is not None:
        refs["issue_number"] = issue_number
    if pr_number is not None:
        refs["pr_number"] = pr_number
    return refs


class WebhookVerificationError(Exception):
    """Raised when HMAC signature fails verification."""
    pass


class GitHubWebhookVerifier:
    """HMAC SHA256 Webhook verifier."""

    def __init__(self, secret: str) -> None:
        self.secret = secret.encode("utf-8")

    def verify(self, payload_bytes: bytes, signature_header: Optional[str]) -> bool:
        """Verify GitHub HMAC-SHA256 signature."""
        if not signature_header or not signature_header.startswith("sha256="):
            logger.warning("Missing or malformed X-Hub-Signature-256 header")
            raise WebhookVerificationError("Missing or malformed signature header")

        expected_sig = signature_header.split("sha256=")[1]
        computed_sig = hmac.new(self.secret, payload_bytes, hashlib.sha256).hexdigest()

        if not hmac.compare_digest(computed_sig, expected_sig):
            logger.warning("HMAC signature mismatch")
            raise WebhookVerificationError("Signature mismatch")

        return True


_CI_PASSED_CONCLUSIONS = frozenset({"success"})
_CI_FAILED_CONCLUSIONS = frozenset({"failure", "timed_out"})

#: Events the GitHub repository webhook must send to POST /webhooks/github.
REQUIRED_WEBHOOK_EVENTS = ("pull_request", "workflow_run", "issue_comment")


class GitHubWebhookParser:
    """Parses raw webhook payloads into structured Orchestrator events."""

    @staticmethod
    def parse_event(
        event_type: str,
        payload: Dict[str, Any],
        *,
        ci_workflow_names: FrozenSet[str] | None = None,
    ) -> Optional[Dict[str, Any]]:
        """Extract structured pipeline events from raw webhooks."""
        parsed: Optional[Dict[str, Any]] = None

        if event_type == "pull_request":
            parsed = GitHubWebhookParser._parse_pull_request(payload)
        elif event_type == "workflow_run":
            parsed = GitHubWebhookParser._parse_workflow_run(payload, ci_workflow_names)
        elif event_type in {"issue_comment", "pull_request_review_comment"}:
            parsed = GitHubWebhookParser._parse_copilot_question(payload)

        if parsed is None:
            return None

        refs = extract_github_event_refs(payload)
        for key, value in refs.items():
            if value is not None and parsed.get(key) in (None, "", 0):
                parsed[key] = value
        return parsed

    @staticmethod
    def _parse_pull_request(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        action = payload.get("action")
        # synchronize is a new push on an existing PR — not a new pipeline event.
        if action not in {"opened", "reopened"}:
            return None
        pr = payload.get("pull_request", {})
        return {
            "event_type": "pr_opened",
            "pr_number": pr.get("number"),
            "pr_url": pr.get("html_url"),
            "branch": pr.get("head", {}).get("ref"),
            "sender": payload.get("sender", {}).get("login"),
        }

    @staticmethod
    def _parse_workflow_run(
        payload: Dict[str, Any],
        ci_workflow_names: FrozenSet[str] | None,
    ) -> Optional[Dict[str, Any]]:
        if payload.get("action") != "completed":
            return None

        run = payload.get("workflow_run") or {}
        name = (run.get("name") or "").strip()
        allowed = ci_workflow_names or frozenset()
        if allowed and name.lower() not in allowed:
            logger.info(
                "Ignoring workflow_run name=%r (CI filter: %s)",
                name,
                ", ".join(sorted(allowed)),
            )
            return None

        conclusion = (run.get("conclusion") or "").lower()
        if conclusion in _CI_PASSED_CONCLUSIONS:
            event_type = "tests_passed"
        elif conclusion in _CI_FAILED_CONCLUSIONS:
            event_type = "tests_failed"
        else:
            logger.info(
                "Ignoring workflow_run conclusion=%s name=%r (not a CI pass/fail)",
                conclusion,
                name,
            )
            return None

        return {
            "event_type": event_type,
            "run_id": run.get("id"),
            "conclusion": conclusion,
            "workflow_name": name,
            "failure_log": f"Workflow '{name}' finished with conclusion: {conclusion}",
        }

    @staticmethod
    def _parse_copilot_question(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if payload.get("action") != "created":
            return None
        comment = payload.get("comment", {})
        body = comment.get("body", "")
        sender = payload.get("sender", {}).get("login") or ""

        is_copilot = "copilot" in sender.lower() or "bot" in sender.lower()
        has_question = "?" in body or "please clarify" in body.lower()
        if not (is_copilot and has_question):
            return None

        issue = payload.get("issue") or payload.get("pull_request") or {}
        return {
            "event_type": "copilot_question",
            "question": body,
            "issue_number": issue.get("number"),
            "sender": sender,
        }
