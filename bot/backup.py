import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import FSInputFile
from sqlalchemy import select

from .config import settings
from .db import (
    Admin, Attachment, BotSetting, Item, Section, Session, Subject, User, utcnow,
    Quiz, QuizQuestion, QuizAttempt, QuizPoll, QuizResponse, QuizSession,
    QuizSessionPoll, QuizSessionAnswer, QuizTarget, GroupSettings, GroupQuizSchedule, set_setting,
)

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
        quizzes = (await rows(Quiz)).all()
        questions = (await rows(QuizQuestion)).all()
        attempts = (await rows(QuizAttempt)).all()
        private_polls = (await rows(QuizPoll)).all()
        responses = (await rows(QuizResponse)).all()
        quiz_sessions = (await rows(QuizSession)).all()
        session_polls = (await rows(QuizSessionPoll)).all()
        session_answers = (await rows(QuizSessionAnswer)).all()
        targets = (await rows(QuizTarget)).all()
        group_settings = (await rows(GroupSettings)).all()
        schedules = (await rows(GroupQuizSchedule)).all()
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
        "quizzes": [{"id": x.id, "owner_telegram_id": x.owner_telegram_id, "title": x.title,
                     "original_text": x.original_text, "created_at": _dt(x.created_at)} for x in quizzes],
        "quiz_questions": [{"id": x.id, "quiz_id": x.quiz_id, "number": x.number, "question": x.question,
                            "options_json": x.options_json, "correct_option_index": x.correct_option_index,
                            "explanation": x.explanation} for x in questions],
        "quiz_attempts": [{"id": x.id, "quiz_id": x.quiz_id, "user_telegram_id": x.user_telegram_id,
                           "total_questions": x.total_questions, "answered_count": x.answered_count,
                           "correct_count": x.correct_count, "status": x.status,
                           "started_at": _dt(x.started_at), "finished_at": _dt(x.finished_at)} for x in attempts],
        "quiz_polls": [{"poll_id": x.poll_id, "attempt_id": x.attempt_id,
                        "question_number": x.question_number, "correct_option_index": x.correct_option_index} for x in private_polls],
        "quiz_responses": [{"id": x.id, "attempt_id": x.attempt_id, "question_number": x.question_number,
                            "selected_option_index": x.selected_option_index, "is_correct": x.is_correct,
                            "answered_at": _dt(x.answered_at)} for x in responses],
        "quiz_sessions": [{"id": x.id, "quiz_id": x.quiz_id, "chat_id": x.chat_id, "chat_title": x.chat_title,
                           "started_by": x.started_by, "total_questions": x.total_questions, "status": x.status,
                           "control_message_id": x.control_message_id, "thread_id": x.thread_id,
                           "paused_at": _dt(x.paused_at), "ends_at": _dt(x.ends_at),
                           "settings_json": x.settings_json, "started_at": _dt(x.started_at),
                           "finished_at": _dt(x.finished_at)} for x in quiz_sessions],
        "quiz_session_polls": [{"poll_id": x.poll_id, "session_id": x.session_id,
                                "question_number": x.question_number, "correct_option_index": x.correct_option_index,
                                "message_id": x.message_id} for x in session_polls],
        "quiz_session_answers": [{"id": x.id, "session_id": x.session_id, "user_telegram_id": x.user_telegram_id,
                                  "user_name": x.user_name, "question_number": x.question_number,
                                  "selected_option_index": x.selected_option_index, "is_correct": x.is_correct,
                                  "answered_at": _dt(x.answered_at)} for x in session_answers],
        "quiz_targets": [{"chat_id": x.chat_id, "kind": x.kind, "title": x.title, "username": x.username,
                          "added_by": x.added_by, "is_active": x.is_active, "created_at": _dt(x.created_at)} for x in targets],
        "group_settings": [{"chat_id": x.chat_id, "title": x.title, "enabled": x.enabled,
                            "start_policy": x.start_policy, "allowed_admin_ids_json": x.allowed_admin_ids_json,
                            "open_period": x.open_period, "duration_seconds": x.duration_seconds,
                            "shuffle_questions": x.shuffle_questions, "shuffle_options": x.shuffle_options,
                            "max_attempts": x.max_attempts, "explanation_mode": x.explanation_mode,
                            "anonymous_mode": x.anonymous_mode, "leaderboard_limit": x.leaderboard_limit,
                            "timezone": x.timezone, "created_at": _dt(x.created_at),
                            "updated_at": _dt(x.updated_at)} for x in group_settings],
        "group_quiz_schedules": [{"id": x.id, "chat_id": x.chat_id, "quiz_id": x.quiz_id,
                                  "thread_id": x.thread_id, "requested_by": x.requested_by,
                                  "run_at": _dt(x.run_at), "repeat_mode": x.repeat_mode,
                                  "timezone": x.timezone, "is_active": x.is_active,
                                  "last_run_at": _dt(x.last_run_at), "created_at": _dt(x.created_at)} for x in schedules],
    }


