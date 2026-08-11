"""
Typed notification payloads for Telegram Adapter.
"""
from __future__ import annotations

from typing import Optional
from dataclasses import dataclass


@dataclass(frozen=True)
class NotificationPayload:
    chat_id: int
    text: str
    reply_markup: Optional[object] = None
