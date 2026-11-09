"""Safe Telegram operations shared by handlers."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)

log = logging.getLogger(__name__)


async def safe_copy_message(
    bot: Bot,
    *,
    chat_id: int,
    from_chat_id: int,
    message_id: int,
    max_attempts: int = 5,
) -> bool:
    """Copy a message with FloodWait retries and safe blocked-chat handling.

    Network timeouts are not retried because delivery may have succeeded
    before the connection was lost, which could duplicate a broadcast.
    """
    attempts = max(1, max_attempts)
    for attempt in range(1, attempts + 1):
        try:
            await bot.copy_message(
                chat_id=chat_id,
                from_chat_id=from_chat_id,
                message_id=message_id,
            )
            return True
        except TelegramRetryAfter as exc:
            delay = float(exc.retry_after) + 0.5
            log.warning(
                "Telegram FloodWait during broadcast to chat=%s; attempt=%s/%s; sleep=%.1fs",
                chat_id,
                attempt,
                attempts,
                delay,
            )
            if attempt >= attempts:
                return False
            await asyncio.sleep(delay)
        except TelegramForbiddenError:
            log.info(
                "Broadcast recipient chat=%s is inaccessible or has blocked the bot",
                chat_id,
            )
            return False
        except TelegramBadRequest as exc:
            log.warning("Telegram rejected broadcast copy to chat=%s: %s", chat_id, exc)
            return False
        except (TelegramNetworkError, asyncio.TimeoutError):
            log.exception(
                "Broadcast copy timed out for chat=%s; not retrying to avoid duplicates",
                chat_id,
            )
            return False
        except TelegramAPIError:
            log.exception("Telegram API failed while broadcasting to chat=%s", chat_id)
            return False
    return False
