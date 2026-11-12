import logging
import time
from collections import OrderedDict

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import Session, touch_user, get_setting, setting_enabled
from .middleware_logic import is_private_chat

log = logging.getLogger(__name__)


def _event_chat(event):
    chat = getattr(event, "chat", None)
    if chat is not None:
        return chat
    message = getattr(event, "message", None)
    return getattr(message, "chat", None)


class DbMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        private_update = is_private_chat(_event_chat(event))

        async with Session() as session:
            data["session"] = session
            is_new = False
            if private_update and user is not None:
                is_new = await touch_user(session, user)
            data["new_user"] = is_new
            if is_new:
                await session.commit()
                if user.id not in settings.owner_ids and await setting_enabled(session, "new_user_alerts", True):
                    text = f"🆕 عضو جديد\n👤 {user.full_name}\n🆔 <code>{user.id}</code>"
                    for owner_id in settings.owner_ids:
                        try:
                            await data["bot"].send_message(owner_id, text)
                        except TelegramAPIError:
                            log.warning("Failed to notify owner %s about new user %s", owner_id, user.id, exc_info=True)
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise


class ThrottleMiddleware(BaseMiddleware):
    def __init__(
        self,
        window_seconds: float = 0.15,
        idle_ttl_seconds: float = 300.0,
        cleanup_interval_seconds: float = 60.0,
        max_tracked_users: int = 10000,
    ):
        self.window_seconds = window_seconds
        self.idle_ttl_seconds = idle_ttl_seconds
        self.cleanup_interval_seconds = cleanup_interval_seconds
        self.max_tracked_users = max_tracked_users
        self.last_seen = OrderedDict()
        self.last_cleanup = 0.0

    def _cleanup(self, now: float) -> None:
        if now - self.last_cleanup < self.cleanup_interval_seconds:
            return
        self.last_cleanup = now
        while self.last_seen:
            _uid, last_seen = next(iter(self.last_seen.items()))
            if now - last_seen <= self.idle_ttl_seconds:
                break
            self.last_seen.popitem(last=False)

    async def __call__(self, handler, event, data):
        # Group tests and forum-topic conversations must never be throttled by this private-chat guard.
        if not is_private_chat(_event_chat(event)):
            return await handler(event, data)

        user_id = getattr(getattr(event, "from_user", None), "id", None)
        if user_id is None:
            return await handler(event, data)

        now = time.monotonic()
        self._cleanup(now)
        previous = self.last_seen.get(user_id)
        self.last_seen[user_id] = now
        self.last_seen.move_to_end(user_id)
        while len(self.last_seen) > self.max_tracked_users:
            self.last_seen.popitem(last=False)

        if previous is not None and now - previous < self.window_seconds:
            if isinstance(event, CallbackQuery):
                await event.answer("تمهّل قليلاً")
            return None
        return await handler(event, data)


class MembershipMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        # Force-subscription is a private-user onboarding rule. Never gate group messages/callbacks.
        if not is_private_chat(_event_chat(event)):
            return await handler(event, data)
        user = getattr(event, "from_user", None)
        uid = getattr(user, "id", None)
        if not uid or uid in settings.owner_ids:
            return await handler(event, data)
        session: AsyncSession = data.get("session")
        if session is None or not await setting_enabled(session, "force_sub", False):
            return await handler(event, data)
        channel = (await get_setting(session, "channel_id", "")).strip()
        link = (await get_setting(session, "channel_link", settings.channel_link)).strip()
        if not channel:
            return await handler(event, data)
        chat = int(channel) if channel.lstrip("-").isdigit() else (channel if channel.startswith("@") else "@" + channel)
        try:
            member = await data["bot"].get_chat_member(chat, uid)
            allowed = member.status in {"member", "administrator", "creator"} or (
                member.status == "restricted" and getattr(member, "is_member", False)
            )
        except TelegramAPIError:
            log.warning("Membership check failed for user %s in channel %s", uid, chat, exc_info=True)
            allowed = False
        if allowed:
            return await handler(event, data)

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 الاشتراك بالقناة", url=link or "https://t.me/")],
            [InlineKeyboardButton(text="✅ تحقق من الاشتراك", callback_data="check_sub")],
        ])
        prompt = "🔒 <b>الاشتراك مطلوب</b>\n\nاشترك بالقناة أولاً ثم اضغط «تحقق من الاشتراك»."
        if isinstance(event, CallbackQuery):
            await event.answer("يجب الاشتراك أولاً", show_alert=True)
            try:
                if event.message:
                    await event.message.edit_text(prompt, reply_markup=kb)
            except TelegramAPIError:
                log.warning("Could not display membership prompt for user %s", uid, exc_info=True)
        elif isinstance(event, Message):
            await event.answer(prompt, reply_markup=kb)
        return None
