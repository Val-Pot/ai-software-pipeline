"""
Dedicated GitHub Actions Adapter supporting REST API polling, webhook parsing, and status event resolution.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, List, Dict, Any

from adapters.github.client import GitHubHTTPClient, GitHubClientError
from adapters.github.actions_models import (
    WorkflowStatus,
    WorkflowConclusion,
    ActionsWorkflowRun,
    ActionsStatusEvent,
)

logger = logging.getLogger(__name__)


class GitHubActionsAdapter:
    """
    Adapter for interacting with GitHub Actions workflows.
    Exposes query methods and event parsers for the Orchestrator.
    """

    def __init__(self, client: GitHubHTTPClient) -> None:
        self.client = client

    async def get_workflow_run(self, run_id: int) -> ActionsWorkflowRun:
        """Read a specific GitHub Actions workflow run by ID."""
        data = await self.client.get(f"/actions/runs/{run_id}")
        return ActionsWorkflowRun.from_dict(data)

    async def get_latest_run_for_branch(self, branch_name: str) -> Optional[ActionsWorkflowRun]:
        """Fetch the latest workflow run for a specific git branch."""
        res = await self.client.get("/actions/runs", params={"branch": branch_name, "per_page": 1})
        runs = res.get("workflow_runs", [])
        if not runs:
            return None
        return ActionsWorkflowRun.from_dict(runs[0])

    async def poll_workflow_completion(
        self,
        run_id: int,
        poll_interval: float = 5.0,
        timeout: float = 300.0,
    ) -> ActionsStatusEvent:
        """
        Poll a workflow run until completed or timeout reached.
        """
        start_time = asyncio.get_event_loop().time()
        while True:
            run = await self.get_workflow_run(run_id)
            if run.status == WorkflowStatus.COMPLETED:
                logger.info("Workflow run_id=%d completed with conclusion=%s", run_id, run.conclusion)
                return self.to_status_event(run)

            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed >= timeout:
                logger.warning("Polling workflow run_id=%d timed out after %.1fs", run_id, timeout)
                return ActionsStatusEvent(
                    run_id=run_id,
                    workflow_name=run.name,
                    status=WorkflowStatus.COMPLETED,
                    conclusion=WorkflowConclusion.TIMED_OUT,
                    branch=run.head_branch,
                    sha=run.head_sha,
                    html_url=run.html_url,
                    event_type="tests_failed",
                    message=f"Workflow run timed out after {timeout} seconds",
                )

            await asyncio.sleep(poll_interval)

    @staticmethod
    def parse_webhook_payload(payload: Dict[str, Any]) -> Optional[ActionsStatusEvent]:
        """
        Parse raw workflow_run webhook payloads into structured ActionsStatusEvent.
        """
        action = payload.get("action")
        run_data = payload.get("workflow_run")
        if not run_data:
            return None

        run = ActionsWorkflowRun.from_dict(run_data)

        # Handle in-progress or queued status
        if action in {"requested", "in_progress"}:
            return ActionsStatusEvent(
                run_id=run.id,
                workflow_name=run.name,
                status=run.status,
                conclusion=run.conclusion,
                branch=run.head_branch,
                sha=run.head_sha,
                html_url=run.html_url,
                event_type="tests_running",
                message=f"Workflow '{run.name}' status: {run.status}",
            )

        # Handle completed action
        if action == "completed":
            return GitHubActionsAdapter.to_status_event(run)

        return None

    @staticmethod
    def to_status_event(run: ActionsWorkflowRun) -> ActionsStatusEvent:
        """Map ActionsWorkflowRun model to structured ActionsStatusEvent for Orchestrator."""
        if run.conclusion == WorkflowConclusion.SUCCESS:
            event_type = "tests_passed"
            message = f"Workflow '{run.name}' passed successfully!"
        elif run.conclusion in {WorkflowConclusion.FAILURE, WorkflowConclusion.TIMED_OUT, WorkflowConclusion.CANCELLED}:
            event_type = "tests_failed"
            message = f"Workflow '{run.name}' failed with conclusion: {run.conclusion}"
        else:
            event_type = "tests_running"
            message = f"Workflow '{run.name}' status: {run.status}"

        return ActionsStatusEvent(
            run_id=run.id,
            workflow_name=run.name,
            status=run.status,
            conclusion=run.conclusion,
            branch=run.head_branch,
            sha=run.head_sha,
            html_url=run.html_url,
            event_type=event_type,
            message=message,
        )
