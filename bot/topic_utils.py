"""Small, pure helpers for routing Telegram messages to forum topics."""
from __future__ import annotations

from typing import Any


def topic_thread_id(message: Any) -> int | None:
    """Return a thread ID only for messages that Telegram marks as topic messages."""
    if not getattr(message, "is_topic_message", False):
        return None
    return getattr(message, "message_thread_id", None)


def notification_topic_kwargs(
    chat_id: int,
    notify_chat_id: int,
    thread_id: int | None,
) -> dict[str, int]:
    """Route status notifications to the originating topic only in the same chat."""
    if chat_id == notify_chat_id and thread_id is not None:
        return {"message_thread_id": thread_id}
    return {}
