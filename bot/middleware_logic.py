"""Pure helpers for middleware chat and activity policies."""
from __future__ import annotations

from datetime import datetime
from typing import Any


def is_private_chat(chat: Any) -> bool:
    if chat is None:
        return False
    chat_type = getattr(chat, "type", chat)
    return str(getattr(chat_type, "value", chat_type)).lower() == "private"


def should_refresh_last_active(
    last_active: datetime | None,
    now: datetime,
    minimum_interval_seconds: int = 300,
) -> bool:
    if last_active is None:
        return True
    return (now - last_active).total_seconds() >= minimum_interval_seconds
