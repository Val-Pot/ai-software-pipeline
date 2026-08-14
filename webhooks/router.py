"""
FastAPI Router for GitHub Webhooks.

Validates HMAC SHA-256 signatures, parses webhook payloads through
the CodingAgentAdapter (structured agent events) and the legacy
GitHubWebhookParser (CI/PR events), then forwards to the Orchestrator.

DI dependencies are resolved from ``app.dependencies`` — no direct
access to app state within handler functions.
"""
from __future__ import annotations

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel

from adapters.coding_agent.adapter import CodingAgentAdapter
from adapters.coding_agent.models import AgentEventType
from adapters.github.webhooks import (
    GitHubWebhookParser,
    GitHubWebhookVerifier,
    WebhookVerificationError,
    extract_github_event_refs,
    parse_webhook_payload,
)
from app.dependencies import (
    get_coding_agent_adapter,
    get_pipeline_runner,
    get_webhook_verifier,
)
from config.settings import get_settings
from orchestrator.pipeline_runner import PipelineRunner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["GitHub Webhooks"])


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------


class WebhookResponse(BaseModel):
    status: str
    event_type: str
    delivery_id: str
    job_id: str = "N/A"
    message: str


# ---------------------------------------------------------------------------
# GitHub webhook endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/github",
    response_model=WebhookResponse,
    status_code=status.HTTP_200_OK,
    summary="GitHub Webhook Receiver",
    description=(
        "Receives GitHub webhook events, verifies HMAC SHA-256 signatures, "
        "and routes agent/CI events to the Orchestrator."
    ),
)
async def receive_github_webhook(
    request: Request,
    x_github_event: Annotated[str, Header(alias="X-GitHub-Event")],
    x_github_delivery: Annotated[str, Header(alias="X-GitHub-Delivery")],
    x_hub_signature_256: Annotated[str, Header(alias="X-Hub-Signature-256")],
    verifier: Annotated[GitHubWebhookVerifier, Depends(get_webhook_verifier)],
    runner: Annotated[PipelineRunner, Depends(get_pipeline_runner)],
    coding_agent: Annotated[Optional[CodingAgentAdapter], Depends(get_coding_agent_adapter)],
) -> WebhookResponse:
    """
    GitHub Webhook receiver.

    Processing pipeline:
      1. Verify HMAC SHA-256 signature.
      2. Parse JSON body.
      3. Route to CodingAgentAdapter (agent lifecycle events).
      4. Route to GitHubWebhookParser (CI/PR pipeline events).
      5. Forward structured event to PipelineRunner.
    """
    logger.info(
        "GitHub webhook received: event=%s delivery_id=%s",
        x_github_event,
        x_github_delivery,
    )

    # ---- 1. Read body and verify HMAC signature -------------------------
    body = await request.body()
    try:
        verifier.verify(body, x_hub_signature_256)
    except WebhookVerificationError as exc:
        logger.warning(
            "Webhook HMAC verification failed for delivery_id=%s: %s",
            x_github_delivery,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid HMAC SHA-256 webhook signature.",
        ) from exc

    # ---- 2. Parse JSON payload from the already-read body ----------------
    try:
        payload: dict = parse_webhook_payload(body)
    except Exception as exc:
        logger.error(
            "Failed to parse JSON body for delivery_id=%s: %s",
            x_github_delivery,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload.",
        ) from exc

    # ---- 3. Attempt CodingAgentAdapter parsing (agent lifecycle) ---------
    refs = extract_github_event_refs(payload)
    job_id: str = refs.get("job_id") or ""

    if coding_agent is not None:
        agent_event = coding_agent.parse_webhook_event(x_github_event, payload, job_id=job_id)
        if agent_event is not None:
            logger.info(
                "CodingAgentAdapter parsed event=%s for delivery_id=%s",
                agent_event.event_type,
                x_github_delivery,
            )

            # Map CodingAgentEvent → Orchestrator event types
            orchestrator_event_type = _agent_event_to_orchestrator_type(agent_event.event_type)
            if orchestrator_event_type:
                orchestrator_payload = {
                    "pr_number": agent_event.pr_number or refs.get("pr_number"),
                    "pr_url": agent_event.pr_url,
                    "issue_number": agent_event.issue_number or refs.get("issue_number"),
                    "question": agent_event.question,
                    "comment_id": agent_event.comment_id,
                    "message": agent_event.message,
                }
                success = await runner.process_event(
                    job_id=job_id,
                    event_id=x_github_delivery,
                    event_type=orchestrator_event_type,
                    payload=orchestrator_payload,
                )
                return WebhookResponse(
                    status="processed" if success else "failed",
                    event_type=agent_event.event_type,
                    delivery_id=x_github_delivery,
                    job_id=job_id,
                    message=(
                        "Agent event forwarded to Orchestrator"
                        if success
                        else "Orchestrator rejected agent event"
                    ),
                )

            # Recognised agent event but no matching Orchestrator transition.
            return WebhookResponse(
                status="acknowledged",
                event_type=agent_event.event_type,
                delivery_id=x_github_delivery,
                job_id=job_id,
                message=f"Agent event acknowledged (no Orchestrator transition): {agent_event.event_type}",
            )

    # ---- 4. Fallback: legacy GitHubWebhookParser (CI/PR events) ----------
    settings = getattr(request.app.state, "settings", None) or get_settings()
    parsed_event = GitHubWebhookParser.parse_event(
        x_github_event,
        payload,
        ci_workflow_names=settings.ci_workflow_names,
    )
    if not parsed_event:
        logger.info(
            "Unhandled GitHub event=%s delivery_id=%s — ignored.",
            x_github_event,
            x_github_delivery,
        )
        return WebhookResponse(
            status="ignored",
            event_type=x_github_event,
            delivery_id=x_github_delivery,
            message="Event type or action not subscribed.",
        )

    # ---- 5. Forward CI/PR event to Orchestrator ---------------------------
    success = await runner.process_event(
        job_id=job_id,
        event_id=x_github_delivery,
        event_type=parsed_event["event_type"],
        payload=parsed_event,
    )

    logger.info(
        "Webhook event=%s delivery_id=%s forwarded to Orchestrator: success=%s",
        parsed_event["event_type"],
        x_github_delivery,
        success,
    )

    return WebhookResponse(
        status="processed" if success else "failed",
        event_type=parsed_event["event_type"],
        delivery_id=x_github_delivery,
        job_id=job_id,
        message=(
            "Event successfully forwarded to Orchestrator"
            if success
            else "Orchestrator rejected event"
        ),
    )


# ---------------------------------------------------------------------------
# Helper: map CodingAgentEvent types to Orchestrator event types
# ---------------------------------------------------------------------------

_AGENT_TO_ORCHESTRATOR: dict[AgentEventType, str] = {
    AgentEventType.PR_CREATED: "pr_opened",
    AgentEventType.AGENT_COMPLETED: "agent_completed",
    AgentEventType.COPILOT_QUESTION: "copilot_question",
    AgentEventType.AGENT_STARTED: "agent_started",
    AgentEventType.FIX_REQUESTED: "fix_requested",
    # AGENT_ASSIGNED and ADAPTER_ERROR produce no Orchestrator transition.
}


def _agent_event_to_orchestrator_type(event_type: AgentEventType) -> Optional[str]:
    """Map an ``AgentEventType`` to its Orchestrator-level event string, or None."""
    return _AGENT_TO_ORCHESTRATOR.get(event_type)
