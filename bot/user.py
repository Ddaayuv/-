from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from ..config import settings
from ..db import Subject, Section, Item, get_setting
from ..utils import esc, normalize
from ..menu_buttons import build_menu_markup

router=Router()
class SearchInput(StatesGroup): term=State()
def _two_columns(buttons): return [buttons[i:i+2] for i in range(0,len(buttons),2)]
def subjects_kb(rows, developer=False):
    buttons=[InlineKeyboardButton(text=f"{s.emoji} {s.name_ar}",callback_data=f"sub:{s.id}") for s in rows]
    k=_two_columns(buttons); k.append([InlineKeyboardButton(text="🔎 بحث في المحاضرات",callback_data="search"), InlineKeyboardButton(text="🤖 إنشاء اختبار",callback_data="quiz:start")])
    if developer:k.append([InlineKeyboardButton(text="🛠 لوحة المطوّر",callback_data="adm:panel")])
    return InlineKeyboardMarkup(inline_keyboard=k)
async def show_home(m,session,user_id=None):
    uid=user_id if user_id is not None else m.from_user.id
    rows=(await session.scalars(select(Subject).where(Subject.is_active.is_(True)).order_by(Subject.sort_order,Subject.id))).all()
    entries=[{"key":f"subject_{s.id}","text":f"{s.emoji} {s.name_ar}","callback":f"sub:{s.id}"} for s in rows]
    entries += [{"key":"search","text":"🔎 بحث في المحاضرات","callback":"search"},{"key":"quiz","text":"🤖 إنشاء اختبار","callback":"quiz:start"}]
    if uid in settings.owner_ids: entries.append({"key":"admin_panel","text":"🛠 لوحة المطوّر","callback":"adm:panel","admin_only":True})
    markup=await build_menu_markup(session,"main",entries,uid,columns=2)
    if not rows:return await m.answer("📚 لا توجد مواد متاحة حالياً.",reply_markup=markup)
    await m.answer("🎓 <b>المواد الدراسية 📚</b>\n\nاختر المادة:",reply_markup=markup)
@router.message(CommandStart())
async def start(m,session):
    if m.from_user.id in settings.owner_ids:
        from .admin import panel_kb
        return await m.answer("🛠 <b>مرحباً بك أيها المطوّر</b>\n\nتم التعرف عليك تلقائياً. هذه لوحة التحكم:",reply_markup=panel_kb())
    await show_home(m,session,m.from_user.id)
@router.message(Command("menu"))
async def menu(m,session): await show_home(m,session,m.from_user.id)
@router.callback_query(F.data=="check_sub")
async def check_sub(cb,session):
    channel=(await get_setting(session,"channel_id","")).strip(); link=(await get_setting(session,"channel_link","")).strip()
    try:
        chat=int(channel) if channel.lstrip("-").isdigit() else (channel if channel.startswith("@") else "@"+channel)
        member=await cb.bot.get_chat_member(chat,cb.from_user.id)
        allowed=member.status in {"member","administrator","creator"} or (member.status=="restricted" and getattr(member,"is_member",False))
        if allowed:
            await cb.answer("تم التحقق ✅"); await show_home(cb.message,session,cb.from_user.id)
        else: await cb.answer("لم يتم العثور على اشتراكك بعد",show_alert=True)
    except Exception: await cb.answer("تعذر التحقق. تأكد أن البوت مشرف بالقناة.",show_alert=True)
@router.callback_query(F.data=="search")
async def search_button(cb,state):
    await state.set_state(SearchInput.term); await cb.message.answer("🔎 أرسل كلمة البحث الآن، أو اكتب /cancel للإلغاء."); await cb.answer()
@router.message(Command("search"))
async def search_command(m,state,session):
    term=(m.text or "").partition(" ")[2].strip()
    if not term: await state.set_state(SearchInput.term); return await m.answer("🔎 اكتب كلمة البحث الآن.")
    await run_search(m,session,term)
@router.message(SearchInput.term)
async def search_state(m,state,session):
    if (m.text or "").strip().startswith("/cancel"): await state.clear(); return await m.answer("تم إلغاء البحث.")
    term=(m.text or "").strip()
    if not term:return await m.answer("أرسل كلمة أو عبارة للبحث.")
    await run_search(m,session,term); await state.clear()
async def run_search(m,session,term):
    needle=normalize(term)
    rows=(await session.scalars(select(Item).join(Section,Item.section_id==Section.id).where(Item.is_published.is_(True),Item.search_text.contains(needle)).order_by(Item.id.desc()).limit(15))).all()
    if not rows:return await m.answer("لم أجد نتائج مطابقة. جرّب كلمة أقصر.")
    await m.answer("🔎 <b>نتائج البحث</b>",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"📄 {x.title[:50]}",callback_data=f"item:{x.id}")] for x in rows]))
