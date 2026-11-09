"""Database, throttling, and force-subscription middlewares."""
from __future__ import annotations

import logging
import time
from typing import Any

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import Session, get_setting, setting_enabled, touch_user

log = logging.getLogger(__name__)


class DbMiddleware(BaseMiddleware):
    """Create one async DB session per incoming message/callback update."""

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user is None:
            return await handler(event, data)

        async with Session() as session:
            data["session"] = session
            is_new = await touch_user(session, user)
            data["new_user"] = is_new
            await session.commit()

            if (
                is_new
                and user.id not in settings.owner_ids
                and await setting_enabled(session, "new_user_alerts", True)
            ):
                notice = (
                    f"🆕 عضو جديد\n👤 {user.full_name}\n"
                    f"🆔 <code>{user.id}</code>"
                )
                for owner_id in settings.owner_ids:
                    try:
                        await data["bot"].send_message(owner_id, notice)
                    except TelegramForbiddenError:
                        log.info("Could not DM owner=%s about a new user", owner_id)
                    except TelegramAPIError:
                        log.exception("Failed to notify owner=%s about a new user", owner_id)

            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                log.exception("Handler failed; rolled back update for user=%s", user.id)
                raise


class ThrottleMiddleware(BaseMiddleware):
    """Per-user throttle with periodic cleanup of idle entries."""

    def __init__(self, interval: float = 0.15, max_entries: int = 5000):
        self.interval = max(0.0, interval)
        self.max_entries = max(100, max_entries)
        self.last: dict[int, float] = {}
        self.calls = 0

    async def __call__(self, handler, event, data):
        user_id = getattr(getattr(event, "from_user", None), "id", 0)
        now = time.monotonic()
        self.calls += 1
        if user_id:
            previous = self.last.get(user_id, 0.0)
            if now - previous < self.interval:
                if isinstance(event, CallbackQuery):
                    try:
                        await event.answer("تمهّل قليلاً")
                    except TelegramAPIError:
                        log.debug("Could not acknowledge throttled callback", exc_info=True)
                return
            self.last[user_id] = now

        if self.calls % 500 == 0 and len(self.last) > self.max_entries:
            cutoff = now - 300.0
            active = [
                (uid, timestamp)
                for uid, timestamp in self.last.items()
                if timestamp >= cutoff
            ]
            if len(active) > self.max_entries:
                active = sorted(active, key=lambda item: item[1], reverse=True)[
                    : self.max_entries
                ]
            self.last = dict(active)
        return await handler(event, data)


class MembershipMiddleware(BaseMiddleware):
    """Force-subscription check; scope changes are reserved for the permissions phase."""

    async def _verification_unavailable(self, event: Any) -> None:
        notice = (
            "⚠️ تعذّر التحقق من الاشتراك حالياً بسبب مشكلة في الوصول إلى القناة. "
            "تأكد من صلاحيات البوت ثم أعد المحاولة بعد قليل."
        )
        if isinstance(event, CallbackQuery):
            try:
                await event.answer("تعذّر التحقق مؤقتاً. حاول مرة أخرى.", show_alert=True)
            except TelegramAPIError:
                log.debug("Could not acknowledge membership check failure", exc_info=True)
        elif isinstance(event, Message):
            try:
                await event.answer(notice)
            except TelegramAPIError:
                log.debug("Could not send membership check warning", exc_info=True)

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        user_id = getattr(user, "id", None)
        if not user_id or user_id in settings.owner_ids:
            return await handler(event, data)

        # Let the explicit retry button reach its own membership-check handler.
        if isinstance(event, CallbackQuery) and event.data == "check_sub":
            return await handler(event, data)

        session: AsyncSession | None = data.get("session")
        if session is None or not await setting_enabled(session, "force_sub", False):
            return await handler(event, data)

        channel = (await get_setting(session, "channel_id", "")).strip()
        link = (await get_setting(session, "channel_link", settings.channel_link)).strip()
        if not channel:
            return await handler(event, data)

        chat: int | str = (
            int(channel)
            if channel.lstrip("-").isdigit()
            else channel if channel.startswith("@") else f"@{channel}"
        )
        try:
            member = await data["bot"].get_chat_member(chat, user_id)
            status = getattr(member.status, "value", member.status)
            allowed = status in {"member", "administrator", "creator"} or (
                status == "restricted" and getattr(member, "is_member", False)
            )
        except TelegramForbiddenError:
            log.exception(
                "Bot lacks access to inspect user=%s in subscription channel=%s",
                user_id, channel,
            )
            await self._verification_unavailable(event)
            return
        except TelegramAPIError:
            log.exception(
                "Telegram API failed checking user=%s in subscription channel=%s",
                user_id, channel,
            )
            await self._verification_unavailable(event)
            return
        except Exception:
            log.exception("Unexpected membership check failure for user=%s", user_id)
            await self._verification_unavailable(event)
            return

        if allowed:
            return await handler(event, data)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 الاشتراك بالقناة", url=link or "https://t.me/")],
            [InlineKeyboardButton(text="✅ تحقق من الاشتراك", callback_data="check_sub")],
        ])
        text = "🔒 <b>الاشتراك مطلوب</b>\n\nاشترك بالقناة أولاً ثم اضغط «تحقق من الاشتراك»."
        if isinstance(event, CallbackQuery):
            try:
                await event.answer("يجب الاشتراك أولاً", show_alert=True)
            except TelegramAPIError:
                log.debug("Could not acknowledge missing membership", exc_info=True)
            if event.message is not None:
                try:
                    await event.message.edit_text(text, reply_markup=keyboard)
                except TelegramAPIError:
                    log.debug("Could not edit force-subscription prompt", exc_info=True)
        elif isinstance(event, Message):
            try:
                await event.answer(text, reply_markup=keyboard)
            except TelegramAPIError:
                log.debug("Could not send force-subscription prompt", exc_info=True)
