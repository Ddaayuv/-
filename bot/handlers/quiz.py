"""Quizzes that work in three places:

* the bot (private chat)  — the student solves, gets a score at the end;
* groups / supergroups    — polls are non-anonymous, a leaderboard is built (/quiz, /quizstop);
* channels                — polls are published anonymously (Telegram forbids non-anonymous
                            polls in channels, so there are no per-user results there).

Telegram limits that shape this file: ~20 messages/minute per group or channel (so polls
are paced), 300 chars per question, 100 per option, 200 per explanation.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BufferedInputFile, CallbackQuery, ChatMemberUpdated, InlineKeyboardButton, InlineKeyboardMarkup, Message, PollAnswer)
from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError

from ..config import settings
from ..db import (Quiz, QuizAttempt, QuizPoll, QuizQuestion, QuizResponse, QuizSession, QuizSessionAnswer,
                  QuizSessionPoll, QuizTarget, GroupSettings, GroupQuizSchedule, Session, audit, utcnow)
from ..membership import inspect_channel, status_of
from ..quiz_parser import MAX_EXPLANATION, parse_quiz_text
from ..utils import esc, parse_chat_ref
from ..group_quiz_logic import can_start_quiz, rank_leaderboard, local_schedule_to_utc, next_schedule_time

router = Router()
log = logging.getLogger(__name__)

GROUP_TYPES = {"group", "supergroup"}
PRIVATE_DELAY = 0.4   # Telegram allows ~1 message/second to one user
SHARED_DELAY = 3.2    # ~20 messages/minute in a group or channel
MEDALS = ["🥇", "🥈", "🥉"]

_publishing: set[tuple[int, int]] = set()          # chat ids with a publish in progress
_tasks: set[asyncio.Task] = set()      # keep references so tasks are not garbage collected


class QuizInput(StatesGroup):
    text = State()
    confirm = State()
    add_target = State()


# ----------------------------------------------------------------------------- texts / keyboards

def quiz_prompt() -> str:
    return (
        "🤖 <b>إنشاء اختبار</b>\n\nأرسل الأسئلة كنص مع الخيارات والإجابة الصحيحة، بهذا الشكل:\n\n"
        "<code>السؤال: ما عاصمة العراق؟\nأ) بغداد\nب) البصرة\nج) الموصل\nد) أربيل\n"
        "الإجابة الصحيحة: أ\nالشرح: بغداد هي العاصمة (اختياري)</code>\n\n"
        "• يمكنك إرسال عدة أسئلة في رسالة واحدة (كل سؤال يبدأ بـ «السؤال:» أو برقم).\n"
        "• أول سطر قبل الأسئلة يُستخدم كعنوان للاختبار.\n"
        "• سأعرض معاينة قبل الحفظ ولا أخمّن الإجابات.\n"
        "• بعد المعاينة تستطيع حلّه هنا، أو <b>نشره في قناة أو مجموعة</b> أنت مشرف فيها.\n\n"
        "اكتب /cancel للإلغاء."
    )


GROUP_HELP = (
    "🧩 <b>اختبار داخل المجموعة</b>\n\n"
    "أرسل <code>/quiz</code> ثم الأسئلة بنفس الرسالة، أو اكتب <code>/quiz</code> <b>رداً</b> على رسالة فيها الأسئلة.\n"
    "لتشغيل اختبار محفوظ: <code>/quiz رقم_الاختبار</code>\n\n"
    "• <code>/quizstop</code> إنهاء الاختبار وإعلان الترتيب\n"
    "• <code>/quizresults</code> عرض الترتيب الحالي\n\n"
    "الأوامر للمشرفين فقط. شكل الأسئلة:\n"
    "<code>السؤال: ما عاصمة العراق؟\nأ) بغداد\nب) البصرة\nالإجابة: أ</code>"
)


def _kb(confirm: bool = False) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="❌ إلغاء", callback_data="quiz:cancel")]]
    if confirm:
        rows.insert(0, [InlineKeyboardButton(text="📢 حفظ ونشر في قناة/مجموعة", callback_data="quiz:savepub")])
        rows.insert(0, [InlineKeyboardButton(text="✅ حفظ وحلّه هنا", callback_data="quiz:confirm"),
                        InlineKeyboardButton(text="✏️ تعديل النص", callback_data="quiz:edit")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _preview(parsed: dict) -> str:
    title = parsed.get("title") or "اختبار من النص"
    lines = [f"🧾 <b>معاينة: {esc(title)}</b>"]
    for q in parsed["questions"]:
        lines.append(f"\n<b>{q['number']}. {esc(q['text'])}</b>")
        for opt in q["options"]:
            lines.append(f"{esc(opt['label'])}) {esc(opt['text'])}")
        lines.append(f"<i>الإجابة الصحيحة: {esc(q['options'][q['correct_option_index']]['label'])}</i>")
        if q.get("explanation"):
            lines.append(f"<i>💡 {esc(q['explanation'])}</i>")
    for warning in parsed.get("warnings", []):
        lines.append(f"\n⚠️ {esc(warning)}")
    return "\n".join(lines)


def _problems_text(parsed: dict) -> str:
    lines = ["⚠️ لم أتمكن من اعتماد الاختبار. أصلح الأخطاء التالية ثم أرسل النص كاملاً:"]
    lines.extend("• " + esc(issue) for issue in parsed["issues"][:30])
    if not parsed["issues"]:
        lines.append("• لم أكتشف أسئلة مكتملة.")
    return "\n".join(lines)


def _is_valid(parsed: dict) -> bool:
    return bool(parsed["questions"]) and not parsed["issues"] and parsed["valid_count"] == parsed["detected_count"]


# ----------------------------------------------------------------------------- saving / sending

async def _save_quiz(session, user_id: int, raw: str, parsed: dict) -> Quiz:
    quiz = Quiz(owner_telegram_id=user_id, title=(parsed.get("title") or "اختبار من النص")[:200], original_text=raw)
    for q in parsed["questions"]:
        quiz.questions.append(QuizQuestion(
            number=q["number"], question=q["text"], options_json=json.dumps(q["options"], ensure_ascii=False),
            correct_option_index=q["correct_option_index"], explanation=(q.get("explanation") or None)))
    session.add(quiz)
    await session.commit()
    return quiz


def _poll_payload(question: QuizQuestion, total: int, anonymous: bool) -> dict:
    """Arguments for send_poll. Text is HTML-escaped and sent with parse_mode=HTML explicitly:
    the bot's default parse mode is HTML, so a question like "ما ناتج <script>" would otherwise
    be mangled or rejected by Telegram."""
    options = json.loads(question.options_json)
    payload = {
        "question": esc(f"{question.number}/{total} {question.question}"[:300]),
        "question_parse_mode": "HTML",
        "options": [str(opt["text"])[:100] for opt in options],
        "type": "quiz",
        "is_anonymous": anonymous,
        "correct_option_id": question.correct_option_index,
    }
    if question.explanation:
        payload["explanation"] = esc(question.explanation[:MAX_EXPLANATION])
        payload["explanation_parse_mode"] = "HTML"
    return payload


async def _send_poll(bot: Bot, chat_id: int, payload: dict, *, thread_id: int | None = None) -> Message:
    """send_poll that waits out Telegram flood limits instead of failing the whole quiz."""
    if thread_id is not None:
        payload = {**payload, "message_thread_id": thread_id}
    for _ in range(5):
        try:
            return await bot.send_poll(chat_id=chat_id, **payload)
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 1)
    raise RuntimeError("Telegram kept asking to slow down")


def _publish_error(exc: Exception) -> str:
    t = str(exc).lower()
    if "not enough rights" in t or "have no rights" in t or "need administrator rights" in t:
        return "البوت لا يملك صلاحية نشر الرسائل/الاستطلاعات هناك. فعّلها له في صلاحيات المشرف (للمجموعة: السماح بالاستطلاعات)."
    if "chat not found" in t or "kicked" in t or "forbidden" in t or "not a member" in t:
        return "البوت مطرود من الوجهة أو لم يعد فيها."
    if "poll" in t and "anonymous" in t:
        return "تيليجرام يمنع الاستطلاعات غير المجهولة في القنوات."
    return str(exc)[:200] or "خطأ غير معروف"


async def _publish_attempt(bot: Bot, session, quiz: Quiz, user_id: int) -> None:
    """Private chat: the student solves the quiz here and gets a score."""
    questions = list(quiz.questions)
    attempt = QuizAttempt(quiz_id=quiz.id, user_telegram_id=user_id, total_questions=len(questions),
                          answered_count=0, correct_count=0, status="active")
    session.add(attempt)
    await session.commit()
    sent = 0
    failure = ""
    for question in questions:
        try:
            message = await _send_poll(bot, user_id, _poll_payload(question, len(questions), anonymous=False))
            session.add(QuizPoll(poll_id=message.poll.id, attempt_id=attempt.id,
                                 question_number=question.number, correct_option_index=question.correct_option_index))
            await session.commit()
            sent += 1
        except Exception as exc:
            await session.rollback()
            failure = _publish_error(exc)
            log.exception("Failed to send quiz question to user=%s", user_id)
            break
        await asyncio.sleep(PRIVATE_DELAY)
    if sent != len(questions):
        attempt = await session.get(QuizAttempt, attempt.id)
        attempt.total_questions = sent
        attempt.status = "partial" if sent else "failed"
        await session.commit()
        if not sent:
            await bot.send_message(user_id, f"تعذّر إرسال أسئلة الاختبار ({esc(failure)}). لم يتم احتساب نتيجة.")
        else:
            await bot.send_message(user_id, f"⚠️ أُرسل {sent} من {len(questions)} سؤالاً فقط ({esc(failure)}). سأحسب نتيجتك على ما وصل.")
    else:
        await bot.send_message(user_id, f"✅ بدأ الاختبار #{quiz.id}. أجب عن جميع الاستطلاعات وستظهر نتيجتك بعد آخر إجابة.")


# ----------------------------------------------------------------------------- publishing to groups / channels

async def is_chat_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    if user_id in settings.owner_ids:
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramAPIError:
        return False
    return status_of(member) in {"creator", "administrator"}


async def _launch_publish(bot: Bot, quiz_id: int, chat_id: int, kind: str, title: str,
                          started_by: int, notify_chat_id: int, thread_id: int | None = None) -> str | None:
    """Starts publishing in the background. Returns an error text if it cannot start."""
    lock_key = (chat_id, int(thread_id or 0))
    if lock_key in _publishing:
        return "⏳ هناك نشر جارٍ الآن في هذا الموضوع. انتظر حتى ينتهي."
    if kind != "channel":
        async with Session() as s:
            active = await s.scalar(select(QuizSession).where(
                QuizSession.chat_id == chat_id, (QuizSession.thread_id == thread_id if thread_id is not None else QuizSession.thread_id.is_(None)), QuizSession.status.in_(("publishing", "active", "paused"))
            ).order_by(QuizSession.id.desc()))
            if active:
                limit = timedelta(hours=1) if active.status == "publishing" else timedelta(hours=24)
                if utcnow() - active.started_at > limit:
                    active.status, active.finished_at = "closed", utcnow()
                    await s.commit()
                else:
                    return f"يوجد اختبار نشط في هذه المجموعة (#{active.quiz_id}). أنهِه أولاً بالأمر /quizstop"
    _publishing.add(lock_key)
    task = asyncio.create_task(_publish_task(bot, quiz_id, chat_id, kind, title, started_by, notify_chat_id, thread_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    task.add_done_callback(lambda _task: _publishing.discard(lock_key))
    return None


async def _publish_task(bot, quiz_id, chat_id, kind, title, started_by, notify_chat_id, thread_id=None) -> None:
    try:
        await _publish_to_chat(bot, quiz_id, chat_id, kind, title, started_by, notify_chat_id, thread_id)
    except Exception:
        log.exception("quiz publish crashed (chat=%s)", chat_id)
        try:
            await bot.send_message(notify_chat_id, "❌ حدث خطأ غير متوقع أثناء نشر الاختبار. راجع سجلات الخادم.")
        except Exception:
            pass
    finally:
        _publishing.discard((chat_id, int(thread_id or 0)))


async def _publish_to_chat(bot: Bot, quiz_id: int, chat_id: int, kind: str, title: str,
                           started_by: int, notify_chat_id: int, thread_id: int | None = None) -> None:
    is_channel = kind == "channel"
    async with Session() as session:
        quiz = await session.get(Quiz, quiz_id)
        if quiz is None or not quiz.questions:
            await bot.send_message(notify_chat_id, "❌ الاختبار غير موجود أو بلا أسئلة.")
            return
        questions = list(quiz.questions)
        group_settings = await session.get(GroupSettings, chat_id) if not is_channel else None
        snapshot = {}
        if group_settings is not None:
            snapshot = {"shuffle_questions": group_settings.shuffle_questions, "shuffle_options": group_settings.shuffle_options,
                        "anonymous_mode": group_settings.anonymous_mode, "explanation_mode": group_settings.explanation_mode,
                        "leaderboard_limit": group_settings.leaderboard_limit}
        if snapshot.get("shuffle_questions"):
            random.shuffle(questions)
        qs = None
        if not is_channel:
            qs = QuizSession(quiz_id=quiz.id, chat_id=chat_id, chat_title=title[:200], started_by=started_by,
                             thread_id=thread_id, settings_json=json.dumps(snapshot, ensure_ascii=False),
                             ends_at=(utcnow() + timedelta(seconds=group_settings.duration_seconds)) if group_settings and group_settings.duration_seconds else None,
                             total_questions=len(questions), status="publishing")
            session.add(qs)
            await session.commit()
        sent, failure = 0, ""
        for question in questions:
            try:
                options = json.loads(question.options_json)
                order = list(range(len(options)))
                if group_settings and group_settings.shuffle_options:
                    random.shuffle(order)
                correct_index = order.index(question.correct_option_index)
                view = type("QuestionView", (), {"number": sent + 1, "question": question.question,
                    "options_json": json.dumps([options[i] for i in order], ensure_ascii=False),
                    "correct_option_index": correct_index, "explanation": question.explanation})()
                payload = _poll_payload(view, len(questions), anonymous=is_channel)
                if group_settings and group_settings.open_period:
                    payload["open_period"] = group_settings.open_period
                if group_settings and group_settings.explanation_mode == "end":
                    payload.pop("explanation", None); payload.pop("explanation_parse_mode", None)
                message = await _send_poll(bot, chat_id, payload, thread_id=thread_id)
            except Exception as exc:
                failure = _publish_error(exc)
                log.warning("quiz publish to %s stopped at question %s: %s", chat_id, question.number, exc)
                break
            sent += 1
            if qs is not None:
                session.add(QuizSessionPoll(poll_id=message.poll.id, session_id=qs.id, question_number=question.number,
                                            correct_option_index=correct_index, message_id=message.message_id))
                await session.commit()
            await asyncio.sleep(SHARED_DELAY)
        if qs is not None:
            qs.total_questions = sent
            qs.status = "active" if sent else "failed"
            await session.commit()
            if sent:
                control = await bot.send_message(
                    chat_id, f"🏁 <b>اختبار #{quiz.id}</b> — {sent} سؤال\nأجيبوا عن الاستطلاعات أعلاه. استخدموا أزرار التحكم.",
                    message_thread_id=thread_id,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="📊 الترتيب الحالي", callback_data=f"quizs:board:{qs.id}"),
                        InlineKeyboardButton(text="⏸ إيقاف مؤقت", callback_data=f"quizs:pause:{qs.id}"),
                        InlineKeyboardButton(text="▶️ استئناف", callback_data=f"quizs:resume:{qs.id}"),
                        InlineKeyboardButton(text="🏁 إنهاء", callback_data=f"quizs:stop:{qs.id}")]]))
                qs.control_message_id = control.message_id
                await session.commit()
        await audit(session, started_by, "quiz_publish", f"quiz#{quiz.id} -> {chat_id} ({kind}) sent={sent}/{len(questions)}")
        await session.commit()
    if sent == len(questions):
        note = "\n\nℹ️ القنوات تخفي هوية المجيبين، لذلك لا يوجد ترتيب للقنوات." if is_channel else ""
        text = f"✅ تم نشر الاختبار #{quiz_id} في «{esc(title)}» ({sent} سؤال).{note}"
    elif sent:
        text = f"⚠️ نُشر {sent} من {len(questions)} سؤالاً في «{esc(title)}» ثم توقف: {esc(failure)}"
    else:
        text = f"❌ لم يُنشر الاختبار في «{esc(title)}»: {esc(failure)}"
    try:
        await bot.send_message(notify_chat_id, text)
    except Exception:
        pass


# ----------------------------------------------------------------------------- leaderboard / finishing

async def _leaderboard(session, session_id: int) -> list[tuple[int, str, int, int]]:
    correct = func.sum(case((QuizSessionAnswer.is_correct.is_(True), 1), else_=0))
    answered = func.count(QuizSessionAnswer.id)
    rows = (await session.execute(
        select(QuizSessionAnswer.user_telegram_id, func.max(QuizSessionAnswer.user_name), correct, answered)
        .where(QuizSessionAnswer.session_id == session_id)
        .group_by(QuizSessionAnswer.user_telegram_id)
        .order_by(correct.desc(), answered.desc(), func.max(QuizSessionAnswer.answered_at).asc())
    )).all()
    return rank_leaderboard([(uid, name or "مجهول", int(c or 0), int(n or 0)) for uid, name, c, n in rows])


def _format_board(rows, quiz_id: int, total: int, limit: int = 10, *, final: bool = False, anonymous: bool = False) -> str:
    head = "🏆 <b>النتيجة النهائية</b>" if final else "📊 <b>الترتيب الحالي</b>"
    lines = [f"{head} — اختبار #{quiz_id}", f"عدد الأسئلة: {total} • المشاركون: {len(rows)}", ""]
    if not rows:
        lines.append("لم يجب أحد بعد.")
    for i, (_uid, name, correct, answered) in enumerate(rows[:limit]):
        mark = MEDALS[i] if i < len(MEDALS) else f"{i + 1}."
        if anonymous:
            name = f"مشارك {i + 1}"
        pct = round(correct * 100 / total) if total else 0
        lines.append(f"{mark} {esc(name)} — <b>{correct}</b>/{total} ({pct}%)" + ("" if answered >= total else f" <i>أجاب على {answered}</i>"))
    if len(rows) > limit:
        lines.append(f"… و{len(rows) - limit} مشاركين آخرين")
    return "\n".join(lines)


async def _latest_session(session, chat_id: int, only_active: bool = False, thread_id: int | None = None):
    q = select(QuizSession).where(QuizSession.chat_id == chat_id)
    q = q.where(QuizSession.thread_id == thread_id) if thread_id is not None else q.where(QuizSession.thread_id.is_(None))
    if only_active:
        q = q.where(QuizSession.status.in_(("publishing", "active", "paused")))
    return await session.scalar(q.order_by(QuizSession.id.desc()))


async def finish_session(bot: Bot, session_id: int) -> bool:
    """Close the polls and announce the leaderboard. Returns False if already finished."""
    async with Session() as s:
        qs = await s.get(QuizSession, session_id)
        if qs is None or qs.status in {"closed", "failed"}:
            return False
        polls = (await s.scalars(select(QuizSessionPoll).where(QuizSessionPoll.session_id == session_id)
                                 .order_by(QuizSessionPoll.question_number))).all()
        qs.status, qs.finished_at = "closed", utcnow()   # close first: late answers are ignored
        await s.commit()
        chat_id, control_id, quiz_id, total = qs.chat_id, qs.control_message_id, qs.quiz_id, qs.total_questions
        thread_id = qs.thread_id
        board_cfg = json.loads(qs.settings_json or "{}")
        rows = await _leaderboard(s, session_id)
        quiz = await s.get(Quiz, quiz_id)
        end_explanations = []
        if board_cfg.get("explanation_mode") == "end" and quiz is not None:
            end_explanations = [f"{q.number}. {esc(q.explanation)}" for q in quiz.questions if q.explanation]
    for poll in polls:
        try:
            await bot.stop_poll(chat_id, poll.message_id)
        except Exception:
            pass
        await asyncio.sleep(0.35)
    try:
        final_text = _format_board(rows, quiz_id, total,
            limit=int(board_cfg.get("leaderboard_limit", 10)), anonymous=bool(board_cfg.get("anonymous_mode", False)), final=True)
        if end_explanations:
            final_text += "\n\n💡 <b>الشروحات</b>\n" + "\n".join(end_explanations)[:2500]
        await bot.send_message(chat_id, final_text, message_thread_id=thread_id)
    except Exception:
        log.exception("could not send final leaderboard to %s", chat_id)
    if control_id:
        try:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=control_id, reply_markup=None)
        except Exception:
            pass
    return True


@router.callback_query(F.data.startswith("quizs:"))
async def session_buttons(cb: CallbackQuery, session):
    try:
        _, action, raw_id = cb.data.split(":")
        qs = await session.get(QuizSession, int(raw_id))
    except (ValueError, TypeError):
        qs = None
    if qs is None:
        return await cb.answer("هذا الاختبار لم يعد موجوداً.", show_alert=True)
    if action == "board":
        rows = await _leaderboard(session, qs.id)
        cfg = json.loads(qs.settings_json or "{}")
        short = ["📊 الترتيب الحالي"]
        for i, (_u, name, correct, _n) in enumerate(rows[:5]):
            if cfg.get("anonymous_mode"):
                name = f"مشارك {i + 1}"
            short.append(f"{MEDALS[i] if i < 3 else str(i + 1) + '.'} {name[:18]} {correct}/{qs.total_questions}")
        if not rows:
            short.append("لم يجب أحد بعد")
        return await cb.answer("\n".join(short)[:190], show_alert=True)
    if action in {"stop", "pause", "resume"}:
        if not await is_chat_admin(cb.bot, qs.chat_id, cb.from_user.id):
            return await cb.answer("⛔ التحكم بالاختبار للمشرفين فقط.", show_alert=True)
        if action == "pause":
            if qs.status != "active":
                return await cb.answer("الاختبار ليس في حالة تشغيل.", show_alert=True)
            qs.status, qs.paused_at = "paused", utcnow()
            await session.commit()
            await cb.answer("تم إيقاف الاختبار مؤقتاً")
            try:
                await cb.bot.send_message(qs.chat_id, "⏸ تم إيقاف قبول الإجابات مؤقتاً. يمكن استئنافه من زر التحكم.", message_thread_id=qs.thread_id)
            except TelegramAPIError:
                pass
            return
        if action == "resume":
            if qs.status != "paused":
                return await cb.answer("الاختبار ليس متوقفاً مؤقتاً.", show_alert=True)
            qs.status, qs.paused_at = "active", None
            await session.commit()
            await cb.answer("تم استئناف الاختبار")
            try:
                await cb.bot.send_message(qs.chat_id, "▶️ تم استئناف الاختبار.", message_thread_id=qs.thread_id)
            except TelegramAPIError:
                pass
            return
        await cb.answer("جارٍ إنهاء الاختبار…")
        await finish_session(cb.bot, qs.id)
        return
    await cb.answer()


# ----------------------------------------------------------------------------- group commands

def _anonymous_admin(message: Message) -> bool:
    return message.sender_chat is not None and message.sender_chat.id == message.chat.id


async def _group_settings(session, chat_id: int, title: str = "") -> GroupSettings:
    row = await session.get(GroupSettings, chat_id)
    if row is None:
        row = GroupSettings(chat_id=chat_id, title=(title or "")[:200])
        session.add(row)
        await session.flush()
    elif title:
        row.title = title[:200]
    return row


async def _can_start_in_group(bot: Bot, chat_id: int, user_id: int, anonymous: bool, cfg: GroupSettings) -> bool:
    if user_id in settings.owner_ids:
        return bool(cfg.enabled)
    try:
        member = await bot.get_chat_member(chat_id, user_id) if not anonymous else None
        status = status_of(member) if member is not None else "unknown"
    except TelegramAPIError:
        status = "unknown"
    try:
        allowed = {int(x) for x in json.loads(cfg.allowed_admin_ids_json or "[]")}
    except (ValueError, TypeError):
        allowed = set()
    return can_start_quiz(cfg.start_policy, status, enabled=cfg.enabled,
                           is_owner_id=user_id in settings.owner_ids,
                           allowed_admin_ids=allowed, user_id=user_id,
                           anonymous_admin=anonymous)


@router.message(Command("quizsettings"), F.chat.type.in_(GROUP_TYPES))
async def group_quiz_settings(message: Message, session):
    uid = message.from_user.id if message.from_user else 0
    if not (_anonymous_admin(message) or await is_chat_admin(message.bot, message.chat.id, uid)):
        return await message.reply("⛔ إعدادات الاختبار للمشرفين فقط.")
    cfg = await _group_settings(session, message.chat.id, message.chat.title or "")
    await session.commit()
    await message.reply(_group_settings_text(cfg), reply_markup=_group_settings_kb(message.chat.id))


def _group_settings_text(cfg: GroupSettings) -> str:
    policy = {"owner_only": "مالك المجموعة فقط", "all_admins": "كل المشرفين", "selected": "مشرفون محددون"}.get(cfg.start_policy, "كل المشرفين")
    period = f"{cfg.open_period} ثانية" if cfg.open_period else "بلا مؤقّت"
    duration = f"{cfg.duration_seconds} ثانية" if cfg.duration_seconds else "بلا مدة كلية"
    return (f"⚙️ <b>إعدادات الاختبارات</b>\nالحالة: {'مفعّلة' if cfg.enabled else 'معطّلة'}\n"
            f"من يبدأ: {policy}\nوقت السؤال: {period}\nالمدة الكلية: {duration}\n"
            f"خلط الأسئلة: {'نعم' if cfg.shuffle_questions else 'لا'} • خلط الخيارات: {'نعم' if cfg.shuffle_options else 'لا'}\n"
            f"إظهار الشرح: {cfg.explanation_mode} • الترتيب المجهول: {'نعم' if cfg.anonymous_mode else 'لا'}\n"
            f"عدد الظاهرين: {cfg.leaderboard_limit}")


def _group_settings_kb(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="تفعيل/تعطيل", callback_data=f"quizcfg:enabled:{chat_id}"),
         InlineKeyboardButton(text="سياسة البدء", callback_data=f"quizcfg:policy:{chat_id}")],
        [InlineKeyboardButton(text="خلط الأسئلة", callback_data=f"quizcfg:shuffleq:{chat_id}"),
         InlineKeyboardButton(text="خلط الخيارات", callback_data=f"quizcfg:shuffleo:{chat_id}")],
        [InlineKeyboardButton(text="مؤقّت السؤال", callback_data=f"quizcfg:period:{chat_id}"),
         InlineKeyboardButton(text="مدة الاختبار", callback_data=f"quizcfg:duration:{chat_id}")],
        [InlineKeyboardButton(text="إظهار الشرح", callback_data=f"quizcfg:explain:{chat_id}"),
         InlineKeyboardButton(text="اختبار مجهول", callback_data=f"quizcfg:anonymous:{chat_id}")],
        [InlineKeyboardButton(text="عدد المتصدرين", callback_data=f"quizcfg:limit:{chat_id}")],
    ])


@router.callback_query(F.data.startswith("quizcfg:"))
async def group_quiz_settings_callback(cb: CallbackQuery, session):
    try:
        _, action, raw_chat = cb.data.split(":")
        chat_id = int(raw_chat)
    except (ValueError, TypeError):
        return await cb.answer("طلب غير صالح.", show_alert=True)
    if not await is_chat_admin(cb.bot, chat_id, cb.from_user.id):
        return await cb.answer("⛔ هذه الإعدادات للمشرفين فقط.", show_alert=True)
    cfg = await _group_settings(session, chat_id)
    if action == "enabled":
        cfg.enabled = not cfg.enabled
    elif action == "policy":
        cfg.start_policy = {"all_admins": "owner_only", "owner_only": "selected", "selected": "all_admins"}.get(cfg.start_policy, "owner_only")
    elif action == "shuffleq":
        cfg.shuffle_questions = not cfg.shuffle_questions
    elif action == "shuffleo":
        cfg.shuffle_options = not cfg.shuffle_options
    elif action == "period":
        cfg.open_period = {None: 30, 30: 60, 60: 0}.get(cfg.open_period, 30) or None
    elif action == "duration":
        cfg.duration_seconds = {None: 600, 600: 1800, 1800: 3600, 3600: 0}.get(cfg.duration_seconds, 600) or None
    elif action == "explain":
        cfg.explanation_mode = "end" if cfg.explanation_mode == "after" else "after"
    elif action == "anonymous":
        cfg.anonymous_mode = not cfg.anonymous_mode
    elif action == "limit":
        cfg.leaderboard_limit = {5: 10, 10: 20, 20: 5}.get(cfg.leaderboard_limit, 10)
    else:
        return await cb.answer("إعداد غير معروف.", show_alert=True)
    await session.commit()
    await cb.message.edit_text(_group_settings_text(cfg), reply_markup=_group_settings_kb(chat_id))
    await cb.answer("تم حفظ الإعدادات")


@router.message(Command("quizallow"), F.chat.type.in_(GROUP_TYPES))
async def group_quiz_allow_admin(message: Message, session, command: CommandObject):
    uid = message.from_user.id if message.from_user else 0
    if not await is_chat_admin(message.bot, message.chat.id, uid):
        return await message.reply("⛔ مالك المجموعة أو مشرف مخوّل فقط.")
    try:
        actor = await message.bot.get_chat_member(message.chat.id, uid)
        if status_of(actor) != "creator" and uid not in settings.owner_ids:
            return await message.reply("⛔ إدارة قائمة البدء المسموح بها لمالك المجموعة فقط.")
    except TelegramAPIError:
        return await message.reply("تعذّر التحقق من مالك المجموعة.")
    try:
        target_id = int((command.args or "").strip())
    except ValueError:
        return await message.reply("استخدم: /quizallow رقم_المستخدم")
    cfg = await _group_settings(session, message.chat.id, message.chat.title or "")
    try:
        ids = {int(x) for x in json.loads(cfg.allowed_admin_ids_json or "[]")}
    except (ValueError, TypeError):
        ids = set()
    ids.add(target_id)
    cfg.allowed_admin_ids_json = json.dumps(sorted(ids))
    cfg.start_policy = "selected"
    await session.commit()
    await message.reply(f"✅ أُضيف المستخدم {target_id} إلى قائمة المشرفين المسموح لهم.")


@router.message(Command("quiz"), F.chat.type.in_(GROUP_TYPES))
async def group_quiz(message: Message, command: CommandObject, session):
    bot, uid = message.bot, (message.from_user.id if message.from_user else 0)
    anonymous = _anonymous_admin(message)
    cfg = await _group_settings(session, message.chat.id, message.chat.title or "")
    if not await _can_start_in_group(bot, message.chat.id, uid, anonymous, cfg):
        return await message.reply("⛔ لا تملك صلاحية بدء الاختبار أو أن ميزة الاختبارات معطّلة. استخدم /quizsettings للمراجعة.")
    args = (command.args or "").strip()
    if args.isdigit():
        quiz = await session.get(Quiz, int(args))
        if quiz is None or anonymous or (quiz.owner_telegram_id != uid and uid not in settings.owner_ids):
            return await message.reply("لا أجد اختباراً بهذا الرقم يخصّ حسابك. (لا يعمل مع حساب المشرف المجهول)")
    else:
        reply = message.reply_to_message
        source = args or ((reply.text or reply.caption or "") if reply else "")
        if not source.strip():
            return await message.reply(GROUP_HELP)
        parsed = parse_quiz_text(source)
        if not _is_valid(parsed):
            return await message.reply(_problems_text(parsed)[:3900])
        quiz = await _save_quiz(session, uid, source, parsed)
    chat = message.chat
    thread_id = getattr(message, "message_thread_id", None)
    error = await _launch_publish(bot, quiz.id, chat.id, "group", chat.title or "", uid, chat.id, thread_id)
    if error:
        return await message.reply(error)
    minutes = max(1, round(len(quiz.questions) * SHARED_DELAY / 60))
    await message.reply(f"⏳ سأنشر {len(quiz.questions)} سؤالاً (نحو {minutes} دقيقة بسبب حدود تيليجرام). "
                        "يمكنكم الإجابة مباشرة أثناء النشر.")


@router.message(Command("quizpause"), F.chat.type.in_(GROUP_TYPES))
async def group_quiz_pause(message: Message, session):
    uid = message.from_user.id if message.from_user else 0
    if not (_anonymous_admin(message) or await is_chat_admin(message.bot, message.chat.id, uid)):
        return await message.reply("⛔ هذا الأمر للمشرفين فقط.")
    qs = await _latest_session(session, message.chat.id, only_active=True, thread_id=getattr(message, "message_thread_id", None))
    if qs is None or qs.status != "active":
        return await message.reply("لا يوجد اختبار نشط قابل للإيقاف.")
    qs.status, qs.paused_at = "paused", utcnow()
    await session.commit()
    await message.reply("⏸ تم إيقاف قبول الإجابات مؤقتاً.")


@router.message(Command("quizresume"), F.chat.type.in_(GROUP_TYPES))
async def group_quiz_resume(message: Message, session):
    uid = message.from_user.id if message.from_user else 0
    if not (_anonymous_admin(message) or await is_chat_admin(message.bot, message.chat.id, uid)):
        return await message.reply("⛔ هذا الأمر للمشرفين فقط.")
    qs = await _latest_session(session, message.chat.id, thread_id=getattr(message, "message_thread_id", None))
    if qs is None or qs.status != "paused":
        return await message.reply("لا يوجد اختبار متوقف مؤقتاً.")
    qs.status, qs.paused_at = "active", None
    await session.commit()
    await message.reply("▶️ تم استئناف الاختبار.")


@router.message(Command("quizstop"), F.chat.type.in_(GROUP_TYPES))
async def group_quiz_stop(message: Message, session):
    uid = message.from_user.id if message.from_user else 0
    if not (_anonymous_admin(message) or await is_chat_admin(message.bot, message.chat.id, uid)):
        return await message.reply("⛔ هذا الأمر لمشرفي المجموعة فقط.")
    qs = await _latest_session(session, message.chat.id, only_active=True, thread_id=getattr(message, "message_thread_id", None))
    if qs is None:
        return await message.reply("لا يوجد اختبار نشط في هذه المجموعة.")
    await finish_session(message.bot, qs.id)


@router.message(Command("quizresults"), F.chat.type.in_(GROUP_TYPES))
async def group_quiz_results(message: Message, session):
    qs = await _latest_session(session, message.chat.id, thread_id=getattr(message, "message_thread_id", None))
    if qs is None:
        return await message.reply("لم يُنشر أي اختبار في هذه المجموعة بعد.")
    rows = await _leaderboard(session, qs.id)
    cfg = json.loads(qs.settings_json or "{}")
    await message.reply(_format_board(rows, qs.quiz_id, qs.total_questions,
        limit=int(cfg.get("leaderboard_limit", 10)), anonymous=bool(cfg.get("anonymous_mode", False)),
        final=qs.status == "closed"), message_thread_id=qs.thread_id)


@router.message(Command("quizhistory"), F.chat.type.in_(GROUP_TYPES))
async def group_quiz_history(message: Message, session):
    rows = (await session.scalars(select(QuizSession).where(QuizSession.chat_id == message.chat.id)
        .order_by(QuizSession.id.desc()).limit(10))).all()
    if not rows:
        return await message.reply("لا يوجد سجل اختبارات بعد.")
    lines = ["📚 <b>سجل الاختبارات</b>"]
    for row in rows:
        lines.append(f"#{row.quiz_id} — {row.status} — {row.started_at:%Y-%m-%d %H:%M}")
    await message.reply("\n".join(lines))


@router.message(Command("quizdelete"), F.chat.type.in_(GROUP_TYPES))
async def group_quiz_delete(message: Message, session, command: CommandObject):
    uid = message.from_user.id if message.from_user else 0
    if not (_anonymous_admin(message) or await is_chat_admin(message.bot, message.chat.id, uid)):
        return await message.reply("⛔ الحذف للمشرفين فقط.")
    try:
        quiz_id = int((command.args or "").strip())
    except ValueError:
        return await message.reply("استخدم: /quizdelete رقم_الاختبار")
    qs_rows = (await session.scalars(select(QuizSession).where(
        QuizSession.chat_id == message.chat.id, QuizSession.quiz_id == quiz_id))).all()
    if not qs_rows:
        return await message.reply("لا توجد جلسة لهذا الاختبار في المجموعة.")
    for qs in qs_rows:
        polls = (await session.scalars(select(QuizSessionPoll).where(QuizSessionPoll.session_id == qs.id))).all()
        for poll in polls:
            try:
                await message.bot.delete_message(message.chat.id, poll.message_id)
            except TelegramAPIError:
                pass
        if qs.control_message_id:
            try:
                await message.bot.delete_message(message.chat.id, qs.control_message_id)
            except TelegramAPIError:
                pass
        await session.delete(qs)
    await session.commit()
    await message.reply("🗑️ حُذفت جلسة الاختبار ورسائل استطلاعاتها من هذه المجموعة. بقيت نسخة الاختبار المحفوظة متاحة لمالكها.")



@router.message(Command("quiztimezone"), F.chat.type.in_(GROUP_TYPES))
async def group_quiz_timezone(message: Message, command: CommandObject, session):
    uid = message.from_user.id if message.from_user else 0
    if not await is_chat_admin(message.bot, message.chat.id, uid):
        return await message.reply("⛔ ضبط المنطقة الزمنية للمشرفين فقط.")
    name = (command.args or "").strip()
    if not name:
        cfg = await _group_settings(session, message.chat.id)
        return await message.reply(f"المنطقة الزمنية الحالية: {cfg.timezone}\nاستخدم /quiztimezone Asia/Baghdad")
    try:
        ZoneInfo(name)
    except Exception:
        return await message.reply("اسم المنطقة الزمنية غير صالح. مثال: Asia/Baghdad")
    cfg = await _group_settings(session, message.chat.id, message.chat.title or "")
    cfg.timezone = name
    await session.commit()
    await message.reply(f"✅ تم ضبط توقيت الجدولة إلى {name}.")


@router.message(Command("quizdeny"), F.chat.type.in_(GROUP_TYPES))
async def group_quiz_deny_admin(message: Message, command: CommandObject, session):
    uid = message.from_user.id if message.from_user else 0
    try:
        actor = await message.bot.get_chat_member(message.chat.id, uid)
        if status_of(actor) != "creator" and uid not in settings.owner_ids:
            return await message.reply("⛔ هذا الأمر لمالك المجموعة فقط.")
        target_id = int((command.args or "").strip())
    except (ValueError, TelegramAPIError):
        return await message.reply("استخدم: /quizdeny رقم_المستخدم")
    cfg = await _group_settings(session, message.chat.id, message.chat.title or "")
    try:
        ids = {int(x) for x in json.loads(cfg.allowed_admin_ids_json or "[]")}
    except (ValueError, TypeError):
        ids = set()
    ids.discard(target_id)
    cfg.allowed_admin_ids_json = json.dumps(sorted(ids))
    await session.commit()
    await message.reply(f"✅ أُزيل المستخدم {target_id} من القائمة المسموح لها.")


@router.message(Command("quizschedule"), F.chat.type.in_(GROUP_TYPES))
async def group_quiz_schedule(message: Message, command: CommandObject, session):
    uid = message.from_user.id if message.from_user else 0
    if not await is_chat_admin(message.bot, message.chat.id, uid):
        return await message.reply("⛔ الجدولة للمشرفين فقط.")
    parts = (command.args or "").split()
    if len(parts) not in {3, 4}:
        return await message.reply("استخدم: /quizschedule رقم_الاختبار YYYY-MM-DD HH:MM [daily|weekly]")
    try:
        quiz_id = int(parts[0])
        local = datetime.strptime(parts[1] + " " + parts[2], "%Y-%m-%d %H:%M")
        repeat = parts[3].lower() if len(parts) == 4 else "none"
        if repeat not in {"none", "daily", "weekly"}:
            raise ValueError
    except ValueError:
        return await message.reply("صيغة التاريخ أو التكرار غير صحيحة.")
    quiz = await session.get(Quiz, quiz_id)
    if quiz is None or (quiz.owner_telegram_id != uid and uid not in settings.owner_ids):
        return await message.reply("الاختبار غير موجود أو ليس ملكك.")
    cfg = await _group_settings(session, message.chat.id, message.chat.title or "")
    try:
        utc = local_schedule_to_utc(local, cfg.timezone)
    except Exception:
        return await message.reply("المنطقة الزمنية المخزنة غير صالحة. صححها عبر /quiztimezone.")
    if utc <= utcnow():
        return await message.reply("موعد الجدولة يجب أن يكون في المستقبل.")
    session.add(GroupQuizSchedule(chat_id=message.chat.id, quiz_id=quiz_id,
        thread_id=getattr(message, "message_thread_id", None), requested_by=uid,
        run_at=utc, repeat_mode=repeat, timezone=cfg.timezone))
    await session.commit()
    await message.reply(f"✅ تمت جدولة الاختبار #{quiz_id} بتاريخ {local:%Y-%m-%d %H:%M} ({cfg.timezone}).")



@router.message(Command("quizrepost"), F.chat.type.in_(GROUP_TYPES))
async def group_quiz_repost(message: Message, command: CommandObject, session):
    uid = message.from_user.id if message.from_user else 0
    if not (_anonymous_admin(message) or await is_chat_admin(message.bot, message.chat.id, uid)):
        return await message.reply("⛔ إعادة النشر للمشرفين فقط.")
    raw = (command.args or "").strip()
    qs = await _latest_session(session, message.chat.id, thread_id=getattr(message, "message_thread_id", None))
    try:
        quiz_id = int(raw) if raw else (qs.quiz_id if qs else 0)
    except ValueError:
        return await message.reply("استخدم: /quizrepost [رقم_الاختبار]")
    quiz = await session.get(Quiz, quiz_id)
    if quiz is None:
        return await message.reply("الاختبار غير موجود.")
    cfg = await _group_settings(session, message.chat.id, message.chat.title or "")
    if not await _can_start_in_group(message.bot, message.chat.id, uid, _anonymous_admin(message), cfg):
        return await message.reply("⛔ لا تملك صلاحية إعادة النشر.")
    error = await _launch_publish(message.bot, quiz.id, message.chat.id, "group",
        message.chat.title or "", uid, message.chat.id, getattr(message, "message_thread_id", None))
    if error:
        return await message.reply(error)
    await message.reply(f"🔁 بدأت إعادة نشر الاختبار #{quiz.id}.")


@router.message(Command("quizstats"), F.chat.type.in_(GROUP_TYPES))
async def group_quiz_stats(message: Message, session):
    uid = message.from_user.id if message.from_user else 0
    if not (_anonymous_admin(message) or await is_chat_admin(message.bot, message.chat.id, uid)):
        return await message.reply("⛔ الإحصائيات للمشرفين فقط.")
    qs = await _latest_session(session, message.chat.id, thread_id=getattr(message, "message_thread_id", None))
    if qs is None:
        return await message.reply("لا توجد اختبارات مسجلة.")
    participant_count = await session.scalar(select(func.count(func.distinct(QuizSessionAnswer.user_telegram_id))).where(QuizSessionAnswer.session_id == qs.id)) or 0
    answer_count = await session.scalar(select(func.count(QuizSessionAnswer.id)).where(QuizSessionAnswer.session_id == qs.id)) or 0
    correct_count = await session.scalar(select(func.count(QuizSessionAnswer.id)).where(QuizSessionAnswer.session_id == qs.id, QuizSessionAnswer.is_correct.is_(True))) or 0
    await message.reply(f"📊 <b>إحصائيات الاختبار #{qs.quiz_id}</b>\nالمشاركون: {participant_count}\nالإجابات: {answer_count}\nالإجابات الصحيحة: {correct_count}\nالحالة: {qs.status}")


@router.message(Command("quizgroups"), F.chat.type == "private")
async def developer_quiz_groups(message: Message, session):
    if message.from_user.id not in settings.owner_ids:
        return await message.answer("⛔ هذا الأمر للمطوّر فقط.")
    targets = (await session.scalars(select(QuizTarget).where(QuizTarget.kind.in_(("group", "supergroup")))
        .order_by(QuizTarget.created_at.desc()).limit(50))).all()
    if not targets:
        return await message.answer("لا توجد مجموعات مرتبطة بعد.")
    rows = []
    for target in targets:
        count = await session.scalar(select(func.count(QuizSession.id)).where(QuizSession.chat_id == target.chat_id)) or 0
        active = await session.scalar(select(QuizSession.id).where(QuizSession.chat_id == target.chat_id,
            QuizSession.status.in_(("publishing", "active", "paused"))).order_by(QuizSession.id.desc()).limit(1))
        cfg = await session.get(GroupSettings, target.chat_id)
        state = "مفعّلة" if cfg is None or cfg.enabled else "معطّلة"
        rows.append([InlineKeyboardButton(text=f"{target.title[:22]} • {count} اختبار • {state}", callback_data=f"quizdev:disable:{target.chat_id}")])
        if active:
            rows.append([InlineKeyboardButton(text=f"إنهاء جلسة #{active}", callback_data=f"quizdev:finish:{active}")])
    await message.answer("🛠 <b>مجموعات الاختبارات</b>\nاضغط على المجموعة لتعطيل/تفعيل الاختبارات فيها، أو أنهِ جلسة عالقة:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("quizdev:"))
async def developer_quiz_group_action(cb: CallbackQuery, session):
    if cb.from_user.id not in settings.owner_ids:
        return await cb.answer("⛔ للمطوّر فقط.", show_alert=True)
    try:
        _, action, raw_id = cb.data.split(":")
        value = int(raw_id)
    except (ValueError, TypeError):
        return await cb.answer("طلب غير صالح.", show_alert=True)
    if action == "finish":
        ok = await finish_session(cb.bot, value)
        return await cb.answer("تم إنهاء الجلسة." if ok else "الجلسة منتهية أو غير موجودة.", show_alert=True)
    if action == "disable":
        cfg = await _group_settings(session, value)
        cfg.enabled = not cfg.enabled
        await session.commit()
        return await cb.answer("تم تفعيل الاختبارات." if cfg.enabled else "تم تعطيل الاختبارات.", show_alert=True)
    await cb.answer("إجراء غير معروف.", show_alert=True)


@router.message(Command("quizexport"), F.chat.type.in_(GROUP_TYPES))
async def group_quiz_export(message: Message, session, command: CommandObject):
    uid = message.from_user.id if message.from_user else 0
    if not await is_chat_admin(message.bot, message.chat.id, uid):
        return await message.reply("⛔ تصدير النتائج للمشرفين فقط.")
    qs = await _latest_session(session, message.chat.id, thread_id=getattr(message, "message_thread_id", None))
    if qs is None:
        return await message.reply("لا توجد نتائج.")
    rows = await _leaderboard(session, qs.id)
    lines = ["الترتيب,الاسم,الصحيحة,المجاب"]
    for i, (_user, name, correct, answered) in enumerate(rows, 1):
        lines.append(f'{i},"{name.replace(chr(34), chr(34)*2)}",{correct},{answered}')
    import io
    doc = BufferedInputFile(("\n".join(lines)).encode("utf-8-sig"), filename=f"quiz-{qs.id}.csv")
    await message.bot.send_document(message.chat.id, doc, caption=f"نتائج الاختبار #{qs.quiz_id}")



async def quiz_maintenance_loop(bot: Bot):
    """Recover interrupted sessions and run durable group schedules/auto-expiry."""
    async with Session() as s:
        interrupted = (await s.scalars(select(QuizSession).where(QuizSession.status == "publishing"))).all()
        for qs in interrupted:
            count = await s.scalar(select(func.count(QuizSessionPoll.poll_id)).where(QuizSessionPoll.session_id == qs.id)) or 0
            qs.status = "active" if count else "failed"
            if not count:
                qs.finished_at = utcnow()
        await s.commit()
    while True:
        try:
            now = utcnow()
            async with Session() as s:
                due = (await s.scalars(select(GroupQuizSchedule).where(
                    GroupQuizSchedule.is_active.is_(True), GroupQuizSchedule.run_at <= now
                ).order_by(GroupQuizSchedule.run_at).limit(20))).all()
                due_data = [(x.id, x.chat_id, x.quiz_id, x.thread_id, x.requested_by, x.repeat_mode, x.run_at) for x in due]
                expired = (await s.scalars(select(QuizSession).where(
                    QuizSession.status.in_(("active", "paused")), QuizSession.ends_at.is_not(None),
                    QuizSession.ends_at <= now
                ))).all()
                expired_ids = [x.id for x in expired]
                await s.commit()
            for session_id in expired_ids:
                await finish_session(bot, session_id)
            for schedule_id, chat_id, quiz_id, thread_id, requester, repeat_mode, scheduled_at in due_data:
                async with Session() as s:
                    target = await s.scalar(select(QuizTarget).where(QuizTarget.chat_id == chat_id))
                    quiz = await s.get(Quiz, quiz_id)
                    cfg = await s.get(GroupSettings, chat_id)
                    if not target or not target.is_active or not quiz or (cfg and not cfg.enabled):
                        continue
                    error = await _launch_publish(bot, quiz_id, chat_id, target.kind, target.title or "",
                                                  requester, chat_id, thread_id)
                    if error:
                        log.info("Scheduled quiz %s was not launched: %s", quiz_id, error)
                        continue
                    schedule = await s.get(GroupQuizSchedule, schedule_id)
                    if schedule and schedule.is_active and schedule.run_at == scheduled_at:
                        schedule.last_run_at = now
                        if repeat_mode == "none":
                            schedule.is_active = False
                        else:
                            schedule.run_at = next_schedule_time(schedule.run_at, repeat_mode) or schedule.run_at
                        await s.commit()
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("group quiz maintenance loop failed")
            await asyncio.sleep(30)


async def recover_group_quiz_sessions(bot: Bot):
    """Close sessions that have remained active for more than 24 hours."""
    cutoff = utcnow() - timedelta(hours=24)
    async with Session() as s:
        stale = (await s.scalars(select(QuizSession).where(
            QuizSession.status.in_(("active", "paused")), QuizSession.started_at < cutoff
        ))).all()
        ids = [x.id for x in stale]
    for session_id in ids:
        await finish_session(bot, session_id)


@router.message(F.migrate_to_chat_id)
async def group_migrated(message: Message):
    old_chat_id = message.chat.id
    new_chat_id = message.migrate_to_chat_id
    async with Session() as s:
        target = await s.scalar(select(QuizTarget).where(QuizTarget.chat_id == old_chat_id))
        existing_target = await s.scalar(select(QuizTarget).where(QuizTarget.chat_id == new_chat_id))
        if target and existing_target:
            target.is_active = False
        elif target:
            target.chat_id = new_chat_id
        group_cfg = await s.get(GroupSettings, old_chat_id)
        existing_cfg = await s.get(GroupSettings, new_chat_id)
        if group_cfg and existing_cfg is None:
            group_cfg.chat_id = new_chat_id
        elif group_cfg and existing_cfg:
            existing_cfg.enabled = existing_cfg.enabled and group_cfg.enabled
        await s.execute(update(QuizSession).where(QuizSession.chat_id == old_chat_id).values(chat_id=new_chat_id))
        await s.execute(update(GroupQuizSchedule).where(GroupQuizSchedule.chat_id == old_chat_id).values(chat_id=new_chat_id))
        await s.commit()
    try:
        await message.bot.send_message(new_chat_id, "✅ تم تحديث ربط الاختبارات بعد تحويل المجموعة إلى سوبرغروب. استخدموا /quiz لبدء اختبار جديد.")
    except TelegramAPIError:
        log.warning("Could not announce chat migration old=%s new=%s", old_chat_id, new_chat_id)


@router.my_chat_member()
async def bot_membership_changed(event: ChatMemberUpdated):


@router.message(Command("quizstop"), F.chat.type.in_(GROUP_TYPES))
async def group_quiz_stop(message: Message, session):
    uid = message.from_user.id if message.from_user else 0
    if not (_anonymous_admin(message) or await is_chat_admin(message.bot, message.chat.id, uid)):
        return await message.reply("⛔ هذا الأمر لمشرفي المجموعة فقط.")
    qs = await _latest_session(session, message.chat.id, only_active=True, thread_id=getattr(message, "message_thread_id", None))
    if qs is None:
        return await message.reply("لا يوجد اختبار نشط في هذه المجموعة.")
    await finish_session(message.bot, qs.id)


@router.message(Command("quizresults"), F.chat.type.in_(GROUP_TYPES))
async def group_quiz_results(message: Message, session):
    qs = await _latest_session(session, message.chat.id)
    if qs is None:
        return await message.reply("لم يُنشر أي اختبار في هذه المجموعة بعد.")
    rows = await _leaderboard(session, qs.id)
    await message.reply(_format_board(rows, qs.quiz_id, qs.total_questions, final=qs.status == "closed"))


# ----------------------------------------------------------------------------- bot added to / removed from a chat

@router.my_chat_member()
async def bot_membership_changed(event: ChatMemberUpdated):
    """When the bot is added to a group/channel it becomes an available quiz destination."""
    chat = event.chat
    ctype = str(getattr(chat.type, "value", chat.type))
    if ctype not in GROUP_TYPES | {"channel"}:
        return
    new_status = status_of(event.new_chat_member)
    adder = event.from_user.id if event.from_user else 0
    async with Session() as s:
        row = await s.scalar(select(QuizTarget).where(QuizTarget.chat_id == chat.id))
        if new_status in {"left", "kicked"}:
            if row is not None:
                row.is_active = False
                await s.commit()
            return
        if new_status not in {"member", "administrator"}:
            return
        if row is None:
            row = QuizTarget(chat_id=chat.id, added_by=adder)
            s.add(row)
        row.kind, row.title, row.username, row.is_active = ctype, chat.title or "", getattr(chat, "username", None), True
        await s.commit()
    note = ""
    if ctype == "channel" and new_status != "administrator":
        note = "\n⚠️ للنشر في القناة يجب أن يكون البوت <b>مشرفاً</b> بصلاحية نشر الرسائل."
    elif ctype == "channel" and getattr(event.new_chat_member, "can_post_messages", None) is False:
        note = "\n⚠️ البوت مشرف لكن بلا صلاحية «نشر الرسائل»؛ فعّلها له."
    hint = "اكتب /quiz داخل المجموعة، أو /quizsettings لضبط الاختبار. الصلاحيات المطلوبة: إرسال الرسائل والاستطلاعات." if ctype in GROUP_TYPES else "افتح «🤖 إنشاء اختبار» هنا"
    if ctype in GROUP_TYPES:
        try:
            await event.bot.send_message(chat.id, "👋 أهلاً! أستطيع تشغيل اختبارات داخل هذه المجموعة.\nللمشرف: /quiz ثم أرسل الأسئلة، أو /quiz ردّاً على رسالة الأسئلة.\nالإعدادات: /quizsettings • النتائج: /quizresults • الإنهاء: /quizstop\nأحتاج صلاحية إرسال الرسائل والاستطلاعات. ولحذف رسائل الاختبار امنحني حذف الرسائل أيضاً.")
        except TelegramAPIError:
            log.warning("Could not send welcome message to chat=%s", chat.id)
    if adder:
        try:
            await event.bot.send_message(adder, f"✅ ربطتُ «{esc(chat.title or '')}» بالبوت لنشر الاختبارات. {hint}.{note}")
        except Exception:
            pass


# ----------------------------------------------------------------------------- private flow: create / preview / save

@router.callback_query(F.data == "quiz:start")
async def quiz_start(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(QuizInput.text)
    await cb.message.answer(quiz_prompt(), reply_markup=_kb())
    await cb.answer()


@router.message(Command("myquizzes"), F.chat.type == "private")
async def my_quizzes(message: Message, session):
    quizzes = (await session.scalars(select(Quiz).where(Quiz.owner_telegram_id == message.from_user.id)
                                     .order_by(Quiz.created_at.desc()).limit(10))).all()
    if not quizzes:
        return await message.answer("ما عندك اختبارات محفوظة بعد. اضغط «🤖 إنشاء اختبار».")
    rows = [[InlineKeyboardButton(text=f"🔁 #{q.id} {q.title[:20]} ({len(q.questions)})", callback_data=f"quiz:retry:{q.id}"),
             InlineKeyboardButton(text="📢 نشر", callback_data=f"quiz:pick:{q.id}")] for q in quizzes]
    await message.answer("📚 اختباراتك المحفوظة:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.message(QuizInput.text)
async def receive_quiz_text(message: Message, state: FSMContext):
    raw = message.text or ""
    if raw.strip().lower() in {"/cancel", "إلغاء", "الغاء"}:
        await state.clear()
        return await message.answer("تم إلغاء إنشاء الاختبار.")
    parsed = parse_quiz_text(raw)
    if not _is_valid(parsed):
        return await message.answer(_problems_text(parsed)[:3900], reply_markup=_kb())
    await state.update_data(quiz_raw=raw, quiz_parsed=parsed)
    await state.set_state(QuizInput.confirm)
    preview = _preview(parsed)
    for start in range(0, len(preview), 3200):
        await message.answer(preview[start:start + 3200],
                             reply_markup=_kb(confirm=True) if start + 3200 >= len(preview) else None)


@router.message(QuizInput.confirm)
async def wait_for_confirmation(message: Message):
    await message.answer("اضغط «حفظ وحلّه هنا» أو «حفظ ونشر» أو «تعديل النص» أسفل المعاينة.")


@router.callback_query(F.data == "quiz:edit")
async def quiz_edit(cb: CallbackQuery, state: FSMContext):
    await state.set_state(QuizInput.text)
    await cb.message.answer("✏️ أرسل النص كاملاً من جديد.", reply_markup=_kb())
    await cb.answer()


@router.callback_query(F.data == "quiz:cancel")
async def quiz_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer("تم الإلغاء")
    try:
        await cb.message.edit_text("❌ تم إلغاء إنشاء الاختبار.")
    except Exception:
        await cb.message.answer("❌ تم إلغاء إنشاء الاختبار.")


async def _confirmed_quiz(cb: CallbackQuery, state: FSMContext, session) -> Quiz | None:
    if await state.get_state() != QuizInput.confirm.state:
        await cb.answer("انتهت المعاينة؛ أرسل الأسئلة مجدداً.", show_alert=True)
        return None
    data = await state.get_data()
    parsed, raw = data.get("quiz_parsed"), data.get("quiz_raw")
    if not isinstance(parsed, dict) or not isinstance(raw, str) or parsed.get("issues"):
        await state.clear()
        await cb.answer("المعاينة غير صالحة. أعد إرسال النص.", show_alert=True)
        return None
    try:
        quiz = await _save_quiz(session, cb.from_user.id, raw, parsed)
    except Exception:
        await session.rollback()
        log.exception("Could not save quiz")
        await cb.answer("تعذّر حفظ الاختبار.", show_alert=True)
        return None
    await state.clear()
    return quiz


@router.callback_query(F.data == "quiz:confirm")
async def quiz_confirm(cb: CallbackQuery, state: FSMContext, session):
    quiz = await _confirmed_quiz(cb, state, session)
    if quiz is None:
        return
    try:
        await cb.message.edit_text(f"✅ تم حفظ الاختبار #{quiz.id}. جارٍ إرسال الأسئلة...")
    except Exception:
        pass
    await cb.answer("تم حفظ الاختبار")
    await _publish_attempt(cb.bot, session, quiz, cb.from_user.id)


@router.callback_query(F.data == "quiz:savepub")
async def quiz_save_and_publish(cb: CallbackQuery, state: FSMContext, session):
    quiz = await _confirmed_quiz(cb, state, session)
    if quiz is None:
        return
    await cb.answer("تم حفظ الاختبار")
    await _show_picker(cb.message, cb.bot, session, cb.from_user.id, quiz.id, edit=True)


@router.callback_query(F.data.startswith("quiz:retry:"))
async def quiz_retry(cb: CallbackQuery, session):
    quiz = await _owned_quiz(cb, session)
    if quiz is None:
        return
    await cb.answer("بدأ الاختبار")
    await _publish_attempt(cb.bot, session, quiz, cb.from_user.id)


async def _owned_quiz(cb: CallbackQuery, session, index: int = 2) -> Quiz | None:
    try:
        quiz_id = int(cb.data.split(":")[index])
    except (ValueError, IndexError):
        await cb.answer("رقم الاختبار غير صحيح.", show_alert=True)
        return None
    quiz = await session.get(Quiz, quiz_id)
    if not quiz or (quiz.owner_telegram_id != cb.from_user.id and cb.from_user.id not in settings.owner_ids):
        await cb.answer("هذا الاختبار غير متاح لحسابك.", show_alert=True)
        return None
    if not quiz.questions:
        await cb.answer("هذا الاختبار لا يحتوي أسئلة.", show_alert=True)
        return None
    return quiz


# ----------------------------------------------------------------------------- private flow: choose where to publish

async def _visible_targets(bot: Bot, session, user_id: int) -> list[QuizTarget]:
    rows = list((await session.scalars(select(QuizTarget).where(QuizTarget.is_active.is_(True))
                                       .order_by(QuizTarget.id).limit(40))).all())
    flags = await asyncio.gather(*(is_chat_admin(bot, r.chat_id, user_id) for r in rows))
    return [r for r, ok in zip(rows, flags) if ok]


async def _show_picker(message: Message, bot: Bot, session, user_id: int, quiz_id: int, *, edit: bool) -> None:
    targets = await _visible_targets(bot, session, user_id)
    icon = {"channel": "📢", "group": "👥", "supergroup": "👥"}
    rows = [[InlineKeyboardButton(text=f"{icon.get(t.kind, '📢')} {t.title or t.chat_id}"[:60], callback_data=f"quiz:pub:{quiz_id}:{t.id}")] for t in targets]
    rows.append([InlineKeyboardButton(text="➕ إضافة قناة/مجموعة", callback_data=f"quiz:addt:{quiz_id}")])
    rows.append([InlineKeyboardButton(text="▶️ حلّه هنا بدلاً من ذلك", callback_data=f"quiz:retry:{quiz_id}")])
    if targets:
        text = f"📢 <b>اختبار #{quiz_id}</b>\n\nاختر المكان الذي تريد نشر الاختبار فيه:"
    else:
        text = (f"📢 <b>اختبار #{quiz_id}</b> محفوظ.\n\nلا توجد وجهة نشر لك بعد. "
                "أضف البوت <b>مشرفاً</b> في قناتك أو مجموعتك فتظهر هنا تلقائياً، أو اضغط «➕ إضافة قناة/مجموعة».\n\n"
                "• القنوات: يجب أن يملك البوت صلاحية نشر الرسائل.\n• المجموعات: يكفي أن يكون عضواً مسموحاً له بالاستطلاعات.")
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    if edit:
        try:
            return await message.edit_text(text, reply_markup=kb)
        except Exception:
            pass
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("quiz:pick:"))
async def quiz_pick(cb: CallbackQuery, session, state: FSMContext):
    quiz = await _owned_quiz(cb, session)
    if quiz is None:
        return
    await state.clear()
    await cb.answer()
    await _show_picker(cb.message, cb.bot, session, cb.from_user.id, quiz.id, edit=False)


@router.callback_query(F.data.startswith("quiz:addt:"))
async def quiz_add_target_start(cb: CallbackQuery, session, state: FSMContext):
    quiz = await _owned_quiz(cb, session)
    if quiz is None:
        return
    await state.set_state(QuizInput.add_target)
    await state.update_data(target_quiz_id=quiz.id)
    await cb.message.answer(
        "➕ <b>إضافة وجهة نشر</b>\n\nأرسل <code>@username</code> للقناة/المجموعة أو معرّفها الرقمي، "
        "أو <b>حوّل رسالة</b> منها إلى هنا.\nشرطان: البوت داخل الوجهة، وأنت مشرف فيها. /cancel للإلغاء.")
    await cb.answer()


@router.message(QuizInput.add_target)
async def quiz_add_target(message: Message, state: FSMContext, session):
    raw = (message.text or message.caption or "").strip()
    if raw.lower().startswith("/cancel"):
        await state.clear()
        return await message.answer("تم الإلغاء.")
    origin = getattr(message, "forward_origin", None)
    origin_chat = (getattr(origin, "chat", None) or getattr(origin, "sender_chat", None)) if origin else None
    ref = str(origin_chat.id) if origin_chat is not None else parse_chat_ref(raw)
    if not ref:
        return await message.answer("❌ لم أفهم المعرّف. أرسل @username أو المعرّف الرقمي أو حوّل رسالة من الوجهة. /cancel للإلغاء.")
    info = await inspect_channel(message.bot, ref, need_admin=False, need_post_rights=True)
    if not info.ok:
        return await message.answer(f"❌ {esc(info.problem)}\n\nصحّح ذلك وأعد الإرسال، أو /cancel.")
    if not await is_chat_admin(message.bot, info.chat_id, message.from_user.id):
        return await message.answer("⛔ يجب أن تكون مشرفاً في هذه الوجهة لتنشر فيها.")
    row = await session.scalar(select(QuizTarget).where(QuizTarget.chat_id == info.chat_id))
    if row is None:
        row = QuizTarget(chat_id=info.chat_id, added_by=message.from_user.id)
        session.add(row)
    row.kind, row.title, row.username, row.is_active = info.kind, info.title, info.username, True
    await session.commit()
    quiz_id = (await state.get_data()).get("target_quiz_id")
    await state.clear()
    await message.answer(f"✅ أُضيفت الوجهة «{esc(info.title)}».")
    if quiz_id:
        await _show_picker(message, message.bot, session, message.from_user.id, int(quiz_id), edit=False)


async def _target_for(cb: CallbackQuery, session) -> tuple[Quiz, QuizTarget] | None:
    quiz = await _owned_quiz(cb, session)
    if quiz is None:
        return None
    try:
        target = await session.get(QuizTarget, int(cb.data.split(":")[3]))
    except (ValueError, IndexError):
        target = None
    if target is None or not target.is_active:
        await cb.answer("هذه الوجهة غير متاحة.", show_alert=True)
        return None
    if not await is_chat_admin(cb.bot, target.chat_id, cb.from_user.id):
        await cb.answer("⛔ لست مشرفاً في هذه الوجهة.", show_alert=True)
        return None
    return quiz, target


@router.callback_query(F.data.startswith("quiz:pub:"))
async def quiz_publish_confirm(cb: CallbackQuery, session):
    found = await _target_for(cb, session)
    if found is None:
        return
    quiz, target = found
    kind_text = "قناة (مجهول الهوية، بلا ترتيب)" if target.kind == "channel" else "مجموعة (مع ترتيب للمشاركين)"
    await cb.answer()
    await cb.message.edit_text(
        f"📢 نشر الاختبار #{quiz.id} ({len(quiz.questions)} سؤال) في:\n<b>{esc(target.title)}</b> — {kind_text}\n\nهل تؤكد؟",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ نعم، انشر", callback_data=f"quiz:go:{quiz.id}:{target.id}"),
             InlineKeyboardButton(text="⬅️ رجوع", callback_data=f"quiz:pick:{quiz.id}")]]))


@router.callback_query(F.data.startswith("quiz:go:"))
async def quiz_publish_go(cb: CallbackQuery, session):
    found = await _target_for(cb, session)
    if found is None:
        return
    quiz, target = found
    error = await _launch_publish(cb.bot, quiz.id, target.chat_id, target.kind, target.title, cb.from_user.id, cb.from_user.id)
    if error:
        return await cb.answer(error[:190], show_alert=True)
    seconds = round(len(quiz.questions) * SHARED_DELAY)
    await cb.answer("بدأ النشر")
    await cb.message.edit_text(f"⏳ جارٍ نشر الاختبار #{quiz.id} في <b>{esc(target.title)}</b> "
                               f"(نحو {max(1, seconds // 60)} دقيقة بسبب حدود تيليجرام). سأخبرك عند الانتهاء.")


# ----------------------------------------------------------------------------- poll answers

async def _record_private_answer(session, bot: Bot, poll: QuizPoll, voter, chosen: int) -> None:
    attempt = await session.get(QuizAttempt, poll.attempt_id)
    if not attempt or attempt.user_telegram_id != voter.id or attempt.status not in {"active", "partial"}:
        return
    existing = await session.scalar(select(QuizResponse.id).where(
        QuizResponse.attempt_id == attempt.id, QuizResponse.question_number == poll.question_number))
    if existing:
        return
    session.add(QuizResponse(attempt_id=attempt.id, question_number=poll.question_number,
                             selected_option_index=chosen, is_correct=chosen == poll.correct_option_index))
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return
    await session.execute(update(QuizAttempt).where(
        QuizAttempt.id == attempt.id, QuizAttempt.status.in_(["active", "partial"])
    ).values(answered_count=QuizAttempt.answered_count + 1,
             correct_count=QuizAttempt.correct_count + (1 if chosen == poll.correct_option_index else 0))
        .execution_options(synchronize_session=False))
    await session.refresh(attempt)
    done = attempt.total_questions > 0 and attempt.answered_count == attempt.total_questions
    if done:
        attempt.status = "completed"
        attempt.finished_at = utcnow()
    score, total = attempt.correct_count, attempt.total_questions
    await session.commit()
    if done:
        pct = round(score * 100 / total) if total else 0
        try:
            await bot.send_message(voter.id, f"🏁 <b>انتهى الاختبار</b>\n\n✅ الصحيحة: <b>{score}</b> من <b>{total}</b>\n📊 النتيجة: <b>{pct}%</b>")
        except Exception:
            log.exception("Could not send test result to user=%s", voter.id)


async def _record_session_answer(session, sp: QuizSessionPoll, voter, chosen: int) -> None:
    qs = await session.get(QuizSession, sp.session_id)
    if qs is None or qs.status not in {"publishing", "active"}:
        return
    session.add(QuizSessionAnswer(session_id=qs.id, user_telegram_id=voter.id, user_name=(voter.full_name or "")[:200],
                                  question_number=sp.question_number, selected_option_index=chosen,
                                  is_correct=chosen == sp.correct_option_index))
    try:
        await session.commit()
    except IntegrityError:  # same user answering the same question twice
        await session.rollback()


@router.poll_answer()
async def collect_poll_answer(poll_answer: PollAnswer, bot: Bot):
    voter = poll_answer.user
    if voter is None or not poll_answer.option_ids:
        return
    chosen = poll_answer.option_ids[0]
    async with Session() as session:
        poll = await session.get(QuizPoll, poll_answer.poll_id)
        if poll is not None:
            return await _record_private_answer(session, bot, poll, voter, chosen)
        session_poll = await session.get(QuizSessionPoll, poll_answer.poll_id)
        if session_poll is not None:
            await _record_session_answer(session, session_poll, voter, chosen)
