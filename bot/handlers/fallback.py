"""Fallback for old, deleted, or unsupported inline buttons."""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery

router = Router()
log = logging.getLogger(__name__)


@router.callback_query()
async def unknown_callback(callback: CallbackQuery) -> None:
    log.info(
        "Unsupported/stale callback from user=%s: %s",
        callback.from_user.id,
        callback.data,
    )
    try:
        await callback.answer(
            "هذا الزر قديم أو لم يعد متاحاً. افتح /menu أو /adminpanel من جديد.",
            show_alert=True,
        )
    except TelegramAPIError:
        log.debug("Could not acknowledge stale callback", exc_info=True)
