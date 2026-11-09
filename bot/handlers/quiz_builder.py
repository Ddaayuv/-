"""Guided quiz builder, drafts, and JSON/CSV/XLSX import."""
from __future__ import annotations

import csv
import io
import json
import logging
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import func, select

from ..db import Quiz, QuizAttempt, QuizQuestion, QuizSession, Session
from ..quiz_parser import parse_quiz_text
from ..utils import esc

router = Router()
log = logging.getLogger(__name__)


class QuizBuild(StatesGroup):
    title = State()
    description = State()
    passing_score = State()
    duration = State()
    attempts = State()
    question_text = State()
    question_image = State()
    options = State()
    correct_text = State()
    explanation = State()
    points = State()
    import_file = State()


def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _cancel_kb() -> InlineKeyboardMarkup:
    return _kb([[_button("❌ إلغاء", "qbuild:cancel")]])


def _ask(message: Message, text: str, keyboard: InlineKeyboardMarkup | None = None):
    return message.answer(text, reply_markup=keyboard or _cancel_kb())


def _normalise_question(raw: dict[str, Any], number: int) -> dict[str, Any]:
    question_text = str(raw.get("question") or raw.get("text") or "").strip()
    qtype = str(raw.get("question_type") or raw.get("type") or "mcq").strip().lower()
    aliases = {
        "multiple_choice": "mcq", "multiple-choice": "mcq", "quiz": "mcq",
        "true_false": "true_false", "true/false": "true_false", "tf": "true_false",
        "text": "text", "short_answer": "text", "photo": "photo", "image": "photo",
    }
    qtype = aliases.get(qtype, qtype)
    if not question_text:
        raise ValueError(f"السؤال {number}: نص السؤال فارغ.")
    if qtype not in {"mcq", "true_false", "text", "photo"}:
        raise ValueError(f"السؤال {number}: نوع السؤال غير مدعوم ({qtype}).")

    options = raw.get("options", [])
    if isinstance(options, str):
        options = [part.strip() for part in options.replace(";", "|").split("|") if part.strip()]
    if isinstance(options, list):
        options = [
            str(value.get("text", "")).strip() if isinstance(value, dict) else str(value).strip()
            for value in options
        ]
    else:
        raise ValueError(f"السؤال {number}: الخيارات يجب أن تكون قائمة أو نصاً مفصولاً بعلامة |.")

    correct_raw = raw.get("correct_option_index", raw.get("correct_index"))
    correct_text = raw.get("correct_text_answer", raw.get("correct_answer", raw.get("answer")))
    correct_index = -1
    if qtype == "true_false":
        options = ["صح", "خطأ"]
        value = str(correct_text or "").strip().lower()
        if value in {"صح", "صحيح", "true", "yes", "1", "أ"}:
            correct_index = 0
        elif value in {"خطأ", "خاطئ", "false", "no", "0", "ب"}:
            correct_index = 1
        elif correct_raw is not None:
            correct_index = int(correct_raw)
    elif qtype in {"mcq", "photo"}:
        options = [str(value).strip() for value in options]
        if not 2 <= len(options) <= 6 or any(not value for value in options):
            raise ValueError(f"السؤال {number}: يجب توفير 2 إلى 6 خيارات غير فارغة.")
        if correct_raw is None and correct_text is not None:
            label = str(correct_text).strip().upper()
            if label in {"A", "B", "C", "D", "E", "F"}:
                correct_index = ord(label) - ord("A")
            elif label in {"أ", "ب", "ج", "د", "هـ", "و"}:
                correct_index = ["أ", "ب", "ج", "د", "هـ", "و"].index(label)
            else:
                matches = [i for i, value in enumerate(options) if value == str(correct_text).strip()]
                if len(matches) == 1:
                    correct_index = matches[0]
        elif correct_raw is not None:
            correct_index = int(correct_raw)
    else:
        correct_text = str(correct_text or "").strip()
        if not correct_text:
            raise ValueError(f"السؤال {number}: الإجابة النصية الصحيحة مطلوبة.")

    if qtype in {"mcq", "photo", "true_false"} and not 0 <= correct_index < len(options):
        raise ValueError(f"السؤال {number}: رقم الإجابة الصحيحة غير صالح.")
    points = float(raw.get("points", 1) or 1)
    if points <= 0 or points > 1000:
        raise ValueError(f"السؤال {number}: الدرجة يجب أن تكون أكبر من صفر ولا تتجاوز 1000.")

    image_file_id = raw.get("image_file_id")
    return {
        "number": number,
        "question": question_text[:3000],
        "question_type": qtype,
        "options": [{"label": chr(65 + i), "text": value[:100]} for i, value in enumerate(options)],
        "correct_option_index": correct_index,
        "correct_text_answer": str(correct_text).strip() if qtype == "text" else None,
        "explanation": str(raw.get("explanation") or "").strip()[:200] or None,
        "points": points,
        "image_file_id": str(image_file_id) if image_file_id else None,
        "time_limit_seconds": (
            int(raw["time_limit_seconds"])
            if raw.get("time_limit_seconds") not in (None, "", 0, "0")
            else None
        ),
    }


