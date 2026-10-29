from __future__ import annotations

from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError


@dataclass
class ChannelInfo:
    ok: bool = False
    problem: str = ""
    chat_id: int = 0
    kind: str = ""
    title: str = ""
    username: str | None = None


def status_of(member) -> str:
    """Return the member status as a plain string ('creator', 'administrator', 'member', 'left', 'kicked'...)."""
    status = getattr(member, "status", member)
    return str(getattr(status, "value", status))


async def inspect_channel(bot: Bot, ref, need_admin: bool = False, need_post_rights: bool = False) -> ChannelInfo:
    """Check that the bot can use a chat (channel/group) and return its details."""
    try:
        chat = await bot.get_chat(ref)
    except TelegramAPIError:
        return ChannelInfo(problem="لم أجد هذه الوجهة، أو البوت ليس فيها. أضف البوت أولاً ثم أعد المحاولة.")
    kind = str(getattr(chat.type, "value", chat.type))
    if kind not in {"channel", "group", "supergroup"}:
        return ChannelInfo(problem="الوجهة يجب أن تكون قناة أو مجموعة.")
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat.id, me.id)
    except TelegramAPIError:
        return ChannelInfo(problem="تعذّر التحقق من صلاحيات البوت هناك. تأكد أنه داخل الوجهة.")
    status = status_of(member)
    if status in {"left", "kicked"}:
        return ChannelInfo(problem="البوت ليس عضواً في هذه الوجهة.")
    if (need_admin or (need_post_rights and kind == "channel")) and status not in {"administrator", "creator"}:
        return ChannelInfo(problem="يجب أن يكون البوت مشرفاً في هذه الوجهة.")
    if need_post_rights and kind == "channel" and getattr(member, "can_post_messages", True) is False:
        return ChannelInfo(problem="البوت مشرف لكن بلا صلاحية «نشر الرسائل». فعّلها له.")
    return ChannelInfo(ok=True, chat_id=chat.id, kind=kind, title=chat.title or "",
                       username=getattr(chat, "username", None))
