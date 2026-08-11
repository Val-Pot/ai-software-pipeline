"""
Individual event handlers for webhooks.
"""
from __future__ import annotations

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def handle_issue_event(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Handle issues webhook events."""
    action = payload.get("action")
    issue = payload.get("issue", {})
    logger.info("Received issue event action=%s for issue #%s", action, issue.get("number"))
    return None


def handle_pr_event(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Handle pull request webhook events."""
    action = payload.get("action")
    pr = payload.get("pull_request", {})
    logger.info("Received PR event action=%s for PR #%s", action, pr.get("number"))
    if action in {"opened", "reopened", "synchronize"}:
        return {
            "event_type": "pr_opened",
            "pr_number": pr.get("number"),
            "pr_url": pr.get("html_url"),
            "branch": pr.get("head", {}).get("ref"),
        }
    return None


def handle_actions_event(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Handle workflow_run webhook events."""
    action = payload.get("action")
    run = payload.get("workflow_run", {})
    logger.info("Received workflow_run event action=%s for run_id=%s", action, run.get("id"))
    if action == "completed":
        conclusion = run.get("conclusion")
        return {
            "event_type": "tests_passed" if conclusion == "success" else "tests_failed",
            "run_id": run.get("id"),
            "conclusion": conclusion,
            "failure_log": f"Workflow run {run.get('id')} finished with conclusion: {conclusion}",
        }
    return None
