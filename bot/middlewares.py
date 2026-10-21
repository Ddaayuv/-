from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from .db import Session, touch_user, get_setting, setting_enabled
from .config import settings
class DbMiddleware(BaseMiddleware):
    async def __call__(self,handler,event,data):
        user=getattr(event,"from_user",None)
        if user is None:return await handler(event,data)
        async with Session() as session:
            data["session"]=session; is_new=await touch_user(session,user); data["new_user"]=is_new; await session.commit()
            if is_new and user.id not in settings.owner_ids and await setting_enabled(session,"new_user_alerts",True):
                text=f"🆕 عضو جديد\n👤 {user.full_name}\n🆔 <code>{user.id}</code>"
                for oid in settings.owner_ids:
                    try:await data["bot"].send_message(oid,text)
                    except Exception:pass
            try:
                result=await handler(event,data); await session.commit(); return result
            except Exception:
                await session.rollback(); raise
class ThrottleMiddleware(BaseMiddleware):
    def __init__(self):self.last={}
    async def __call__(self,handler,event,data):
        uid=getattr(getattr(event,"from_user",None),"id",0); import time; now=time.monotonic()
        if uid and now-self.last.get(uid,0)<0.15:
            if isinstance(event,CallbackQuery):await event.answer("تمهّل قليلاً")
            return
        self.last[uid]=now; return await handler(event,data)
class MembershipMiddleware(BaseMiddleware):
    async def __call__(self,handler,event,data):
        user=getattr(event,"from_user",None); uid=getattr(user,"id",None)
        if not uid or uid in settings.owner_ids:return await handler(event,data)
        session:AsyncSession=data.get("session")
        if session is None or not await setting_enabled(session,"force_sub",False):return await handler(event,data)
        channel=(await get_setting(session,"channel_id","")).strip(); link=(await get_setting(session,"channel_link",settings.channel_link)).strip()
        if not channel:return await handler(event,data)
        chat=int(channel) if channel.lstrip("-").isdigit() else (channel if channel.startswith("@") else "@"+channel)
        try:
            member=await data["bot"].get_chat_member(chat,uid); allowed=member.status in {"member","administrator","creator"} or (member.status=="restricted" and getattr(member,"is_member",False))
        except Exception:allowed=False
        if allowed:return await handler(event,data)
        from aiogram.types import InlineKeyboardMarkup,InlineKeyboardButton
        kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📢 الاشتراك بالقناة",url=link or "https://t.me/")],[InlineKeyboardButton(text="✅ تحقق من الاشتراك",callback_data="check_sub")]])
        text="🔒 <b>الاشتراك مطلوب</b>\n\nاشترك بالقناة أولاً ثم اضغط «تحقق من الاشتراك»."
        if isinstance(event,CallbackQuery):
            await event.answer("يجب الاشتراك أولاً",show_alert=True)
            try:await event.message.edit_text(text,reply_markup=kb)
            except Exception:pass
        elif isinstance(event,Message):await event.answer(text,reply_markup=kb)
