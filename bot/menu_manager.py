import json
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from ..config import settings
from ..db import Item, MenuButton, Section, Subject, audit
from ..menu_buttons import ACTION_CALLBACKS, ACTION_LABELS, MENU_TITLES, hidden_keys, save_hidden_keys
from ..utils import esc, is_url

router = Router()

class ButtonWizard(StatesGroup):
    label = State()
    action = State()
    value = State()

def _owner(user_id: int) -> bool:
    return user_id in settings.owner_ids

def _layout(buttons, width=2):
    return [buttons[i:i+width] for i in range(0, len(buttons), width)]

async def _catalog(session, menu_key: str):
    entries = []
    def add(key, label, admin_only=False):
        entries.append((key, label, admin_only))
    if menu_key == "main":
        subjects = (await session.scalars(select(Subject).where(Subject.is_active.is_(True)).order_by(Subject.sort_order, Subject.id).limit(25))).all()
        for s in subjects: add(f"subject_{s.id}", f"{s.emoji} {s.name_ar}")
        add("search", "🔎 بحث في المحاضرات")
        add("quiz", "🤖 إنشاء اختبار")
        add("admin_panel", "🛠 لوحة المطوّر", True)
    elif menu_key == "developer":
        for key, label in [
            ("stats","📊 الإحصائيات"),("users","👥 المستخدمون"),("add_content","➕ إضافة محتوى"),
            ("manage_subjects","📚 إدارة المواد"),("broadcast","📢 إذاعة"),("settings","⚙️ الإعدادات"),
            ("force_sub","🔒 الاشتراك الإجباري"),("alerts","🔔 إشعارات الأعضاء"),
            ("announce","📣 السماح بالإذاعة"),("view_subjects","📖 عرض المواد"),
            ("quiz","🤖 إنشاء اختبار"),("manage_buttons","⚙️ إدارة الأزرار"),
        ]: add(key, label, True)
    elif menu_key == "subject_type":
        add("theory", "📚 النظري"); add("practical", "🧪 العملي"); add("back", "⬅️ رجوع")
    elif menu_key == "sections":
        sections = (await session.scalars(select(Section).order_by(Section.sort_order, Section.id))).all()
        for s in sections: add(f"section_{s.slug}", f"{s.emoji} {s.name_ar}")
        add("back", "⬅️ المواد")
    elif menu_key == "content":
        items = (await session.scalars(select(Item).where(Item.is_published.is_(True)).order_by(Item.id.desc()).limit(20))).all()
        for x in items: add(f"item_{x.id}", f"📄 #{x.id} {x.title[:35]}")
        add("back", "⬅️ رجوع")
    elif menu_key == "admin_subjects":
        subjects = (await session.scalars(select(Subject).order_by(Subject.sort_order, Subject.id).limit(25))).all()
        for s in subjects: add(f"admin_subject_{s.id}", f"{s.emoji} {s.name_ar}", True)
        add("add_subject", "➕ إضافة مادة", True); add("back", "⬅️ اللوحة", True)
    elif menu_key == "subject_details":
        add("rename_subject", "✏️ تغيير اسم المادة", True)
        add("toggle_subject", "🔄 تغيير عملي/نظري", True)
        add("visibility_subject", "🙈 إظهار/إخفاء المادة", True)
        add("subject_content", "📦 محتوى المادة", True)
        add("back", "⬅️ المواد", True)
    elif menu_key == "admin_content":
        items = (await session.scalars(select(Item).order_by(Item.id.desc()).limit(20))).all()
        for x in items: add(f"admin_item_{x.id}", f"📦 #{x.id} {x.title[:35]}", True)
        add("back", "⬅️ تفاصيل المادة", True)
    elif menu_key == "admin_item":
        add("item_visibility", "🙈 إخفاء/نشر المحتوى", True)
        add("item_delete", "🗑 حذف المحتوى", True)
        add("back", "⬅️ رجوع", True)
    return entries

