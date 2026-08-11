"""
GitHub Webhook HMAC signature verifier and Webhook event parser.
"""
from __future__ import annotations

import hmac
import hashlib
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


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


class GitHubWebhookParser:
    """Parses raw webhook payloads into structured Orchestrator events."""

    @staticmethod
    def parse_event(event_type: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract structured pipeline events from raw webhooks."""
        if event_type == "pull_request":
            action = payload.get("action")
            if action in {"opened", "reopened", "synchronize"}:
                pr = payload.get("pull_request", {})
                return {
                    "event_type": "pr_opened",
                    "pr_number": pr.get("number"),
                    "pr_url": pr.get("html_url"),
                    "branch": pr.get("head", {}).get("ref"),
                    "sender": payload.get("sender", {}).get("login"),
                }

        elif event_type == "workflow_run":
            action = payload.get("action")
            if action == "completed":
                run = payload.get("workflow_run", {})
                conclusion = run.get("conclusion")
                return {
                    "event_type": "tests_passed" if conclusion == "success" else "tests_failed",
                    "run_id": run.get("id"),
                    "conclusion": conclusion,
                    "failure_log": f"Workflow '{run.get('name')}' finished with conclusion: {conclusion}",
                }

        elif event_type in {"issue_comment", "pull_request_review_comment"}:
            action = payload.get("action")
            if action == "created":
                comment = payload.get("comment", {})
                body = comment.get("body", "")
                sender = payload.get("sender", {}).get("login")
                
                # Copilot question detection heuristics
                is_copilot = "copilot" in sender.lower() or "bot" in sender.lower()
                has_question = "?" in body or "please clarify" in body.lower()

                if is_copilot and has_question:
                    issue = payload.get("issue") or payload.get("pull_request") or {}
                    return {
                        "event_type": "copilot_question",
                        "question": body,
                        "issue_number": issue.get("number"),
                        "sender": sender,
                    }

        return None
