from aiogram import Router, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from ..config import settings
from ..db import User, Subject, Section, Item, BotSetting, audit, set_setting, setting_enabled, create_item, slugify, build_search_text
from ..utils import esc, is_url
import asyncio
from .menu_manager import router as menu_manager_router
from datetime import datetime, timedelta
router=Router()
router.include_router(menu_manager_router)
def owner(uid): return uid in settings.owner_ids
def grid(buttons,w=2): return [buttons[i:i+w] for i in range(0,len(buttons),w)]
def panel_kb(): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📊 الإحصائيات",callback_data="adm:stats"),InlineKeyboardButton(text="👥 المستخدمون",callback_data="adm:users")],[InlineKeyboardButton(text="➕ إضافة محتوى",callback_data="adm:add"),InlineKeyboardButton(text="📚 إدارة المواد",callback_data="adm:subjects")],[InlineKeyboardButton(text="📢 إذاعة",callback_data="adm:broadcast"),InlineKeyboardButton(text="⚙️ الإعدادات",callback_data="adm:settings")],[InlineKeyboardButton(text="🔒 الاشتراك الإجباري",callback_data="adm:forcesub"),InlineKeyboardButton(text="🔔 إشعارات الأعضاء",callback_data="adm:alerts")],[InlineKeyboardButton(text="📣 السماح بالإذاعة",callback_data="adm:announce"),InlineKeyboardButton(text="📖 عرض المواد",callback_data="adm:home")],[InlineKeyboardButton(text="🤖 إنشاء اختبار",callback_data="quiz:start"),InlineKeyboardButton(text="⚙️ إدارة الأزرار",callback_data="adm:buttons")]])
def back_panel(): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ لوحة المطوّر",callback_data="adm:panel")]])
class AddContent(StatesGroup): subject=State(); mode=State(); section=State(); title=State(); description=State(); attachments=State()
class AddSubject(StatesGroup): meta=State()
class Broadcast(StatesGroup): content=State(); confirm=State()
class EditContent(StatesGroup): values=State()
class EditSubject(StatesGroup): name=State()
class DeleteContent(StatesGroup): confirm=State()
def cancel_kb(): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ إلغاء",callback_data="addflow:cancel")]])
def upload_kb(n): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"✅ حفظ ونشر ({n} مرفق)",callback_data="addflow:finish")],[InlineKeyboardButton(text="❌ إلغاء",callback_data="addflow:cancel")]])
@router.message(Command("adminpanel"))
async def adminpanel(m,session):
    if owner(m.from_user.id): await m.answer("🛠 <b>لوحة تحكم المطوّر</b>\n\n👑 تم التعرف عليك كمطوّر.",reply_markup=panel_kb())
    else: await m.answer("⛔ هذا الأمر للمطوّر فقط.")
@router.message(Command("cancel"))
async def cancel(m,state):
    if owner(m.from_user.id): await state.clear(); await m.answer("✅ تم إلغاء العملية.",reply_markup=panel_kb())
@router.callback_query(F.data=="adm:panel")
async def panel(cb,state):
    if not owner(cb.from_user.id): return await cb.answer("ممنوع",show_alert=True)
    await state.clear(); await cb.message.edit_text("🛠 <b>لوحة تحكم المطوّر</b>\n\nاختر ما تريد إدارته:",reply_markup=panel_kb()); await cb.answer()
@router.callback_query(F.data=="adm:stats")
async def stats(cb,session):
    if not owner(cb.from_user.id):return await cb.answer("ممنوع",show_alert=True)
    now=datetime.utcnow(); users=await session.scalar(select(func.count(User.id))) or 0; active=await session.scalar(select(func.count(User.id)).where(User.last_active>=now-timedelta(days=1))) or 0; week=await session.scalar(select(func.count(User.id)).where(User.joined_at>=now-timedelta(days=7))) or 0; subjects=await session.scalar(select(func.count(Subject.id))) or 0; items=await session.scalar(select(func.count(Item.id))) or 0
    await cb.message.edit_text(f"📊 <b>إحصائيات</b>\n\n👥 المستخدمون: <b>{users}</b>\n🟢 نشطون 24 ساعة: <b>{active}</b>\n🆕 خلال 7 أيام: <b>{week}</b>\n📚 المواد: <b>{subjects}</b>\n📦 المحتوى: <b>{items}</b>",reply_markup=back_panel()); await cb.answer()