async def _show_menu(cb: CallbackQuery, session, menu_key: str):
    if menu_key not in MENU_TITLES:
        return await cb.answer("القائمة غير معروفة", show_alert=True)
    entries = await _catalog(session, menu_key)
    hidden = await hidden_keys(session, menu_key)
    keyboard = []
    for key, label, admin_only in entries:
        icon = "👁️ إظهار" if key in hidden else "🙈 إخفاء"
        keyboard.append([InlineKeyboardButton(text=f"{icon}: {label}"[:64], callback_data=f"btnmgr:toggle:{menu_key}:{key}")])
    custom = (await session.scalars(select(MenuButton).where(MenuButton.menu_key == menu_key).order_by(MenuButton.sort_order, MenuButton.id).limit(20))).all()
    for row in custom:
        keyboard.append([InlineKeyboardButton(text=f"🗑 حذف الزر: {row.label}"[:64], callback_data=f"btnmgr:delete:{row.id}:{menu_key}")])
    keyboard.append([InlineKeyboardButton(text="➕ إضافة زر", callback_data=f"btnmgr:add:{menu_key}")])
    keyboard.append([InlineKeyboardButton(text="⬅️ القوائم", callback_data="adm:buttons")])
    await cb.message.edit_text(
        f"⚙️ <b>{esc(MENU_TITLES[menu_key])}</b>\n\nاضغط على الزر لإخفائه أو إظهاره. الأزرار المخصصة يمكن حذفها نهائياً.\nتغيير الظهور لا يحذف المادة أو المحتوى من قاعدة البيانات.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await cb.answer()

@router.message(Command("buttons"))
async def buttons_command(message: Message, session):
    if not _owner(message.from_user.id):
        return
    await message.answer("⚙️ <b>إدارة الأزرار</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"btnmgr:menu:{key}")]
        for key, label in MENU_TITLES.items()
    ]))

@router.callback_query(F.data == "adm:buttons")
async def button_manager(cb: CallbackQuery):
    if not _owner(cb.from_user.id):
        return await cb.answer("ممنوع", show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"btnmgr:menu:{key}")]
        for key, label in MENU_TITLES.items()
    ] + [[InlineKeyboardButton(text="⬅️ لوحة المطوّر", callback_data="adm:panel")]])
    await cb.message.edit_text("⚙️ <b>إدارة الأزرار</b>\n\nاختر القائمة التي تريد تعديل أزرارها:", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data.startswith("btnmgr:menu:"))
async def manager_open(cb: CallbackQuery, session):
    if not _owner(cb.from_user.id):
        return await cb.answer("ممنوع", show_alert=True)
    await _show_menu(cb, session, cb.data.rsplit(":", 1)[1])

@router.callback_query(F.data.startswith("btnmgr:toggle:"))
async def manager_toggle(cb: CallbackQuery, session):
    if not _owner(cb.from_user.id):
        return await cb.answer("ممنوع", show_alert=True)
    # Callback format is: btnmgr:toggle:<menu_key>:<button_key>
    parts = cb.data.split(":", 3)
    if len(parts) != 4:
        return await cb.answer("الزر غير معروف", show_alert=True)
    menu_key, key = parts[2], parts[3]
    if menu_key not in MENU_TITLES:
        return await cb.answer("القائمة غير معروفة", show_alert=True)
    hidden = await hidden_keys(session, menu_key)
    if key in hidden: hidden.remove(key)
    else: hidden.add(key)
    await save_hidden_keys(session, menu_key, hidden)
    await session.commit()
    await _show_menu(cb, session, menu_key)

@router.callback_query(F.data.startswith("btnmgr:delete:"))
async def manager_delete(cb: CallbackQuery, session):
    if not _owner(cb.from_user.id):
        return await cb.answer("ممنوع", show_alert=True)
    parts = cb.data.split(":")
    if len(parts) != 4:
        return await cb.answer("الزر غير معروف", show_alert=True)
    button_id, menu_key = int(parts[2]), parts[3]
    row = await session.get(MenuButton, button_id)
    if not row or row.menu_key != menu_key:
        return await cb.answer("الزر غير موجود", show_alert=True)
    await session.delete(row)
    await session.commit()
    await _show_menu(cb, session, menu_key)

@router.callback_query(F.data.startswith("btnmgr:add:"))
async def manager_add_start(cb: CallbackQuery, state: FSMContext):
    if not _owner(cb.from_user.id):
        return await cb.answer("ممنوع", show_alert=True)
    menu_key = cb.data.rsplit(":", 1)[1]
    if menu_key not in MENU_TITLES:
        return await cb.answer("القائمة غير معروفة", show_alert=True)
    await state.clear()
    await state.update_data(menu_key=menu_key)
    await state.set_state(ButtonWizard.label)
    await cb.message.answer("✏️ أرسل اسم الزر (بحد أقصى 50 حرفاً)، أو /cancel للإلغاء.")
    await cb.answer()