def _parse_import_bytes(filename: str, content: bytes) -> tuple[str, list[dict[str, Any]]]:
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    title = "اختبار مستورد"
    if suffix == "json":
        data = json.loads(content.decode("utf-8-sig"))
        if isinstance(data, list):
            questions_raw = data
        elif isinstance(data, dict):
            title = str(data.get("title") or title)
            questions_raw = data.get("questions")
        else:
            raise ValueError("ملف JSON يجب أن يحتوي قائمة أسئلة أو كائناً به questions.")
    elif suffix == "csv":
        decoded = content.decode("utf-8-sig")
        questions_raw = list(csv.DictReader(io.StringIO(decoded)))
    elif suffix in {"xlsx", "xlsm"}:
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise ValueError("مكتبة openpyxl غير مثبتة؛ حدّث requirements.txt ثم أعد التشغيل.") from exc
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        sheet = workbook.active
        rows = sheet.iter_rows(values_only=True)
        headers = [str(value or "").strip().lower() for value in next(rows, ())]
        questions_raw = [
            {headers[i]: row[i] for i in range(min(len(headers), len(row)))}
            for row in rows
            if any(value is not None for value in row)
        ]
        workbook.close()
    elif suffix in {"txt", "text"}:
        parsed = parse_quiz_text(content.decode("utf-8-sig"))
        if parsed.get("issues") or not parsed.get("questions"):
            raise ValueError("تعذر تحليل الملف النصي:\n" + "\n".join(parsed.get("issues", [])[:10]))
        title = parsed.get("title") or title
        questions_raw = [
            {
                "question": item["text"],
                "type": "mcq",
                "options": [option["text"] for option in item["options"]],
                "correct_option_index": item["correct_option_index"],
                "explanation": item.get("explanation"),
                "points": 1,
            }
            for item in parsed["questions"]
        ]
    else:
        raise ValueError("الامتدادات المدعومة: JSON وCSV وXLSX وTXT.")

    if not isinstance(questions_raw, list) or not questions_raw:
        raise ValueError("لم أجد أسئلة داخل الملف.")
    if len(questions_raw) > 100:
        raise ValueError("الحد الأقصى للاستيراد 100 سؤال في الملف الواحد.")
    questions = [_normalise_question(row, index + 1) for index, row in enumerate(questions_raw)]
    return title[:200], questions


def _questions_from_data(data: dict[str, Any]) -> list[dict[str, Any]]:
    return data.get("build_questions", [])


