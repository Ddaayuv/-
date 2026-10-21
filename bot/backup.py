import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime

from aiogram import Bot
from aiogram.types import FSInputFile
from sqlalchemy import select

from .config import settings
from .db import (Admin, Attachment, BotSetting, Item, Section, Session, Subject, User, utcnow)

log = logging.getLogger(__name__)
SECRET_WORDS = ("token", "secret", "password", "key_")


def _dt(v):
    return v.isoformat() if v else None


async def build_backup_data() -> dict:
    async with Session() as s:
        rows = lambda model: s.scalars(select(model))
        users = (await rows(User)).all()
        subjects = (await rows(Subject)).all()
        sections = (await rows(Section)).all()
        items = (await rows(Item)).all()
        atts = (await rows(Attachment)).all()
        admins = (await rows(Admin)).all()
        sets = (await rows(BotSetting)).all()
    return {
        "version": 2,
        "created_at": datetime.utcnow().isoformat(),
        "users": [{"telegram_id": u.telegram_id, "name": u.name, "username": u.username} for u in users],
        "subjects": [{"id": x.id, "slug": x.slug, "name_ar": x.name_ar, "name_en": x.name_en, "emoji": x.emoji,
                      "aliases": x.aliases, "sort_order": x.sort_order, "is_active": x.is_active,
                      "has_practical": x.has_practical} for x in subjects],
        "sections": [{"id": x.id, "slug": x.slug, "name_ar": x.name_ar, "emoji": x.emoji, "aliases": x.aliases,
                      "sort_order": x.sort_order} for x in sections],
        "items": [{"id": x.id, "subject_id": x.subject_id, "section_id": x.section_id, "title": x.title,
                   "description": x.description, "week_no": x.week_no, "sort_order": x.sort_order, "tags": x.tags,
                   "is_important": x.is_important, "is_published": x.is_published, "study_mode": x.study_mode,
                   "created_by": x.created_by, "created_at": _dt(x.created_at)} for x in items],
        "attachments": [{"id": a.id, "item_id": a.item_id, "kind": a.kind, "url": a.url, "tg_file_id": a.tg_file_id,
                         "file_name": a.file_name, "size": a.size} for a in atts],
        "admins": [{"telegram_id": a.telegram_id, "role": a.role} for a in admins],
        "settings": [{"key": x.key, "value": x.value} for x in sets
                     if not any(w in x.key.lower() for w in SECRET_WORDS)],
    }


async def make_backup() -> str:
    data = await build_backup_data()
    path = os.path.join(tempfile.gettempdir(), f"steyat_backup_{datetime.now():%Y%m%d_%H%M%S}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return path


async def restore_backup(data: dict) -> dict:
    """Additive restore: only inserts what is missing. Never deletes or overwrites existing rows."""
    if not isinstance(data, dict) or "items" not in data:
        raise ValueError("ملف النسخة الاحتياطية غير صالح")
    added = {"subjects": 0, "items": 0, "attachments": 0, "users": 0}
    async with Session() as s:
        subj_map, sect_map = {}, {}
        for r in data.get("subjects", []):
            row = await s.scalar(select(Subject).where(Subject.slug == r.get("slug", "")))
            if row is None:
                row = Subject(slug=r["slug"], name_ar=r.get("name_ar") or r.get("name", ""), name_en=r.get("name_en", ""),
                              emoji=r.get("emoji", "📘"), aliases=r.get("aliases", ""), sort_order=r.get("sort_order", 0),
                              is_active=r.get("is_active", True), has_practical=r.get("has_practical", r.get("practical", False)))
                s.add(row); await s.flush(); added["subjects"] += 1
            subj_map[r["id"]] = row.id
        for r in data.get("sections", []):
            row = await s.scalar(select(Section).where(Section.slug == r["slug"]))
            if row is not None:
                sect_map[r["id"]] = row.id
        atts_by_item = {}
        for a in data.get("attachments", []):
            atts_by_item.setdefault(a["item_id"], []).append(a)
        for r in data["items"]:
            sid, sec = subj_map.get(r["subject_id"]), sect_map.get(r["section_id"])
            if sid is None or sec is None:
                continue
            exists = await s.scalar(select(Item.id).where(Item.subject_id == sid, Item.section_id == sec,
                                                          Item.title == r["title"], Item.study_mode == r.get("study_mode", r.get("mode", "general"))))
            if exists:
                continue
            subject, section = await s.get(Subject, sid), await s.get(Section, sec)
            from .db import build_search_text
            item = Item(subject_id=sid, section_id=sec, title=r["title"], description=r.get("description", ""),
                        week_no=r.get("week_no"), sort_order=r.get("sort_order", 0), tags=r.get("tags", ""),
                        is_important=r.get("is_important", False), is_published=r.get("is_published", r.get("published", True)),
                        study_mode=r.get("study_mode", r.get("mode", "general")), created_by=r.get("created_by", 0))
            item.attachments = [Attachment(kind=a["kind"], url=a.get("url"), tg_file_id=a.get("tg_file_id"),
                                           file_name=a.get("file_name"), size=a.get("size"))
                                for a in atts_by_item.get(r["id"], [])]
            item.search_text = build_search_text(item, subject, section)
            s.add(item); added["items"] += 1; added["attachments"] += len(item.attachments)
        for u in data.get("users", []):
            if not await s.scalar(select(User.id).where(User.telegram_id == u["telegram_id"])):
                s.add(User(telegram_id=u["telegram_id"], name=u.get("name", ""), username=u.get("username")))
                added["users"] += 1
        await s.commit()
    return added


async def backup_loop(bot: Bot):
    if not settings.backup_chat_id:
        return
    last_day = None
    while True:
        try:
            await asyncio.sleep(1800)
            now = datetime.now()
            if now.hour == settings.backup_hour and last_day != now.date():
                path = await make_backup()
                await bot.send_document(settings.backup_chat_id, FSInputFile(path), caption="💾 نسخة احتياطية تلقائية")
                os.remove(path)
                last_day = now.date()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("فشل إرسال النسخة الاحتياطية")
            await asyncio.sleep(60)
