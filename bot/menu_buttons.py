import json
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from .config import settings
from .db import MenuButton, get_setting

MENU_TITLES = {
    "main": "القائمة الرئيسية للمستخدم",
    "developer": "لوحة المطوّر",
    "subject_type": "اختيار النظري/العملي",
    "sections": "قائمة أقسام المادة",
    "content": "قائمة المحاضرات والمحتوى",
    "admin_subjects": "قائمة إدارة المواد",
    "subject_details": "أزرار تفاصيل المادة",
    "admin_content": "قائمة محتوى الإدارة",
    "admin_item": "تفاصيل المحتوى في الإدارة",
}

ACTION_CALLBACKS = {
    "home": "home",
    "search": "search",
    "quiz": "quiz:start",
    "admin_panel": "adm:panel",
    "stats": "adm:stats",
    "users": "adm:users",
    "add_content": "adm:add",
    "manage_subjects": "adm:subjects",
    "broadcast": "adm:broadcast",
    "settings": "adm:settings",
    "force_sub": "adm:forcesub",
    "alerts": "adm:alerts",
    "announce": "adm:announce",
    "view_subjects": "adm:home",
    "add_subject": "adm:addsubject",
    "manage_buttons": "adm:buttons",
}
ACTION_LABELS = {
    "home": "🏠 الرئيسية",
    "search": "🔎 بحث في المحاضرات",
    "quiz": "🤖 إنشاء اختبار",
    "admin_panel": "🛠 لوحة المطوّر",
    "stats": "📊 الإحصائيات",
    "users": "👥 المستخدمون",
    "add_content": "➕ إضافة محتوى",
    "manage_subjects": "📚 إدارة المواد",
    "broadcast": "📢 إذاعة",
    "settings": "⚙️ الإعدادات",
    "force_sub": "🔒 الاشتراك الإجباري",
    "alerts": "🔔 إشعارات الأعضاء",
    "announce": "📣 السماح بالإذاعة",
    "view_subjects": "📖 عرض المواد",
    "add_subject": "➕ إضافة مادة",
    "manage_buttons": "⚙️ إدارة الأزرار",
    "url": "🔗 فتح رابط خارجي",
    "message": "💬 عرض رسالة",
}
ADMIN_ACTIONS = set(ACTION_CALLBACKS) - {"home", "search", "quiz"}

async def hidden_keys(session, menu_key: str) -> set[str]:
    try:
        data = json.loads(await get_setting(session, f"menu_hidden:{menu_key}", "[]"))
        return {str(value) for value in data} if isinstance(data, list) else set()
    except (TypeError, ValueError):
        return set()

async def save_hidden_keys(session, menu_key: str, values: set[str]) -> None:
    from .db import set_setting
    await set_setting(session, f"menu_hidden:{menu_key}", json.dumps(sorted(values), ensure_ascii=False))

async def build_menu_markup(session, menu_key: str, entries: list[dict], user_id: int, columns: int = 2) -> InlineKeyboardMarkup:
    hidden = await hidden_keys(session, menu_key)
    visible = []
    for entry in entries:
        key = str(entry.get("key", ""))
        if key in hidden:
            continue
        if entry.get("admin_only") and user_id not in settings.owner_ids:
            continue
        visible.append(dict(entry))
    custom_rows = (await session.scalars(
        select(MenuButton).where(MenuButton.menu_key == menu_key, MenuButton.is_active.is_(True))
        .order_by(MenuButton.sort_order, MenuButton.id)
    )).all()
    custom = []
    for row in custom_rows:
        if menu_key == "developer" and user_id not in settings.owner_ids:
            continue
        if row.action in ADMIN_ACTIONS and user_id not in settings.owner_ids:
            continue
        if row.action == "url":
            if row.value.startswith(("https://", "http://")):
                custom.append({"key": f"custom_{row.id}", "text": row.label, "url": row.value, "group": "custom"})
        elif row.action == "message":
            custom.append({"key": f"custom_{row.id}", "text": row.label, "callback": f"mb:{row.id}", "group": "custom"})
        elif row.action in ACTION_CALLBACKS:
            custom.append({"key": f"custom_{row.id}", "text": row.label, "callback": ACTION_CALLBACKS[row.action], "group": "custom", "admin_only": row.action in ADMIN_ACTIONS})
    # Place custom buttons before the footer/back controls, keeping sections visually separate.
    footer_index = next((i for i, x in enumerate(visible) if x.get("group") == "footer"), len(visible))
    visible[footer_index:footer_index] = custom
    rows, current, previous_group = [], [], None
    for entry in visible:
        group = entry.get("group", "default")
        if current and (group != previous_group or entry.get("wide")):
            rows.append(current)
            current = []
        button = InlineKeyboardButton(
            text=str(entry.get("text", "زر"))[:64],
            url=entry["url"] if entry.get("url") else None,
            callback_data=None if entry.get("url") else str(entry.get("callback", "home"))[:64],
        )
        if entry.get("wide"):
            if current:
                rows.append(current)
                current = []
            rows.append([button])
        else:
            current.append(button)
            if len(current) >= max(1, columns):
                rows.append(current)
                current = []
        previous_group = group
    if current:
        rows.append(current)
    return InlineKeyboardMarkup(inline_keyboard=rows)