@router.message(ButtonWizard.label)
async def manager_add_label(message: Message, state: FSMContext):
    if not _owner(message.from_user.id):
        await state.clear()
        return
    label = (message.text or "").strip()
    if label.lower() == "/cancel":
        await state.clear()
        return await message.answer("تم الإلغاء.")
    if not label or len(label) > 50:
        return await message.answer("اسم الزر يجب أن يكون بين 1 و50 حرفاً.")
    await state.update_data(button_label=label)
    await state.set_state(ButtonWizard.action)
    actions = ["home","search","quiz","admin_panel","stats","users","add_content","manage_subjects","broadcast","settings","force_sub","alerts","announce","view_subjects","add_subject","manage_buttons","url","message"]
    rows = _layout([InlineKeyboardButton(text=ACTION_LABELS[action], callback_data=f"btnmgr:action:{action}") for action in actions])
    await message.answer("اختر وظيفة الزر. الوظائف الإدارية محمية بحساب المطوّر:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

@router.callback_query(F.data.startswith("btnmgr:action:"))
async def manager_add_action(cb: CallbackQuery, state: FSMContext, session):
    if not _owner(cb.from_user.id):
        return await cb.answer("ممنوع", show_alert=True)
    action = cb.data.rsplit(":", 1)[1]
    data = await state.get_data()
    menu_key = data.get("menu_key")
    label = data.get("button_label")
    if not menu_key or menu_key not in MENU_TITLES or not label:
        await state.clear()
        return await cb.answer("انتهت جلسة الإضافة؛ حاول من جديد.", show_alert=True)
    if action in {"url", "message"}:
        await state.update_data(button_action=action)
        await state.set_state(ButtonWizard.value)
        prompt = "أرسل رابط https:// أو http://." if action == "url" else "أرسل الرسالة التي تظهر عند ضغط الزر (حتى 1500 حرف)."
        await cb.message.answer(prompt)
        return await cb.answer()
    if action not in ACTION_CALLBACKS:
        return await cb.answer("وظيفة غير مدعومة.", show_alert=True)
    await _save_button(session, cb.from_user.id, menu_key, label, action, "")
    await state.clear()
    await cb.answer("تمت إضافة الزر ✅")
    await _show_menu(cb, session, menu_key)

async def _save_button(session, user_id: int, menu_key: str, label: str, action: str, value: str):
    order = await session.scalar(select(func.max(MenuButton.sort_order)).where(MenuButton.menu_key == menu_key)) or 0
    session.add(MenuButton(menu_key=menu_key, label=label, action=action, value=value, sort_order=order + 1,
                           is_active=True, created_by=user_id))
    await session.commit()

@router.message(ButtonWizard.value)
async def manager_add_value(message: Message, state: FSMContext, session):
    if not _owner(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    menu_key, label, action = data.get("menu_key"), data.get("button_label"), data.get("button_action")
    value = (message.text or "").strip()
    if not menu_key or menu_key not in MENU_TITLES or not label:
        await state.clear()
        return await message.answer("انتهت جلسة الإضافة؛ ابدأ من جديد.")
    if action == "url" and not is_url(value):
        return await message.answer("الرابط غير صحيح. استخدم رابطاً يبدأ بـ https:// أو http://.")
    if action == "message" and (not value or len(value) > 1500):
        return await message.answer("الرسالة يجب أن تكون بين 1 و1500 حرف.")
    await _save_button(session, message.from_user.id, menu_key, label, action, value)
    await state.clear()
    await message.answer("✅ تمت إضافة الزر بنجاح. افتح /buttons لإدارة بقية الأزرار.")

@router.callback_query(F.data.startswith("mb:"))
async def custom_message_button(cb: CallbackQuery, session):
    if not cb.data[3:].isdigit():
        return await cb.answer("زر غير صحيح", show_alert=True)
    row = await session.get(MenuButton, int(cb.data[3:]))
    if not row or not row.is_active or row.action != "message":
        return await cb.answer("هذا الزر غير متاح.", show_alert=True)
    if row.menu_key == "developer" and not _owner(cb.from_user.id):
        return await cb.answer("ممنوع", show_alert=True)
    await cb.message.answer(esc(row.value))
    await cb.answer()
