from __future__ import annotations

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Header, HTTPException, Request

logger = logging.getLogger(__name__)


def verify_signature(secret: str, body: bytes, signature: str | None) -> bool:
    if not secret:
        return True
    if not signature or not signature.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={digest}", signature)


def build_webhook_router(container) -> APIRouter:
    router = APIRouter()

    @router.post("/webhooks/github")
    async def github_webhook(
        request: Request,
        x_github_event: str | None = Header(default=None),
        x_hub_signature_256: str | None = Header(default=None),
    ):
        body = await request.body()
        settings = container.settings
        if not (settings.github_webhook_secret or "").strip():
            raise HTTPException(status_code=503, detail="webhook secret is not configured")
        if not verify_signature(settings.github_webhook_secret, body, x_hub_signature_256):
            raise HTTPException(status_code=401, detail="invalid signature")
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="invalid json") from exc
        event = container.coding_agent.parse_webhook_event(x_github_event or "", payload)
        if event is None:
            return {"ok": True, "ignored": True}
        await container.runner.process_event(event)
        return {"ok": True, "event_id": event.event_id}

    return router