@router.callback_query(F.data=="adm:users")
async def users(cb,session):
    if not owner(cb.from_user.id):return await cb.answer("ممنوع",show_alert=True)
    total=await session.scalar(select(func.count(User.id))) or 0; rows=(await session.scalars(select(User).order_by(User.joined_at.desc()).limit(15))).all(); lines=[f"👥 <b>إجمالي المستخدمين: {total}</b>","","🆕 آخر الأعضاء:"]
    for u in rows: lines.append(f"• {esc(u.name or 'مستخدم')} — <code>{u.telegram_id}</code>"+(f" @ {esc(u.username)}" if u.username else ""))
    await cb.message.edit_text("\n".join(lines),reply_markup=back_panel()); await cb.answer()
async def show_subjects(cb,session):
    rows=(await session.scalars(select(Subject).order_by(Subject.sort_order,Subject.id))).all(); k=grid([InlineKeyboardButton(text=f"{s.emoji} {s.name_ar[:35]}",callback_data=f"adm:subject:{s.id}") for s in rows]); k += [[InlineKeyboardButton(text="➕ إضافة مادة",callback_data="adm:addsubject")],[InlineKeyboardButton(text="⬅️ اللوحة",callback_data="adm:panel")]]; await cb.message.edit_text("📚 <b>إدارة المواد</b>\n\nاختر مادة:",reply_markup=InlineKeyboardMarkup(inline_keyboard=k))
@router.callback_query(F.data=="adm:subjects")
async def subjects(cb,session):
    if not owner(cb.from_user.id):return await cb.answer("ممنوع",show_alert=True)
    await show_subjects(cb,session); await cb.answer()
@router.callback_query(F.data.startswith("adm:subject:"))
async def subject_details(cb,session):
    if not owner(cb.from_user.id):return await cb.answer("ممنوع",show_alert=True)
    sid=int(cb.data.rsplit(":",1)[1]); s=await session.get(Subject,sid)
    if not s:return await cb.answer("المادة غير موجودة",show_alert=True)
    count=await session.scalar(select(func.count(Item.id)).where(Item.subject_id==sid)) or 0
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✏️ تغيير اسم المادة",callback_data=f"adm:editname:{sid}")],[InlineKeyboardButton(text="🔄 تغيير عملي/نظري",callback_data=f"adm:toggle:{sid}")],[InlineKeyboardButton(text="🙈 إخفاء المادة" if s.is_active else "👁 إظهار المادة",callback_data=f"adm:visibility:{sid}")],[InlineKeyboardButton(text=f"📦 المحتوى ({count})",callback_data=f"adm:subjectitems:{sid}")],[InlineKeyboardButton(text="⬅️ المواد",callback_data="adm:subjects")]])
    await cb.message.edit_text(f"{s.emoji} <b>{esc(s.name_ar)}</b>\n\nالظهور: {'🟢 ظاهر' if s.is_active else '🔴 مخفي'}\nطريقة الدراسة: {'عملي + نظري' if s.has_practical else 'قسم واحد'}\nالمحتوى: {count}",reply_markup=kb); await cb.answer()
@router.callback_query(F.data.startswith("adm:editname:"))
async def edit_subject_name_start(cb,state,session):
    if not owner(cb.from_user.id): return await cb.answer("ممنوع",show_alert=True)
    sid=int(cb.data.rsplit(":",1)[1]); subject=await session.get(Subject,sid)
    if not subject: return await cb.answer("المادة غير موجودة",show_alert=True)
    await state.set_state(EditSubject.name)
    await state.update_data(edit_subject_id=sid)
    await cb.message.answer(f"✏️ أرسل الاسم الجديد للمادة الحالية: <b>{esc(subject.name_ar)}</b>\n\nأرسل /cancel للإلغاء.")
    await cb.answer()

@router.message(EditSubject.name)
async def edit_subject_name_save(m,state,session):
    if not owner(m.from_user.id): return
    new_name=(m.text or "").strip()
    if new_name == "/cancel":
        await state.clear()
        return await m.answer("تم إلغاء تغيير اسم المادة.")
    if not new_name or len(new_name)>100:
        return await m.answer("أرسل اسماً من 1 إلى 100 حرف.")
    data=await state.get_data(); sid=int(data.get("edit_subject_id",0))
    subject=await session.get(Subject,sid)
    if not subject:
        await state.clear()
        return await m.answer("المادة غير موجودة؛ لم يتم تغيير شيء.")
    old_name=subject.name_ar
    subject.name_ar=new_name
    await audit(session,m.from_user.id,"rename_subject",f"{old_name} -> {new_name}")
    await session.commit()
    await state.clear()
    await m.answer(f"✅ تم تغيير اسم المادة إلى: <b>{esc(new_name)}</b>\n\nاحتفظنا بنفس معرّف المادة، لذلك تبقى المحاضرات والمرفقات مرتبطة بها.")