async def make_backup() -> str:
    data = await build_backup_data()
    local_now = datetime.now(ZoneInfo(settings.timezone))
    path = os.path.join(tempfile.gettempdir(), f"steyat_backup_{local_now:%Y%m%d_%H%M%S}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return path



def _parse_dt(value):
    if not value:
        return None
    return datetime.fromisoformat(value)


async def _restore_group_quiz_data(s, data: dict, added: dict) -> None:
    """Restore quiz entities additively while remapping source IDs to local IDs."""
    quiz_map = {}
    for r in data.get("quizzes", []):
        row = await s.scalar(select(Quiz).where(
            Quiz.owner_telegram_id == r.get("owner_telegram_id", 0),
            Quiz.title == r.get("title", ""), Quiz.original_text == r.get("original_text", "")))
        if row is None:
            row = Quiz(owner_telegram_id=r.get("owner_telegram_id", 0), title=r.get("title", "اختبار"),
                       original_text=r.get("original_text", ""), created_at=_parse_dt(r.get("created_at")) or utcnow())
            s.add(row)
            await s.flush()
            added["quizzes"] += 1
        quiz_map[r.get("id")] = row.id

    for r in data.get("quiz_questions", []):
        quiz_id = quiz_map.get(r.get("quiz_id"))
        if not quiz_id:
            continue
        exists = await s.scalar(select(QuizQuestion.id).where(
            QuizQuestion.quiz_id == quiz_id, QuizQuestion.number == r.get("number")))
        if not exists:
            s.add(QuizQuestion(quiz_id=quiz_id, number=r["number"], question=r["question"],
                options_json=r["options_json"], correct_option_index=r["correct_option_index"],
                explanation=r.get("explanation")))
            added["quiz_questions"] += 1
    await s.flush()

    attempt_map = {}
    for r in data.get("quiz_attempts", []):
        quiz_id = quiz_map.get(r.get("quiz_id"))
        if not quiz_id:
            continue
        row = await s.scalar(select(QuizAttempt).where(
            QuizAttempt.quiz_id == quiz_id, QuizAttempt.user_telegram_id == r.get("user_telegram_id"),
            QuizAttempt.started_at == (_parse_dt(r.get("started_at")) or utcnow())))
        if row is None:
            row = QuizAttempt(quiz_id=quiz_id, user_telegram_id=r["user_telegram_id"],
                total_questions=r.get("total_questions", 0), answered_count=r.get("answered_count", 0),
                correct_count=r.get("correct_count", 0), status=r.get("status", "active"),
                started_at=_parse_dt(r.get("started_at")) or utcnow(),
                finished_at=_parse_dt(r.get("finished_at")))
            s.add(row); await s.flush(); added["quiz_attempts"] += 1
        attempt_map[r.get("id")] = row.id
    for r in data.get("quiz_polls", []):
        attempt_id = attempt_map.get(r.get("attempt_id"))
        if attempt_id and await s.get(QuizPoll, r["poll_id"]) is None:
            s.add(QuizPoll(poll_id=r["poll_id"], attempt_id=attempt_id,
                question_number=r["question_number"], correct_option_index=r["correct_option_index"]))
            added["quiz_polls"] += 1
    for r in data.get("quiz_responses", []):
        attempt_id = attempt_map.get(r.get("attempt_id"))
        if not attempt_id:
            continue
        exists = await s.scalar(select(QuizResponse.id).where(
            QuizResponse.attempt_id == attempt_id, QuizResponse.question_number == r["question_number"]))
        if not exists:
            s.add(QuizResponse(attempt_id=attempt_id, question_number=r["question_number"],
                selected_option_index=r["selected_option_index"], is_correct=r.get("is_correct", False),
                answered_at=_parse_dt(r.get("answered_at")) or utcnow()))
            added["quiz_responses"] += 1
    await s.flush()

    session_map = {}
    for r in data.get("quiz_sessions", []):
        quiz_id = quiz_map.get(r.get("quiz_id"))
        if not quiz_id:
            continue
        row = await s.scalar(select(QuizSession).where(
            QuizSession.chat_id == r.get("chat_id"), QuizSession.quiz_id == quiz_id,
            QuizSession.started_at == (_parse_dt(r.get("started_at")) or utcnow())))
        if row is None:
            row = QuizSession(quiz_id=quiz_id, chat_id=r["chat_id"], chat_title=r.get("chat_title", ""),
                started_by=r.get("started_by", 0), total_questions=r.get("total_questions", 0),
                status=r.get("status", "closed"), control_message_id=r.get("control_message_id"),
                thread_id=r.get("thread_id"), paused_at=_parse_dt(r.get("paused_at")),
                ends_at=_parse_dt(r.get("ends_at")), settings_json=r.get("settings_json", "{}"),
                started_at=_parse_dt(r.get("started_at")) or utcnow(),
                finished_at=_parse_dt(r.get("finished_at")))
            s.add(row); await s.flush(); added["quiz_sessions"] += 1
        session_map[r.get("id")] = row.id
    for r in data.get("quiz_session_polls", []):
        session_id = session_map.get(r.get("session_id"))
        if session_id and await s.get(QuizSessionPoll, r["poll_id"]) is None:
            s.add(QuizSessionPoll(poll_id=r["poll_id"], session_id=session_id,
                question_number=r["question_number"], correct_option_index=r["correct_option_index"],
                message_id=r["message_id"]))
            added["quiz_session_polls"] += 1
    for r in data.get("quiz_session_answers", []):
        session_id = session_map.get(r.get("session_id"))
        if not session_id:
            continue
        exists = await s.scalar(select(QuizSessionAnswer.id).where(
            QuizSessionAnswer.session_id == session_id,
            QuizSessionAnswer.user_telegram_id == r["user_telegram_id"],
            QuizSessionAnswer.question_number == r["question_number"]))
        if not exists:
            s.add(QuizSessionAnswer(session_id=session_id, user_telegram_id=r["user_telegram_id"],
                user_name=r.get("user_name", ""), question_number=r["question_number"],
                selected_option_index=r["selected_option_index"], is_correct=r.get("is_correct", False),
                answered_at=_parse_dt(r.get("answered_at")) or utcnow()))
            added["quiz_session_answers"] += 1
    for r in data.get("quiz_targets", []):
        if await s.scalar(select(QuizTarget.id).where(QuizTarget.chat_id == r["chat_id"])) is None:
            s.add(QuizTarget(chat_id=r["chat_id"], kind=r.get("kind", "group"), title=r.get("title", ""),
                username=r.get("username"), added_by=r.get("added_by", 0),
                is_active=r.get("is_active", True), created_at=_parse_dt(r.get("created_at")) or utcnow()))
            added["quiz_targets"] += 1
    for r in data.get("group_settings", []):
        if await s.get(GroupSettings, r["chat_id"]) is None:
            fields = {k: r[k] for k in ("title", "enabled", "start_policy", "allowed_admin_ids_json",
                "open_period", "duration_seconds", "shuffle_questions", "shuffle_options", "max_attempts",
                "explanation_mode", "anonymous_mode", "leaderboard_limit", "timezone") if k in r}
            s.add(GroupSettings(chat_id=r["chat_id"], **fields))
            added["group_settings"] += 1
    for r in data.get("group_quiz_schedules", []):
        quiz_id = quiz_map.get(r.get("quiz_id"))
        if not quiz_id:
            continue
        run_at = _parse_dt(r.get("run_at"))
        if not run_at:
            continue
        exists = await s.scalar(select(GroupQuizSchedule.id).where(
            GroupQuizSchedule.chat_id == r["chat_id"], GroupQuizSchedule.quiz_id == quiz_id,
            GroupQuizSchedule.run_at == run_at))
        if not exists:
            s.add(GroupQuizSchedule(chat_id=r["chat_id"], quiz_id=quiz_id,
                thread_id=r.get("thread_id"), requested_by=r.get("requested_by", 0), run_at=run_at,
                repeat_mode=r.get("repeat_mode", "none"), timezone=r.get("timezone", "Asia/Baghdad"),
                is_active=r.get("is_active", False), last_run_at=_parse_dt(r.get("last_run_at")),
                created_at=_parse_dt(r.get("created_at")) or utcnow()))
            added["group_quiz_schedules"] += 1


async def restore_backup(data: dict) -> dict:
    """Additive restore: only inserts what is missing. Never deletes or overwrites existing rows."""
    if not isinstance(data, dict) or "items" not in data:
        raise ValueError("ملف النسخة الاحتياطية غير صالح")
    added = {"subjects": 0, "items": 0, "attachments": 0, "users": 0, "quizzes": 0, "quiz_questions": 0, "quiz_attempts": 0, "quiz_polls": 0, "quiz_responses": 0, "quiz_sessions": 0, "quiz_session_polls": 0, "quiz_session_answers": 0, "quiz_targets": 0, "group_settings": 0, "group_quiz_schedules": 0}
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
        await _restore_group_quiz_data(s, data, added)
        await s.commit()
    return added


async def backup_loop(bot: Bot):
    if not settings.backup_chat_id:
        return
    last_day = None
    while True:
        try:
            await asyncio.sleep(1800)
            now = datetime.now(ZoneInfo(settings.timezone))
            if now.hour == settings.backup_hour and last_day != now.date():
                path = await make_backup()
                try:
                    await bot.send_document(
                        settings.backup_chat_id,
                        FSInputFile(path),
                        caption=f"💾 نسخة احتياطية تلقائية — {now:%Y-%m-%d %H:%M} {settings.timezone}",
                    )
                    async with Session() as session:
                        await set_setting(session, "last_backup_at", utcnow().isoformat())
                        await session.commit()
                    last_day = now.date()
                finally:
                    if os.path.exists(path):
                        os.remove(path)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("فشل إرسال النسخة الاحتياطية")
            await asyncio.sleep(60)