def _manage_markup(data: dict[str, Any]) -> InlineKeyboardMarkup:
    questions = _questions_from_data(data)
    rows = [
        [_button("➕ إضافة سؤال", "qbuild:add_question"),
         _button("👁 معاينة", "qbuild:preview")],
        [_button("🔀 تبديل عشوائية الأسئلة", "qbuild:toggle_questions"),
         _button("🎲 تبديل عشوائية الخيارات", "qbuild:toggle_options")],
        [_button("💾 حفظ كمسودة", "qbuild:save_draft"),
         _button("✅ حفظ الاختبار", "qbuild:save")],
        [_button("📁 استيراد أسئلة", "qbuild:import"),
         _button("❌ إلغاء", "qbuild:cancel")],
    ]
    if questions:
        for index, item in enumerate(questions[-5:]):
            absolute = len(questions) - len(questions[-5:]) + index
            rows.insert(-2, [
                _button(f"🗑 حذف السؤال {absolute + 1}", f"qbuild:remove:{absolute}")
            ])
    return _kb(rows)


async def _show_manage(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    questions = _questions_from_data(data)
    total = sum(float(item.get("points", 1)) for item in questions)
    text = (
        f"🧩 <b>بناء الاختبار</b>\n\n"
        f"العنوان: <b>{esc(data.get('build_title', ''))}</b>\n"
        f"عدد الأسئلة: <b>{len(questions)}</b>\n"
        f"مجموع الدرجات: <b>{total:g}</b>\n"
        f"درجة النجاح: <b>{float(data.get('passing_score', 50)):g}%</b>\n"
        f"المدة: <b>{'بلا حد' if not data.get('time_limit_seconds') else str(data['time_limit_seconds']//60)+' دقيقة'}</b>\n"
        f"المحاولات: <b>{data.get('max_attempts') or 'غير محدودة'}</b>\n\n"
        "أضف الأسئلة ثم اعرض المعاينة واحفظ الاختبار."
    )
    await message.answer(text, reply_markup=_manage_markup(data))


async def _ask_question_type(callback: CallbackQuery) -> None:
    await callback.message.answer(
        "اختر نوع السؤال:",
        reply_markup=_kb([
            [_button("اختيار من متعدد", "qbuild:type:mcq"),
             _button("صح / خطأ", "qbuild:type:true_false")],
            [_button("إجابة نصية", "qbuild:type:text"),
             _button("سؤال مع صورة", "qbuild:type:photo")],
            [_button("❌ إلغاء", "qbuild:cancel")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data == "quiz:wizard")
async def start_builder(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.update_data(
        build_title="",
        build_description="",
        passing_score=50.0,
        time_limit_seconds=None,
        max_attempts=None,
        randomize_questions=False,
        randomize_options=False,
        build_questions=[],
    )
    await state.set_state(QuizBuild.title)
    await callback.message.answer(
        "🧩 <b>إنشاء اختبار جديد</b>\n\nأرسل عنوان الاختبار (حتى 200 حرف).",
        reply_markup=_cancel_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "qbuild:cancel")
async def cancel_builder(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        await callback.message.edit_text("❌ تم إلغاء إنشاء الاختبار.")
    except Exception:
        await callback.message.answer("❌ تم إلغاء إنشاء الاختبار.")
    await callback.answer("تم الإلغاء")


@router.message(Command("importquiz"))
async def import_quiz_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(QuizBuild.import_file)
    await message.answer(
        "📁 أرسل ملف JSON أو CSV أو XLSX أو TXT لاستيراد الأسئلة.\n"
        "أرسل /cancel للإلغاء.",
        reply_markup=_cancel_kb(),
    )


@router.message(QuizBuild.title)
async def receive_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if title.lower() == "/cancel":
        await state.clear()
        return await message.answer("تم الإلغاء.")
    if not title or len(title) > 200:
        return await message.answer("العنوان مطلوب وبحد أقصى 200 حرف.")
    await state.update_data(build_title=title)
    await state.set_state(QuizBuild.description)
    await _ask(message, "أرسل وصف الاختبار، أو اضغط تخطي.", _kb([
        [_button("⏭ تخطي الوصف", "qbuild:skip_description")],
        [_button("❌ إلغاء", "qbuild:cancel")],
    ]))


@router.callback_query(F.data == "qbuild:skip_description")
async def skip_description(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(build_description="")
    await state.set_state(QuizBuild.passing_score)
    await callback.message.answer("أرسل درجة النجاح كنسبة مئوية من 0 إلى 100 (مثلاً 50).")
    await callback.answer()


@router.message(QuizBuild.description)
async def receive_description(message: Message, state: FSMContext) -> None:
    description = (message.text or "").strip()
    if description.lower() == "/cancel":
        await state.clear()
        return await message.answer("تم الإلغاء.")
    await state.update_data(build_description=description[:2000])
    await state.set_state(QuizBuild.passing_score)
    await message.answer("أرسل درجة النجاح كنسبة مئوية من 0 إلى 100 (مثلاً 50).")


@router.message(QuizBuild.passing_score)
async def receive_passing_score(message: Message, state: FSMContext) -> None:
    try:
        value = float((message.text or "").strip().replace(",", "."))
        if not 0 <= value <= 100:
            raise ValueError
    except ValueError:
        return await message.answer("أرسل رقماً من 0 إلى 100.")
    await state.update_data(passing_score=value)
    await state.set_state(QuizBuild.duration)
    await message.answer("أرسل مدة الاختبار بالدقائق. أرسل 0 لجعله بلا مؤقت.")


@router.message(QuizBuild.duration)
async def receive_duration(message: Message, state: FSMContext) -> None:
    try:
        minutes = int((message.text or "").strip())
        if not 0 <= minutes <= 10080:
            raise ValueError
    except ValueError:
        return await message.answer("أرسل عدداً صحيحاً من 0 إلى 10080 دقيقة.")
    await state.update_data(time_limit_seconds=minutes * 60 if minutes else None)
    await state.set_state(QuizBuild.attempts)
    await message.answer("كم محاولة مسموحة لكل مستخدم؟ أرسل 0 للمحاولات غير المحدودة.")


@router.message(QuizBuild.attempts)
async def receive_attempts(message: Message, state: FSMContext) -> None:
    try:
        count = int((message.text or "").strip())
        if not 0 <= count <= 100:
            raise ValueError
    except ValueError:
        return await message.answer("أرسل عدداً صحيحاً من 0 إلى 100.")
    await state.update_data(max_attempts=count or None)
    await state.set_state(None)
    await _show_manage(message, state)


@router.callback_query(F.data == "qbuild:add_question")
async def add_question_start(callback: CallbackQuery) -> None:
    await _ask_question_type(callback)


@router.callback_query(F.data.startswith("qbuild:type:"))
async def choose_question_type(callback: CallbackQuery, state: FSMContext) -> None:
    qtype = callback.data.rsplit(":", 1)[1]
    if qtype not in {"mcq", "true_false", "text", "photo"}:
        return await callback.answer("نوع السؤال غير صالح.", show_alert=True)
    await state.update_data(current_question={
        "question_type": qtype,
        "points": 1.0,
        "explanation": None,
        "image_file_id": None,
    })
    await state.set_state(QuizBuild.question_text)
    await callback.message.answer("أرسل نص السؤال (حتى 3000 حرف).", reply_markup=_cancel_kb())
    await callback.answer()


@router.message(QuizBuild.question_text)
async def receive_question_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text or len(text) > 3000:
        return await message.answer("نص السؤال مطلوب وبحد أقصى 3000 حرف.")
    data = await state.get_data()
    question = dict(data.get("current_question") or {})
    question["question"] = text
    await state.update_data(current_question=question)
    if question["question_type"] == "photo":
        await state.set_state(QuizBuild.question_image)
        return await _ask(message, "أرسل صورة السؤال الآن.", _kb([
            [_button("⏭ بدون صورة", "qbuild:skip_image")],
            [_button("❌ إلغاء", "qbuild:cancel")],
        ]))
    await _continue_question(message, state)


@router.callback_query(F.data == "qbuild:skip_image")
async def skip_image(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(QuizBuild.options)
    await callback.message.answer("أرسل 2 إلى 6 خيارات، كل خيار في سطر مستقل.")
    await callback.answer()


@router.message(QuizBuild.question_image, F.photo)
async def receive_question_image(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    question = dict(data.get("current_question") or {})
    question["image_file_id"] = message.photo[-1].file_id
    await state.update_data(current_question=question)
    await state.set_state(QuizBuild.options)
    await message.answer("أرسل 2 إلى 6 خيارات، كل خيار في سطر مستقل.")


async def _continue_question(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    question = dict(data.get("current_question") or {})
    qtype = question.get("question_type")
    if qtype in {"mcq", "photo"}:
        await state.set_state(QuizBuild.options)
        await message.answer("أرسل 2 إلى 6 خيارات، كل خيار في سطر مستقل.")
    elif qtype == "true_false":
        question["options"] = [{"label": "A", "text": "صح"}, {"label": "B", "text": "خطأ"}]
        await state.update_data(current_question=question)
        await state.set_state(None)
        await message.answer(
            "اختر الإجابة الصحيحة:",
            reply_markup=_kb([
                [_button("صح", "qbuild:true_false:0"),
                 _button("خطأ", "qbuild:true_false:1")],
                [_button("❌ إلغاء", "qbuild:cancel")],
            ]),
        )
    else:
        await state.set_state(QuizBuild.correct_text)
        await message.answer("أرسل الإجابة النصية الصحيحة (يتم التصحيح بالمطابقة بعد توحيد المسافات والحروف).")


@router.message(QuizBuild.options)
async def receive_options(message: Message, state: FSMContext) -> None:
    options = [line.strip() for line in (message.text or "").splitlines() if line.strip()]
    if not 2 <= len(options) <= 6:
        return await message.answer("يجب إرسال خيارين إلى 6 خيارات، كل خيار في سطر مستقل.")
    if len({value.casefold() for value in options}) != len(options):
        return await message.answer("هناك خيارات مكررة. أعد إرسال الخيارات دون تكرار.")
    data = await state.get_data()
    question = dict(data.get("current_question") or {})
    question["options"] = [
        {"label": chr(65 + index), "text": value[:100]}
        for index, value in enumerate(options)
    ]
    await state.update_data(current_question=question)
    await state.set_state(QuizBuild.correct_text)
    await message.answer("أرسل حرف الإجابة الصحيحة A/B/C... أو رقمها من 1 إلى عدد الخيارات.")


@router.message(QuizBuild.correct_text)
async def receive_correct_text(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    data = await state.get_data()
    question = dict(data.get("current_question") or {})
    if question.get("question_type") == "text":
        if not raw:
            return await message.answer("الإجابة النصية الصحيحة مطلوبة.")
        question["correct_text_answer"] = raw[:500]
    else:
        options = question.get("options", [])
        labels = [str(item["label"]).upper() for item in options]
        value = raw.upper()
        if value.isdigit():
            index = int(value) - 1
        elif value in labels:
            index = labels.index(value)
        else:
            return await message.answer("أرسل حرف الخيار (A/B/...) أو رقمه من 1.")
        if not 0 <= index < len(options):
            return await message.answer("رقم الإجابة خارج نطاق الخيارات.")
        question["correct_option_index"] = index
    await state.update_data(current_question=question)
    await state.set_state(QuizBuild.explanation)
    await _ask(message, "أرسل شرح الإجابة، أو اضغط تخطي.", _kb([
        [_button("⏭ تخطي الشرح", "qbuild:skip_explanation")],
        [_button("❌ إلغاء", "qbuild:cancel")],
    ]))


@router.callback_query(F.data == "qbuild:true_false:0")
@router.callback_query(F.data == "qbuild:true_false:1")
async def set_true_false_answer(callback: CallbackQuery, state: FSMContext) -> None:
    index = int(callback.data.rsplit(":", 1)[1])
    data = await state.get_data()
    question = dict(data.get("current_question") or {})
    question["correct_option_index"] = index
    await state.update_data(current_question=question)
    await state.set_state(QuizBuild.explanation)
    await callback.message.answer(
        "أرسل شرح الإجابة أو اضغط تخطي.",
        reply_markup=_kb([
            [_button("⏭ تخطي الشرح", "qbuild:skip_explanation")],
            [_button("❌ إلغاء", "qbuild:cancel")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data == "qbuild:skip_explanation")
async def skip_explanation(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(current_explanation=None)
    await state.set_state(QuizBuild.points)
    await callback.message.answer("أرسل درجة السؤال (مثلاً 1 أو 2.5).")
    await callback.answer()


@router.message(QuizBuild.explanation)
async def receive_explanation(message: Message, state: FSMContext) -> None:
    await state.update_data(current_explanation=(message.text or "").strip()[:200] or None)
    await state.set_state(QuizBuild.points)
    await message.answer("أرسل درجة السؤال (مثلاً 1 أو 2.5).")


@router.message(QuizBuild.points)
async def receive_points(message: Message, state: FSMContext) -> None:
    try:
        points = float((message.text or "").strip().replace(",", "."))
        if not 0 < points <= 1000:
            raise ValueError
    except ValueError:
        return await message.answer("أرسل درجة أكبر من صفر ولا تتجاوز 1000.")
    data = await state.get_data()
    question = dict(data.get("current_question") or {})
    question["points"] = points
    question["explanation"] = data.get("current_explanation")
    questions = list(data.get("build_questions") or [])
    if len(questions) >= 100:
        await state.set_state(None)
        return await message.answer("وصلت إلى الحد الأقصى وهو 100 سؤال.")
    questions.append(question)
    await state.update_data(
        build_questions=questions,
        current_question=None,
        current_explanation=None,
    )
    await state.set_state(None)
    await _show_manage(message, state)


@router.callback_query(F.data.startswith("qbuild:remove:"))
async def remove_question(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        index = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        return await callback.answer("رقم السؤال غير صالح.", show_alert=True)
    data = await state.get_data()
    questions = list(data.get("build_questions") or [])
    if not 0 <= index < len(questions):
        return await callback.answer("السؤال غير موجود.", show_alert=True)
    questions.pop(index)
    await state.update_data(build_questions=questions)
    await callback.answer("تم حذف السؤال من المسودة الحالية.")
    await _show_manage(callback.message, state)


@router.callback_query(F.data == "qbuild:toggle_questions")
async def toggle_question_random(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    value = not bool(data.get("randomize_questions", False))
    await state.update_data(randomize_questions=value)
    await callback.answer("عشوائية الأسئلة " + ("مفعّلة" if value else "متوقفة"))
    await _show_manage(callback.message, state)


@router.callback_query(F.data == "qbuild:toggle_options")
async def toggle_option_random(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    value = not bool(data.get("randomize_options", False))
    await state.update_data(randomize_options=value)
    await callback.answer("عشوائية الخيارات " + ("مفعّلة" if value else "متوقفة"))
    await _show_manage(callback.message, state)


@router.callback_query(F.data == "qbuild:preview")
async def preview_builder(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    questions = _questions_from_data(data)
    if not questions:
        return await callback.answer("أضف سؤالاً واحداً على الأقل.", show_alert=True)
    lines = [
        f"👁 <b>معاينة: {esc(data.get('build_title', ''))}</b>",
        esc(data.get("build_description") or ""),
        f"درجة النجاح: {float(data.get('passing_score', 50)):g}%",
    ]
    for index, item in enumerate(questions, 1):
        lines.append(f"\n<b>{index}. {esc(item['question'])}</b> — {float(item.get('points', 1)):g} درجة")
        for choice in item.get("options", []):
            lines.append(f"{esc(choice['label'])}) {esc(choice['text'])}")
        if item.get("question_type") == "text":
            lines.append("<i>سؤال بإجابة نصية</i>")
        if item.get("explanation"):
            lines.append(f"<i>💡 {esc(item['explanation'])}</i>")
        if item.get("image_file_id"):
            lines.append("🖼 يتضمن صورة")
    preview = "\n".join(lines)
    for offset in range(0, len(preview), 3500):
        await callback.message.answer(preview[offset:offset + 3500])
    await callback.answer()


async def _save_builder_state(
    user_id: int,
    state: FSMContext,
    *,
    as_draft: bool,
) -> Quiz:
    data = await state.get_data()
    questions = _questions_from_data(data)
    if not data.get("build_title") or not questions:
        raise ValueError("أدخل عنوان الاختبار وأضف سؤالاً واحداً على الأقل.")

    async with Session() as session:
        edit_id = data.get("edit_quiz_id")
        quiz = await session.get(Quiz, int(edit_id)) if edit_id else None
        if edit_id and quiz is None:
            raise ValueError("الاختبار الذي تعدّله لم يعد موجوداً.")
        if quiz is None:
            quiz = Quiz(owner_telegram_id=user_id, title=data["build_title"][:200])
            session.add(quiz)
        quiz.title = data["build_title"][:200]
        quiz.description = (data.get("build_description") or "")[:2000]
        quiz.passing_score = float(data.get("passing_score", 50))
        quiz.time_limit_seconds = data.get("time_limit_seconds")
        quiz.max_attempts = data.get("max_attempts")
        quiz.randomize_questions = bool(data.get("randomize_questions"))
        quiz.randomize_options = bool(data.get("randomize_options"))
        quiz.is_draft = as_draft
        quiz.original_text = ""
        if edit_id:
            quiz.questions.clear()
        for index, raw in enumerate(questions, 1):
            question = _normalise_question(raw, index)
            quiz.questions.append(
                QuizQuestion(
                    number=index,
                    question=question["question"],
                    options_json=json.dumps(question["options"], ensure_ascii=False),
                    correct_option_index=question["correct_option_index"],
                    explanation=question["explanation"],
                    question_type=question["question_type"],
                    points=question["points"],
                    image_file_id=question["image_file_id"],
                    correct_text_answer=question["correct_text_answer"],
                    time_limit_seconds=question["time_limit_seconds"],
                )
            )
        await session.commit()
        await session.refresh(quiz)
        return quiz


@router.callback_query(F.data == "qbuild:save")
@router.callback_query(F.data == "qbuild:save_draft")
async def save_builder(callback: CallbackQuery, state: FSMContext) -> None:
    as_draft = callback.data.endswith("save_draft")
    try:
        quiz = await _save_builder_state(callback.from_user.id, state, as_draft=as_draft)
    except Exception as exc:
        log.exception("Could not save guided quiz for user=%s", callback.from_user.id)
        return await callback.answer(str(exc)[:180], show_alert=True)
    await state.clear()
    status = "مسودة" if as_draft else "اختبار محفوظ"
    await callback.message.answer(
        f"✅ تم حفظ {status} #{quiz.id}: <b>{esc(quiz.title)}</b>\n"
        f"عدد الأسئلة: {len(quiz.questions)}",
        reply_markup=_kb([
            [_button("📚 اختباراتي", "qbuild:myquizzes")],
            [_button("➕ إنشاء اختبار آخر", "quiz:wizard")],
        ]),
    )
    await callback.answer("تم الحفظ")


@router.callback_query(F.data == "qbuild:myquizzes")
async def show_my_quizzes(callback: CallbackQuery) -> None:
    await callback.message.answer("افتح قائمة الاختبارات من خلال الأمر /myquizzes.")
    await callback.answer()


@router.callback_query(F.data == "qbuild:import")
async def start_import_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(QuizBuild.import_file)
    await callback.message.answer("أرسل ملف JSON أو CSV أو XLSX أو TXT.")
    await callback.answer()


@router.message(QuizBuild.import_file, F.document)
async def receive_import_file(message: Message, state: FSMContext) -> None:
    document = message.document
    if document.file_size and document.file_size > 10 * 1024 * 1024:
        return await message.answer("حجم الملف يجب ألا يتجاوز 10 MB.")
    buffer = io.BytesIO()
    try:
        await message.bot.download(document, destination=buffer)
        title, questions = _parse_import_bytes(document.file_name or "", buffer.getvalue())
    except Exception as exc:
        log.exception("Could not import quiz file=%s", document.file_name)
        return await message.answer(f"❌ فشل استيراد الملف: {esc(str(exc))[:500]}")
    await state.update_data(build_title=title, build_questions=questions)
    await state.set_state(None)
    await _show_manage(message, state)


@router.message(QuizBuild.import_file, F.text)
async def receive_import_text(message: Message, state: FSMContext) -> None:
    if (message.text or "").strip().lower() == "/cancel":
        await state.clear()
        return await message.answer("تم إلغاء الاستيراد.")
    parsed = parse_quiz_text(message.text or "")
    if parsed.get("issues") or not parsed.get("questions"):
        return await message.answer("تعذّر تحليل النص:\n" + "\n".join(parsed.get("issues", [])[:10]))
    questions = [
        _normalise_question(
            {
                "question": item["text"],
                "type": "mcq",
                "options": [choice["text"] for choice in item["options"]],
                "correct_option_index": item["correct_option_index"],
                "explanation": item.get("explanation"),
            },
            index + 1,
        )
        for index, item in enumerate(parsed["questions"])
    ]
    await state.update_data(
        build_title=parsed.get("title") or "اختبار مستورد",
        build_questions=questions,
    )
    await state.set_state(None)
    await _show_manage(message, state)


@router.callback_query(F.data.startswith("qbuild:resume:"))
async def resume_draft(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        quiz_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        return await callback.answer("رقم الاختبار غير صالح.", show_alert=True)
    async with Session() as session:
        quiz = await session.get(Quiz, quiz_id)
        if not quiz or quiz.owner_telegram_id != callback.from_user.id:
            return await callback.answer("هذا الاختبار غير متاح لحسابك.", show_alert=True)
        attempts = await session.scalar(
            select(func.count(QuizAttempt.id)).where(QuizAttempt.quiz_id == quiz_id)
        ) or 0
        sessions = await session.scalar(
            select(func.count(QuizSession.id)).where(QuizSession.quiz_id == quiz_id)
        ) or 0
        if attempts or sessions:
            return await callback.answer(
                "لا يمكن تعديل الأسئلة بعد وجود محاولات أو نشر. انسخ الاختبار أولاً.",
                show_alert=True,
            )
        question_data = []
        for item in quiz.questions:
            question_data.append({
                "question_type": item.question_type,
                "question": item.question,
                "options": json.loads(item.options_json or "[]"),
                "correct_option_index": item.correct_option_index,
                "correct_text_answer": item.correct_text_answer,
                "explanation": item.explanation,
                "points": item.points,
                "image_file_id": item.image_file_id,
                "time_limit_seconds": item.time_limit_seconds,
            })
        values = {
            "edit_quiz_id": quiz.id,
            "build_title": quiz.title,
            "build_description": quiz.description,
            "passing_score": quiz.passing_score,
            "time_limit_seconds": quiz.time_limit_seconds,
            "max_attempts": quiz.max_attempts,
            "randomize_questions": quiz.randomize_questions,
            "randomize_options": quiz.randomize_options,
            "build_questions": question_data,
        }
    await state.clear()
    await state.update_data(**values)
    await state.set_state(None)
    await _show_manage(callback.message, state)
    await callback.answer("تم تحميل المسودة للتعديل")