@router.callback_query(F.data.startswith("adm:toggle:"))
async def toggle(cb,session):
    if not owner(cb.from_user.id):return await cb.answer("ممنوع",show_alert=True)
    sid=int(cb.data.rsplit(":",1)[1]); s=await session.get(Subject,sid)
    if not s:return await cb.answer("المادة غير موجودة",show_alert=True)
    s.has_practical=not s.has_practical; await audit(session,cb.from_user.id,"toggle_subject_mode",s.name_ar); await cb.answer("تم التحديث ✅"); await subject_details(cb,session)
@router.callback_query(F.data.startswith("adm:visibility:"))
async def visibility(cb,session):
    if not owner(cb.from_user.id):return await cb.answer("ممنوع",show_alert=True)
    sid=int(cb.data.rsplit(":",1)[1]); s=await session.get(Subject,sid)
    if not s:return await cb.answer("المادة غير موجودة",show_alert=True)
    s.is_active=not s.is_active; await audit(session,cb.from_user.id,"toggle_subject_visibility",s.name_ar); await cb.answer("تم التحديث ✅"); await subject_details(cb,session)
@router.callback_query(F.data.startswith("adm:subjectitems:"))
async def subject_items(cb,session):
    if not owner(cb.from_user.id):return await cb.answer("ممنوع",show_alert=True)
    sid=int(cb.data.rsplit(":",1)[1]); rows=(await session.scalars(select(Item).where(Item.subject_id==sid).order_by(Item.id.desc()).limit(40))).all()
    if not rows:return await cb.answer("لا يوجد محتوى بهذه المادة",show_alert=True)
    k=grid([InlineKeyboardButton(text=("✅ " if x.is_published else "🙈 ")+f"#{x.id} {x.title[:30]}",callback_data=f"adm:item:{x.id}") for x in rows]); k.append([InlineKeyboardButton(text="⬅️ تفاصيل المادة",callback_data=f"adm:subject:{sid}")]); await cb.message.edit_text("📦 <b>محتويات المادة</b>",reply_markup=InlineKeyboardMarkup(inline_keyboard=k)); await cb.answer()
@router.callback_query(F.data.startswith("adm:item:"))
async def admin_item(cb,session):
    if not owner(cb.from_user.id):return await cb.answer("ممنوع",show_alert=True)
    item=await session.get(Item,int(cb.data.rsplit(":",1)[1]))
    if not item:return await cb.answer("المحتوى غير موجود",show_alert=True)
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🙈 إخفاء" if item.is_published else "👁 نشر",callback_data=f"adm:itemvisibility:{item.id}")],[InlineKeyboardButton(text="🗑 حذف",callback_data=f"adm:delete:{item.id}")],[InlineKeyboardButton(text="⬅️ رجوع",callback_data=f"adm:subjectitems:{item.subject_id}")]])
    await cb.message.edit_text(f"📄 <b>{esc(item.title)}</b>\n\n#{item.id}\nالحالة: {'منشور' if item.is_published else 'مخفي'}\nالمرفقات: {len(item.attachments)}",reply_markup=kb); await cb.answer()
@router.callback_query(F.data.startswith("adm:itemvisibility:"))
async def item_visibility(cb,session):
    if not owner(cb.from_user.id):return await cb.answer("ممنوع",show_alert=True)
    item=await session.get(Item,int(cb.data.rsplit(":",1)[1])); item.is_published=not item.is_published; await audit(session,cb.from_user.id,"toggle_item_visibility",f"#{item.id}"); await cb.answer("تم التحديث ✅"); await admin_item(cb,session)
