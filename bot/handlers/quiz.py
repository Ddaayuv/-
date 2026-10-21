from __future__ import annotations

import json
import logging
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, PollAnswer
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from ..db import Quiz, QuizAttempt, QuizPoll, QuizQuestion, QuizResponse, Session, utcnow
from ..quiz_parser import parse_quiz_text
from ..utils import esc

router = Router()
log = logging.getLogger(__name__)

class QuizInput(StatesGroup):
    text = State()
    confirm = State()

def quiz_prompt() -> str:
    return (
        "🤖 <b>إنشاء اختبار</b>\n\nأرسل الأسئلة كنص مع الخيارات والإجابة الصحيحة، بهذا الشكل:\n\n"
        "<code>السؤال: ما عاصمة العراق؟\nأ) بغداد\nب) البصرة\nج) الموصل\nد) أربيل\nالإجابة الصحيحة: أ</code>\n\n"
        "سأراجع النص وأعرض معاينة قبل حفظ الاختبار. لا أخمّن الإجابات. اكتب /cancel للإلغاء."
    )

def _kb(confirm: bool = False):
    rows = [[InlineKeyboardButton(text="❌ إلغاء", callback_data="quiz:cancel")]]
    if confirm:
        rows.insert(0, [InlineKeyboardButton(text="✅ حفظ وبدء الاختبار", callback_data="quiz:confirm"),
                        InlineKeyboardButton(text="✏️ تعديل النص", callback_data="quiz:edit")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

@router.callback_query(F.data == "quiz:start")
async def quiz_start(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(QuizInput.text)
    await cb.message.answer(quiz_prompt(), reply_markup=_kb())
    await cb.answer()

@router.message(Command("myquizzes"))
async def my_quizzes(message: Message, session):
    quizzes = (await session.scalars(select(Quiz).where(Quiz.owner_telegram_id == message.from_user.id)
        .order_by(Quiz.created_at.desc()).limit(10))).all()
    if not quizzes:
        return await message.answer("ما عندك اختبارات محفوظة بعد. اضغط «إنشاء اختبار».")
    buttons = [[InlineKeyboardButton(text=f"🔁 اختبار #{q.id} ({len(q.questions)} سؤال)",
                  callback_data=f"quiz:retry:{q.id}")] for q in quizzes]
    await message.answer("📚 اختباراتك المحفوظة:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.message(QuizInput.text)
async def receive_quiz_text(message: Message, state: FSMContext):
    raw = message.text or ""
    if raw.strip().lower() in {"/cancel", "إلغاء", "الغاء"}:
        await state.clear()
        return await message.answer("تم إلغاء إنشاء الاختبار.")
    parsed = parse_quiz_text(raw)
    if parsed["issues"] or not parsed["questions"] or parsed["valid_count"] != parsed["detected_count"]:
        lines = ["⚠️ لم أتمكن من اعتماد الاختبار. أصلح الأخطاء التالية ثم أرسل النص كاملاً:"]
        lines.extend("• " + issue for issue in parsed["issues"][:30])
        if not parsed["issues"]:
            lines.append("• لم أكتشف أسئلة مكتملة.")
        return await message.answer("\n".join(lines), reply_markup=_kb())
    await state.update_data(quiz_raw=raw, quiz_parsed=parsed)
    await state.set_state(QuizInput.confirm)
    lines = ["🧾 <b>معاينة الاختبار</b>"]
    for q in parsed["questions"]:
        lines.append(f"\n<b>{q['number']}. {esc(q['text'])}</b>")
        for opt in q["options"]:
            lines.append(f"{esc(opt['label'])}) {esc(opt['text'])}")
        lines.append(f"<i>الإجابة الصحيحة: {esc(q['options'][q['correct_option_index']]['label'])}</i>")
    preview = "\n".join(lines)
    for start in range(0, len(preview), 3200):
        await message.answer(preview[start:start+3200],
            reply_markup=_kb(confirm=True) if start+3200 >= len(preview) else None)

@router.message(QuizInput.confirm)
async def wait_for_confirmation(message: Message):
    if (message.text or "").strip().lower() in {"/cancel", "إلغاء", "الغاء"}:
        return await message.answer("اضغط زر الإلغاء أسفل المعاينة.")
    await message.answer("اضغط «حفظ وبدء الاختبار» أو «تعديل النص» أسفل المعاينة.")

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

async def _publish_attempt(bot, session, quiz: Quiz, user_id: int):
    questions = list(quiz.questions)
    attempt = QuizAttempt(quiz_id=quiz.id, user_telegram_id=user_id, total_questions=len(questions),
        answered_count=0, correct_count=0, status="active")
    session.add(attempt)
    await session.commit()
    await session.refresh(attempt)
    sent = 0
    for question in questions:
        options = json.loads(question.options_json)
        try:
            poll_message = await bot.send_poll(
                chat_id=user_id, question=f"{question.number}/{len(questions)} {question.question}"[:300],
                options=[str(opt["text"]) for opt in options], type="quiz", is_anonymous=False,
                correct_option_id=question.correct_option_index)
            session.add(QuizPoll(poll_id=poll_message.poll.id, attempt_id=attempt.id,
                question_number=question.number, correct_option_index=question.correct_option_index))
            await session.commit()
            sent += 1
        except Exception:
            await session.rollback()
            log.exception("Failed to send quiz question to user=%s", user_id)
            break
    if sent != len(questions):
        attempt = await session.get(QuizAttempt, attempt.id)
        attempt.total_questions = sent
        attempt.status = "partial" if sent else "failed"
        await session.commit()
        if not sent:
            await bot.send_message(user_id, "تعذّر إرسال أسئلة الاختبار. لم يتم احتساب نتيجة.")
    else:
        await bot.send_message(user_id, f"✅ بدأ الاختبار #{quiz.id}. أجب عن جميع الاستطلاعات وستظهر نتيجتك بعد آخر إجابة.")

@router.callback_query(F.data == "quiz:confirm")
async def quiz_confirm(cb: CallbackQuery, state: FSMContext, session):
    if await state.get_state() != QuizInput.confirm.state:
        return await cb.answer("انتهت المعاينة؛ أرسل الأسئلة مجدداً.", show_alert=True)
    data = await state.get_data()
    parsed, raw = data.get("quiz_parsed"), data.get("quiz_raw")
    if not isinstance(parsed, dict) or not isinstance(raw, str) or parsed.get("issues"):
        await state.clear()
        return await cb.answer("المعاينة غير صالحة. أعد إرسال النص.", show_alert=True)
    quiz = Quiz(owner_telegram_id=cb.from_user.id, title="اختبار من النص", original_text=raw)
    for q in parsed["questions"]:
        quiz.questions.append(QuizQuestion(number=q["number"], question=q["text"],
            options_json=json.dumps(q["options"], ensure_ascii=False),
            correct_option_index=q["correct_option_index"]))
    session.add(quiz)
    try:
        await session.commit()
        await session.refresh(quiz)
    except Exception:
        await session.rollback()
        log.exception("Could not save quiz")
        return await cb.answer("تعذّر حفظ الاختبار.", show_alert=True)
    await state.clear()
    try:
        await cb.message.edit_text(f"✅ تم حفظ الاختبار #{quiz.id}. جارٍ إرسال الأسئلة...")
    except Exception:
        pass
    await cb.answer("تم حفظ الاختبار")
    await _publish_attempt(cb.bot, session, quiz, cb.from_user.id)

@router.callback_query(F.data.startswith("quiz:retry:"))
async def quiz_retry(cb: CallbackQuery, session):
    try:
        quiz_id = int(cb.data.rsplit(":", 1)[1])
    except ValueError:
        return await cb.answer("رقم الاختبار غير صحيح.", show_alert=True)
    quiz = await session.get(Quiz, quiz_id)
    if not quiz or quiz.owner_telegram_id != cb.from_user.id:
        return await cb.answer("هذا الاختبار غير متاح لحسابك.", show_alert=True)
    if not quiz.questions:
        return await cb.answer("هذا الاختبار لا يحتوي أسئلة.", show_alert=True)
    await cb.answer("بدأ الاختبار")
    await _publish_attempt(cb.bot, session, quiz, cb.from_user.id)

@router.poll_answer()
async def collect_poll_answer(poll_answer: PollAnswer, bot):
    voter = poll_answer.user
    if voter is None or not poll_answer.option_ids:
        return
    async with Session() as session:
        poll = await session.get(QuizPoll, poll_answer.poll_id)
        if not poll:
            return
        attempt = await session.get(QuizAttempt, poll.attempt_id)
        if not attempt or attempt.user_telegram_id != voter.id or attempt.status not in {"active", "partial"}:
            return
        existing = await session.scalar(select(QuizResponse.id).where(
            QuizResponse.attempt_id == attempt.id, QuizResponse.question_number == poll.question_number))
        if existing:
            return
        chosen = poll_answer.option_ids[0]
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
            await bot.send_message(voter.id,
                f"🏁 <b>انتهى الاختبار</b>\n\n✅ الصحيحة: <b>{score}</b> من <b>{total}</b>\n📊 النتيجة: <b>{pct}%</b>")
        except Exception:
            log.exception("Could not send test result to user=%s", voter.id)