@router.callback_query(F.data.startswith("sub:"))
async def subject(cb,session):
    try:s=await session.get(Subject,int(cb.data.split(":",1)[1]))
    except Exception:s=None
    if not s or not s.is_active:return await cb.answer("المادة غير متاحة",show_alert=True)
    if s.has_practical:
        kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📚 النظري",callback_data=f"mode:{s.id}:theory"),InlineKeyboardButton(text="🧪 العملي",callback_data=f"mode:{s.id}:practical")],[InlineKeyboardButton(text="⬅️ رجوع",callback_data="home")]])
        await cb.message.edit_text(f"{s.emoji} <b>{esc(s.name_ar)}</b>\n\nاختر نوع المحتوى:",reply_markup=kb); return await cb.answer()
    await show_sections(cb,s.id,"general",session)
@router.callback_query(F.data.startswith("mode:"))
async def mode(cb,session):
    try:_,sid,study_mode=cb.data.split(":"); sid=int(sid)
    except Exception:return await cb.answer("الاختيار غير صحيح",show_alert=True)
    await show_sections(cb,sid,study_mode,session)
async def show_sections(event,sid,study_mode,session):
    # Show every section that really contains published content, so nothing stored in the DB is hidden.
    q=select(Section,func.count(Item.id)).join(Item,Item.section_id==Section.id).where(Item.subject_id==sid,Item.is_published.is_(True))
    if study_mode!="general":q=q.where(Item.study_mode.in_((study_mode,"general")))
    rows=(await session.execute(q.group_by(Section.id).order_by(Section.sort_order,Section.id))).all()
    back=[InlineKeyboardButton(text="⬅️ المواد",callback_data="home")]
    if not rows:
        await event.message.edit_text("لا يوجد محتوى في هذه المادة بعد.",reply_markup=InlineKeyboardMarkup(inline_keyboard=[back])); return await event.answer()
    k=_two_columns([InlineKeyboardButton(text=("🧰 التطبيقات المستخدمة" if x.slug=="practical" else f"{x.emoji} {x.name_ar}")+f" ({n})",callback_data=f"sec:{sid}:{study_mode}:{x.id}") for x,n in rows])
    k.append(back)
    await event.message.edit_text("اختر القسم الذي تريد فتحه:",reply_markup=InlineKeyboardMarkup(inline_keyboard=k)); await event.answer()
@router.callback_query(F.data.startswith("sec:"))
async def section(cb,session):
    try:_,sid,study_mode,section_id=cb.data.split(":"); sid=int(sid); section_id=int(section_id)
    except Exception:return await cb.answer("الاختيار غير صحيح",show_alert=True)
    selected=await session.get(Section,section_id)
    if not selected:return await cb.answer("القسم غير موجود",show_alert=True)
    q=select(Item).where(Item.subject_id==sid,Item.section_id==section_id,Item.is_published.is_(True))
    if study_mode!="general":q=q.where(Item.study_mode.in_((study_mode,"general")))
    rows=(await session.scalars(q.order_by(Item.sort_order,Item.id))).all()
    if not rows:return await cb.answer("لا يوجد محتوى بهذا القسم بعد",show_alert=True)
    k=[[InlineKeyboardButton(text=("⭐ " if x.is_important else "📄 ")+x.title[:50],callback_data=f"item:{x.id}")] for x in rows]; k.append([InlineKeyboardButton(text="⬅️ رجوع",callback_data=f"sub:{sid}")])
    await cb.message.edit_text("📚 <b>المحتوى المتاح</b>\n\nاختر عنوان المحاضرة أو الملف:",reply_markup=InlineKeyboardMarkup(inline_keyboard=k)); await cb.answer()
@router.callback_query(F.data.startswith("item:"))
async def item(cb,session):
    try:x=await session.get(Item,int(cb.data.split(":",1)[1]))
    except Exception:x=None
    if not x or not x.is_published:return await cb.answer("المحتوى غير متاح",show_alert=True)
    body=f"📘 <b>{esc(x.title)}</b>"+(f"\n\n{esc(x.description)}" if x.description else "")
    await cb.message.answer(body)
    for a in x.attachments:
        cap=esc(a.file_name or x.title)[:900]
        try:
            if a.kind=="file" and a.tg_file_id:await cb.message.answer_document(a.tg_file_id,caption=cap)
            elif a.kind=="video" and a.tg_file_id:await cb.message.answer_video(a.tg_file_id,caption=cap)
            elif a.kind=="audio" and a.tg_file_id:await cb.message.answer_audio(a.tg_file_id,caption=cap)
            elif a.kind=="voice" and a.tg_file_id:await cb.message.answer_voice(a.tg_file_id,caption=cap)
            elif a.kind=="photo" and a.tg_file_id:await cb.message.answer_photo(a.tg_file_id,caption=cap)
            elif a.url:await cb.message.answer(a.url)
        except Exception:await cb.message.answer(f"⚠️ تعذّر إرسال المرفق: {cap}")
    await cb.answer()
@router.callback_query(F.data=="home")
async def home(cb,session):
    if cb.from_user.id in settings.owner_ids:
        from .admin import panel_kb
        await cb.message.edit_text("🛠 <b>لوحة المطوّر</b>",reply_markup=panel_kb())
    else:
        rows=(await session.scalars(select(Subject).where(Subject.is_active.is_(True)).order_by(Subject.sort_order,Subject.id))).all()
        await cb.message.edit_text("🎓 <b>المواد الدراسية 📚</b>\n\nاختر المادة:",reply_markup=subjects_kb(rows,False))
    await cb.answer()
