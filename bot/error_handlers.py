"""Centralized logging and safe handling for Telegram update errors."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)

log = logging.getLogger(__name__)

STALE_ERRORS = (
    "message to edit not found",
    "message to delete not found",
    "message can't be edited",
    "message is not modified",
    "query is too old",
    "query id is invalid",
    "message_id_invalid",
)


async def _answer_callback(callback: Any, text: str) -> None:
    try:
        await callback.answer(text, show_alert=True)
    except TelegramAPIError:
        log.debug("Callback may already be expired", exc_info=True)


async def handle_update_error(event: Any) -> bool:
    update = getattr(event, "update", None)
    exc = getattr(event, "exception", None)
    callback = getattr(update, "callback_query", None)
    message = (
        getattr(update, "message", None)
        or getattr(update, "edited_message", None)
        or getattr(update, "channel_post", None)
        or getattr(update, "edited_channel_post", None)
    )
    user = getattr(callback, "from_user", None) or getattr(message, "from_user", None)
    chat = getattr(message, "chat", None) or getattr(
        getattr(callback, "message", None), "chat", None
    )
    user_id = getattr(user, "id", None)
    chat_id = getattr(chat, "id", None)

    if isinstance(exc, TelegramRetryAfter):
        log.warning(
            "Telegram FloodWait on update (user=%s, chat=%s), retry_after=%s",
            user_id,
            chat_id,
            exc.retry_after,
        )
        return True

    if isinstance(exc, TelegramForbiddenError):
        log.info(
            "Telegram access denied (user=%s, chat=%s); user may have blocked bot "
            "or bot may have been removed",
            user_id,
            chat_id,
        )
        return True

    if isinstance(exc, TelegramBadRequest):
        details = str(exc).lower()
        if any(fragment in details for fragment in STALE_ERRORS):
            log.info(
                "Ignored stale Telegram message/callback operation (user=%s, chat=%s): %s",
                user_id,
                chat_id,
                exc,
            )
            if callback is not None:
                await _answer_callback(
                    callback,
                    "انتهت صلاحية هذا الزر أو لم تعد الرسالة موجودة. افتح القائمة من جديد.",
                )
            return True
        log.warning(
            "Telegram rejected update (user=%s, chat=%s): %s",
            user_id,
            chat_id,
            exc,
        )
    elif isinstance(exc, (TelegramNetworkError, TelegramServerError, asyncio.TimeoutError)):
        log.error(
            "Temporary Telegram network/server failure (user=%s, chat=%s)",
            user_id,
            chat_id,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
    elif isinstance(exc, TelegramAPIError):
        log.error(
            "Telegram API error (user=%s, chat=%s)",
            user_id,
            chat_id,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
    else:
        log.error(
            "Unhandled update exception (user=%s, chat=%s)",
            user_id,
            chat_id,
            exc_info=(type(exc), exc, getattr(exc, "__traceback__", None)),
        )

    if callback is not None:
        await _answer_callback(callback, "حدث خطأ مؤقت أثناء تنفيذ الطلب. حاول مرة أخرى.")
    elif message is not None:
        try:
            await message.answer("حدث خطأ مؤقت أثناء تنفيذ الطلب. حاول مرة أخرى.")
        except Exception:
            log.debug("Could not send error notice to user", exc_info=True)
    return True