@router.callback_query(F.data.startswith("adm:delete:"))
async def delete_start(cb,state):
    if not owner(cb.from_user.id):return await cb.answer("ممنوع",show_alert=True)
    item_id=int(cb.data.rsplit(":",1)[1]); await state.update_data(delete_item_id=item_id); await cb.message.edit_text("⚠️ هل تريد حذف المحتوى نهائياً؟",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🗑 نعم، احذف",callback_data="adm:deleteconfirm"),InlineKeyboardButton(text="❌ إلغاء",callback_data=f"adm:item:{item_id}")]])); await cb.answer()
@router.callback_query(F.data=="adm:deleteconfirm")
async def delete_confirm(cb,state,session):
    if not owner(cb.from_user.id):return await cb.answer("ممنوع",show_alert=True)
    item_id=(await state.get_data()).get("delete_item_id"); item=await session.get(Item,item_id) if item_id else None
    if not item:await state.clear();return await cb.answer("المحتوى غير موجود",show_alert=True)
    import json; sid=item.subject_id; snap=json.dumps({"subject_id":item.subject_id,"section_id":item.section_id,"title":item.title,"description":item.description,"study_mode":item.study_mode,"attachments":[{"kind":a.kind,"url":a.url,"tg_file_id":a.tg_file_id,"file_name":a.file_name,"size":a.size} for a in item.attachments]},ensure_ascii=False); await audit(session,cb.from_user.id,"delete_item",f"#{item_id} {snap}"); await session.delete(item); await session.commit(); await state.clear(); await cb.message.edit_text("✅ تم الحذف.",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ المحتوى",callback_data=f"adm:subjectitems:{sid}")],[InlineKeyboardButton(text="⬅️ اللوحة",callback_data="adm:panel")]])); await cb.answer()
@router.callback_query(F.data=="adm:forcesub")
async def force(cb,session):
    if not owner(cb.from_user.id):return await cb.answer("ممنوع",show_alert=True)
    v=not await setting_enabled(session,"force_sub"); await set_setting(session,"force_sub",str(v).lower()); await cb.message.edit_text(f"🔒 <b>الاشتراك الإجباري</b>\nالحالة: {'🟢 مفعّل' if v else '🔴 متوقف'}\n\n<code>/setchannel @channel | https://t.me/channel</code>",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 تبديل",callback_data="adm:forcesub")],[InlineKeyboardButton(text="⬅️ اللوحة",callback_data="adm:panel")]])); await cb.answer()
@router.callback_query(F.data=="adm:alerts")
async def alerts(cb,session):
    if not owner(cb.from_user.id):return await cb.answer("ممنوع",show_alert=True)
    v=not await setting_enabled(session,"new_user_alerts",True); await set_setting(session,"new_user_alerts",str(v).lower()); await cb.message.edit_text(f"🔔 <b>إشعارات الأعضاء</b>\nالحالة: {'🟢 مفعّلة' if v else '🔴 متوقفة'}",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 تبديل",callback_data="adm:alerts")],[InlineKeyboardButton(text="⬅️ اللوحة",callback_data="adm:panel")]])); await cb.answer()
@router.callback_query(F.data=="adm:announce")
async def announce(cb,session):
    if not owner(cb.from_user.id):return await cb.answer("ممنوع",show_alert=True)
    v=not await setting_enabled(session,"announce",True); await set_setting(session,"announce",str(v).lower()); await cb.message.edit_text(f"📣 <b>الإذاعة</b>\nالحالة: {'🟢 مسموحة' if v else '🔴 متوقفة'}",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 تبديل",callback_data="adm:announce")],[InlineKeyboardButton(text="⬅️ اللوحة",callback_data="adm:panel")]])); await cb.answer()
@router.callback_query(F.data=="adm:settings")
async def settings_view(cb,session):
    if not owner(cb.from_user.id):return await cb.answer("ممنوع",show_alert=True)
    f=await setting_enabled(session,"force_sub"); a=await setting_enabled(session,"new_user_alerts",True); b=await setting_enabled(session,"announce",True); c=await session.get(BotSetting,"channel_id")
    await cb.message.edit_text(f"⚙️ <b>الإعدادات</b>\n\n🔒 الاشتراك: {'ON' if f else 'OFF'}\n🔔 الأعضاء: {'ON' if a else 'OFF'}\n📣 الإذاعة: {'ON' if b else 'OFF'}\n📢 القناة: {esc(c.value if c else 'غير محددة')}\n\n<code>/setchannel @channel | https://t.me/channel</code>",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔒 الاشتراك",callback_data="adm:forcesub"),InlineKeyboardButton(text="🔔 الإشعارات",callback_data="adm:alerts")],[InlineKeyboardButton(text="📣 الإذاعة",callback_data="adm:announce")],[InlineKeyboardButton(text="⬅️ اللوحة",callback_data="adm:panel")]])); await cb.answer()
async def ask_subject(m,state,session):
    rows=(await session.scalars(select(Subject).where(Subject.is_active.is_(True)).order_by(Subject.sort_order,Subject.id))).all()
    if not rows:return await m.answer("لا توجد مواد فعالة. أضف مادة أولاً.")
    await state.set_state(AddContent.subject); await m.answer("➕ <b>إضافة محتوى</b>\n\n1/5 — اختر المادة:",reply_markup=InlineKeyboardMarkup(inline_keyboard=grid([InlineKeyboardButton(text=f"{s.emoji} {s.name_ar}",callback_data=f"addflow:subject:{s.id}") for s in rows])+[[InlineKeyboardButton(text="❌ إلغاء",callback_data="addflow:cancel")]]))
@router.callback_query(F.data=="adm:add")
async def add_start(cb,state,session):
    if not owner(cb.from_user.id):return await cb.answer("ممنوع",show_alert=True)
    await state.clear(); await ask_subject(cb.message,state,session); await cb.answer()
@router.message(Command("add"))
async def add_cmd(m,state,session):
    if owner(m.from_user.id):await state.clear(); await ask_subject(m,state,session)
@router.callback_query(F.data.startswith("addflow:subject:"))
async def add_subject(cb,state,session):
    sid=int(cb.data.rsplit(":",1)[1]); s=await session.get(Subject,sid)
    if not s:return await cb.answer("المادة غير موجودة",show_alert=True)
    await state.update_data(subject_id=sid,attachments=[])
    if s.has_practical:
        await state.set_state(AddContent.mode); await cb.message.edit_text(f"{s.emoji} {esc(s.name_ar)}\n\n2/5 — اختر النوع:",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📚 النظري",callback_data=f"addflow:mode:{sid}:theory"),InlineKeyboardButton(text="🧪 العملي",callback_data=f"addflow:mode:{sid}:practical")],[InlineKeyboardButton(text="❌ إلغاء",callback_data="addflow:cancel")]]))
    else:
        await state.update_data(study_mode="general"); await show_sections(cb.message,state,session)
    await cb.answer()
async def show_sections(m,state,session):
    rows=(await session.scalars(select(Section).where(Section.slug!="practical").order_by(Section.sort_order,Section.id))).all(); await state.set_state(AddContent.section); await m.answer("3/5 — اختر قسم الحفظ:",reply_markup=InlineKeyboardMarkup(inline_keyboard=grid([InlineKeyboardButton(text=f"{x.emoji} {x.name_ar}",callback_data=f"addflow:section:{x.id}") for x in rows])+[[InlineKeyboardButton(text="❌ إلغاء",callback_data="addflow:cancel")]]))
@router.callback_query(F.data.startswith("addflow:mode:"))
async def add_mode(cb,state,session):
    _,_,sid,mode=cb.data.split(":"); await state.update_data(subject_id=int(sid),study_mode=mode); await show_sections(cb.message,state,session); await cb.answer()
@router.callback_query(F.data.startswith("addflow:section:"))
async def add_section(cb,state):
    await state.update_data(section_id=int(cb.data.rsplit(":",1)[1])); await state.set_state(AddContent.title); await cb.message.answer("4/5 — أرسل عنوان المحتوى:",reply_markup=cancel_kb()); await cb.answer()
@router.message(AddContent.title)
async def add_title(m,state):
    title=(m.text or "").strip()
    if not title:return await m.answer("أرسل العنوان كنص.")
    await state.update_data(title=title[:300]); await state.set_state(AddContent.description); await m.answer("5/5 — أرسل وصفاً اختيارياً، أو اضغط تخطي.",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏭ تخطي",callback_data="addflow:skipdesc")],[InlineKeyboardButton(text="❌ إلغاء",callback_data="addflow:cancel")]]))
async def ask_attach(m,state,desc=""):
    await state.update_data(description=desc,attachments=[]); await state.set_state(AddContent.attachments); await m.answer("📎 أرسل PDF/ملف/صورة/فيديو/صوت أو رابطاً.\nيمكنك إرسال عدة مرفقات. عند الانتهاء اضغط حفظ أو اكتب /done.",reply_markup=upload_kb(0))
@router.callback_query(F.data=="addflow:skipdesc")
async def skipdesc(cb,state): await ask_attach(cb.message,state,""); await cb.answer()
@router.message(AddContent.description)
async def add_desc(m,state): await ask_attach(m,state,(m.text or "").strip())
def attachment(m):
    if m.document:return {"kind":"file","tg_file_id":m.document.file_id,"file_name":m.document.file_name,"size":m.document.file_size}
    if m.video:return {"kind":"video","tg_file_id":m.video.file_id,"file_name":m.video.file_name,"size":m.video.file_size}
    if m.audio:return {"kind":"audio","tg_file_id":m.audio.file_id,"file_name":m.audio.file_name,"size":m.audio.file_size}
    if m.voice:return {"kind":"voice","tg_file_id":m.voice.file_id,"file_name":"مقطع صوتي","size":m.voice.file_size}
    if m.photo:return {"kind":"photo","tg_file_id":m.photo[-1].file_id,"file_name":None,"size":m.photo[-1].file_size}
    if m.text and is_url(m.text.strip()):return {"kind":"link","url":m.text.strip(),"file_name":m.text.strip()}
    return None
@router.message(AddContent.attachments,Command("done"))
async def done(m,state,session): await finish_add(m,state,session)
@router.callback_query(F.data=="addflow:finish")
async def finish_button(cb,state,session): await finish_add(cb.message,state,session); await cb.answer("تم")
async def finish_add(m,state,session):
    d=await state.get_data(); att=d.get("attachments",[])
    if not att and not d.get("description"):return await m.answer("أرسل ملفاً أو نصاً قبل الحفظ.",reply_markup=upload_kb(0))
    try:
        item=await create_item(session,{"subject_id":d["subject_id"],"section_id":d["section_id"],"title":d["title"],"desc":d.get("description",""),"study_mode":d.get("study_mode","general"),"attachments":att},m.from_user.id); await audit(session,m.from_user.id,"add_item",f"#{item.id} {item.title}"); await session.commit(); await state.clear(); await m.answer(f"✅ <b>تم الحفظ والنشر</b>\n🆔 #{item.id}\n📘 {esc(item.title)}\n📎 {len(att)} مرفق",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➕ إضافة آخر",callback_data="adm:add")],[InlineKeyboardButton(text="🛠 اللوحة",callback_data="adm:panel")]]))
    except Exception:
        await session.rollback(); await m.answer("❌ تعذر الحفظ. اكتب /done للمحاولة مرة أخرى أو /cancel للإلغاء.")
@router.message(AddContent.attachments)
async def collect(m,state):
    d=await state.get_data(); a=attachment(m); arr=d.get("attachments",[])
    if a:
        if len(arr)>=20:return await m.answer("الحد الأقصى 20 مرفقاً.")
        arr.append(a); await state.update_data(attachments=arr); return await m.answer(f"✅ أضيف ({len(arr)}/20). أرسل المزيد أو احفظ.",reply_markup=upload_kb(len(arr)))
    if m.text and not m.text.startswith("/"):
        desc=(d.get("description") or "").strip(); await state.update_data(description=(desc+"\n\n"+m.text.strip()).strip()); return await m.answer("✅ أضيف النص. يمكنك المتابعة أو الحفظ.",reply_markup=upload_kb(len(arr)))
    await m.answer("أرسل ملفاً/رابطاً/نصاً أو /done.",reply_markup=upload_kb(len(arr)))
@router.callback_query(F.data=="addflow:cancel")
async def cancel_add(cb,state): await state.clear(); await cb.message.edit_text("❌ تم إلغاء الإضافة.",reply_markup=panel_kb()); await cb.answer()
@router.message(Command("addsubject"))
async def addsubject(m,command,state,session):
    if not owner(m.from_user.id):return
    if not command.args: await state.set_state(AddSubject.meta); return await m.answer("أرسل: <code>اسم المادة | English | 📘 | عملي</code>")
    await save_subject(m,command.args,state,session)
@router.callback_query(F.data=="adm:addsubject")
async def addsubject_btn(cb,state): await state.set_state(AddSubject.meta); await cb.message.answer("➕ أرسل: <code>اسم المادة | English | 📘 | عملي</code>"); await cb.answer()
@router.message(AddSubject.meta)
async def addsubject_state(m,state,session): await save_subject(m,m.text or "",state,session)
async def save_subject(m,raw,state,session):
    p=[x.strip() for x in raw.split("|")]; name=p[0] if p else ""
    if not name:return await m.answer("اسم المادة مطلوب.")
    en=p[1] if len(p)>1 else ""; emoji=p[2] if len(p)>2 else "📘"; practical=len(p)>3 and p[3].lower() in {"عملي","نعم","true","1","practical"}; base=slugify(en or name); slug=base; n=2
    while await session.scalar(select(Subject.id).where(Subject.slug==slug)):slug=f"{base}-{n}"; n+=1
    session.add(Subject(slug=slug,name_ar=name[:100],name_en=en[:100],emoji=emoji[:8],aliases=name,sort_order=(await session.scalar(select(func.max(Subject.sort_order))) or 0)+1,has_practical=practical)); await audit(session,m.from_user.id,"add_subject",name); await session.commit(); await state.clear(); await m.answer(f"✅ تمت إضافة المادة: <b>{esc(name)}</b>",reply_markup=panel_kb())
@router.message(Command("practical"))
async def practical(m,command,session):
    if not owner(m.from_user.id):return
    try:s=await session.get(Subject,int((command.args or "").strip()))
    except:s=None
    if not s:return await m.answer("الاستخدام: /practical رقم_المادة")
    s.has_practical=not s.has_practical; await m.answer("🧪 عملي + نظري" if s.has_practical else "📚 قسم واحد")
@router.message(Command("togglesubject"))
async def togglesubject(m,command,session):
    if not owner(m.from_user.id):return
    try:s=await session.get(Subject,int((command.args or "").strip()))
    except:s=None
    if not s:return await m.answer("الاستخدام: /togglesubject رقم_المادة")
    s.is_active=not s.is_active; await m.answer(f"✅ تم {'إظهار' if s.is_active else 'إخفاء'} المادة.")
@router.message(Command("setchannel"))
async def setchannel(m,command,session):
    if not owner(m.from_user.id):return
    p=[x.strip() for x in (command.args or "").split("|")]
    if not p or not p[0]:return await m.answer("/setchannel @channel | https://t.me/channel")
    ch=p[0].strip(); ch=("@"+ch.split("/")[-1]) if "t.me/" in ch else ch
    ch=ch.lstrip("@") if not ch.startswith("-") else ch; await set_setting(session,"channel_id",ch); await set_setting(session,"channel_link",p[1] if len(p)>1 else (f"https://t.me/{ch}" if not ch.startswith("-") else "")); await m.answer("✅ تم حفظ القناة.")
@router.message(Command("forcesub"))
async def forcesub(m,command,session):
    if owner(m.from_user.id):
        v=(command.args or "").strip().lower() in {"1","on","true","تشغيل","نعم"}; await set_setting(session,"force_sub",str(v).lower()); await m.answer("🔒 الاشتراك: "+("مفعّل" if v else "متوقف"))
@router.message(Command("alerts"))
async def alerts_cmd(m,command,session):
    if owner(m.from_user.id):
        v=(command.args or "").strip().lower() in {"1","on","true","تشغيل","نعم"}; await set_setting(session,"new_user_alerts",str(v).lower()); await m.answer("🔔 الإشعارات: "+("مفعّلة" if v else "متوقفة"))
@router.message(Command("subjects"))
async def subjects_cmd(m,session):
    if owner(m.from_user.id):
        rows=(await session.scalars(select(Subject).order_by(Subject.sort_order,Subject.id))).all(); await m.answer("📚 المواد:\n"+"\n".join(f"{x.id}. {esc(x.name_ar)}" for x in rows))
@router.callback_query(F.data=="adm:broadcast")
async def broadcast_start(cb,state):
    if not owner(cb.from_user.id):return await cb.answer("ممنوع",show_alert=True)
    await state.set_state(Broadcast.content); await cb.message.answer("📢 أرسل الرسالة/الملف الآن. ثم سأطلب تأكيداً.",reply_markup=cancel_kb()); await cb.answer()
@router.message(Command("broadcast"))
async def broadcast_cmd(m,state):
    if owner(m.from_user.id):await state.set_state(Broadcast.content); await m.answer("📢 أرسل المحتوى ثم أكد الإرسال.")
@router.message(Broadcast.content)
async def broadcast_preview(m,state,session):
    if not owner(m.from_user.id):return
    if not await setting_enabled(session,"announce",True):await state.clear();return await m.answer("⛔ الإذاعة متوقفة.")
    await state.update_data(chat_id=m.chat.id,message_id=m.message_id); await state.set_state(Broadcast.confirm); await m.answer("👁 تمت المعاينة. هل تؤكد الإرسال؟",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ إرسال",callback_data="adm:broadcast:confirm"),InlineKeyboardButton(text="❌ إلغاء",callback_data="adm:broadcast:cancel")]]))
@router.callback_query(F.data=="adm:broadcast:cancel")
async def broadcast_cancel(cb,state): await state.clear(); await cb.message.edit_text("❌ تم إلغاء الإذاعة.",reply_markup=panel_kb()); await cb.answer()
@router.callback_query(F.data=="adm:broadcast:confirm")
async def broadcast_confirm(cb,state,session):
    if not owner(cb.from_user.id):return await cb.answer("ممنوع",show_alert=True)
    d=await state.get_data(); users=(await session.scalars(select(User.telegram_id))).all(); ok=bad=0; await cb.message.edit_text(f"📢 جارٍ الإرسال إلى {len(users)} مستخدم...")
    for uid in users:
        try:await cb.bot.copy_message(uid,d["chat_id"],d["message_id"]); ok+=1
        except:bad+=1
        await asyncio.sleep(.04)
    await state.clear(); await cb.message.edit_text(f"✅ انتهت الإذاعة\nنجحت: {ok}\nفشلت: {bad}",reply_markup=back_panel()); await cb.answer()
@router.callback_query(F.data=="adm:home")
async def admin_home(cb,session):
    if owner(cb.from_user.id):
        from .user import show_home
        await show_home(cb.message,session,cb.from_user.id)
    await cb.answer()
@router.message(Command("hide"))
@router.message(Command("unhide"))
async def hide_unhide(m,command,session):
    if not owner(m.from_user.id):return
    try:item=await session.get(Item,int((command.args or "").strip()))
    except:item=None
    if not item:return await m.answer("المحتوى غير موجود.")
    item.is_published=m.text.startswith("/unhide"); await m.answer("✅ تم التحديث.")
@router.callback_query(F.data=="adm:settings")
async def settings_view(cb,session):
    if not owner(cb.from_user.id):return await cb.answer("ممنوع",show_alert=True)
    f=await setting_enabled(session,"force_sub"); a=await setting_enabled(session,"new_user_alerts",True); an=await setting_enabled(session,"announce",True); c=await session.get(BotSetting,"channel_id")
    await cb.message.edit_text(f"⚙️ <b>الإعدادات</b>\n\n🔒 الاشتراك: {'ON' if f else 'OFF'}\n🔔 الأعضاء: {'ON' if a else 'OFF'}\n📣 الإذاعة: {'ON' if an else 'OFF'}\n📢 القناة: {esc(c.value if c else 'غير محددة')}",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔒 الاشتراك",callback_data="adm:forcesub"),InlineKeyboardButton(text="🔔 الإشعارات",callback_data="adm:alerts")],[InlineKeyboardButton(text="📣 الإذاعة",callback_data="adm:announce")],[InlineKeyboardButton(text="⬅️ اللوحة",callback_data="adm:panel")]])); await cb.answer()

@router.message(Command("backup"))
async def backup_now(m):
    if not owner(m.from_user.id):return
    import os
    from aiogram.types import FSInputFile
    from ..backup import make_backup
    path=await make_backup(); await m.answer_document(FSInputFile(path),caption="💾 نسخة كاملة (تشمل المرفقات). احتفظ بها. للاسترجاع أرسل الملف مع الأمر /restore كتعليق."); os.remove(path)
@router.message(Command("restore"))
async def restore_cmd(m):
    if not owner(m.from_user.id):return
    await m.answer("♻️ أرسل ملف النسخة الاحتياطية (.json) مع كتابة /restore في تعليق الملف، أو رد على الملف بالأمر /restore. الاسترجاع يضيف فقط ما هو مفقود ولا يحذف أو يعدّل شيئاً موجوداً.")
@router.message(F.document,F.caption.startswith("/restore"))
@router.message(F.reply_to_message.document,F.text.startswith("/restore"))
async def restore_file(m):
    if not owner(m.from_user.id):return
    import json, io
    from ..backup import restore_backup
    doc=m.document or m.reply_to_message.document
    try:
        buf=io.BytesIO(); await m.bot.download(doc,destination=buf); added=await restore_backup(json.loads(buf.getvalue().decode("utf-8")))
    except Exception as e:
        return await m.answer(f"❌ فشل الاسترجاع: {esc(str(e))[:300]}")
    await m.answer(f"✅ تم الاسترجاع\n📚 مواد: {added['subjects']}\n📦 محتوى: {added['items']}\n📎 مرفقات: {added['attachments']}\n👥 مستخدمون: {added['users']}")
