"""Deterministic parser for plain-text multiple-choice quizzes; never invents content."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_ORDINALS = r"(?:الأول|الاول|الأولى|الاولى|الثاني|الثانية|الثالث|الثالثة|الرابع|الرابعة|الخامس|الخامسة|السادس|السادسة|السابع|السابعة|الثامن|الثامنة|التاسع|التاسعة|العاشر|العاشرة|\d+)"
_QUESTION_START = re.compile(
    rf"^\s*(?:(?:السؤال)\s*(?:{_ORDINALS})?\s*[:：\-]?\s*|"
    r"س\s*[0-9٠-٩]+\s*[:：.)\-]\s*|"
    r"(?:Q|Question)\s*[0-9٠-٩]*\s*[:：.)\-]\s*|"
    r"[0-9٠-٩]+\s*[-.)]\s*)(.*)$",
    re.IGNORECASE,
)
_OPTION_START = re.compile(r"^\s*(?P<label>[A-Za-z]|[ء-ي]{1,2})\s*[).．.：:\-]\s*(?P<text>.*?)\s*$")
_ANSWER_START = re.compile(
    r"^\s*(?:الإجابة\s*الصحيحة|الاجابة\s*الصحيحة|الإجابة|الاجابة|correct(?:\s+answer)?)\s*[:：=\-]\s*(.*?)\s*$",
    re.IGNORECASE,
)

_AR_LABELS = {"أ": "A", "ا": "A", "إ": "A", "آ": "A", "ب": "B", "ج": "C", "د": "D", "ه": "E", "هـ": "E", "ة": "E", "و": "F"}
_EN_LABELS = {"A": "A", "B": "B", "C": "C", "D": "D", "E": "E", "F": "F"}


def _clean_line(value: str) -> str:
    return unicodedata.normalize("NFKC", (value or "").translate(_ARABIC_DIGITS)).strip()


def _canonical_label(label: str) -> str | None:
    value = unicodedata.normalize("NFKC", (label or "").strip()).upper()
    return _EN_LABELS.get(value) or _AR_LABELS.get(value)


def _key(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").translate(_ARABIC_DIGITS).lower()
    value = re.sub(r"[إأآٱ]", "ا", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _split_blocks(raw: str) -> list[list[str]]:
    lines = [_clean_line(line) for line in (raw or "").replace("\r", "\n").split("\n")]
    blocks: list[list[str]] = []
    current: list[str] = []
    saw_explicit_start = False
    for line in lines:
        match = _QUESTION_START.match(line)
        if match:
            if current and any(x.strip() for x in current):
                blocks.append(current)
            current = []
            remainder = (match.group(1) or "").strip()
            if remainder:
                current.append(remainder)
            saw_explicit_start = True
        else:
            current.append(line)
    if current and any(x.strip() for x in current):
        blocks.append(current)
    if not saw_explicit_start:
        return [lines] if any(line.strip() for line in lines) else []
    return blocks


def _explicit_question_numbers(raw: str) -> list[int]:
    numbers: list[int] = []
    patterns = [
        re.compile(r"^\s*([0-9٠-٩]+)\s*[-.)]"),
        re.compile(r"^\s*س\s*([0-9٠-٩]+)\s*[:：.)\-]", re.I),
        re.compile(r"^\s*Q\s*([0-9٠-٩]+)\s*[:：.)\-]", re.I),
        re.compile(r"^\s*Question\s*([0-9٠-٩]+)\s*[:：.)\-]", re.I),
        re.compile(r"^\s*السؤال\s*([0-9٠-٩]+)\s*[:：.)\-]?"),
    ]
    for raw_line in (raw or "").replace("\r", "\n").split("\n"):
        line = _clean_line(raw_line)
        if not line:
            continue
        for pattern in patterns:
            match = pattern.match(line)
            if match:
                try:
                    numbers.append(int(match.group(1).translate(_ARABIC_DIGITS)))
                except ValueError:
                    pass
                break
    return numbers


def parse_quiz_text(raw: str) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw.strip():
        return {"questions": [], "issues": ["أرسل نص الأسئلة أولاً."], "detected_count": 0, "valid_count": 0}
    blocks = _split_blocks(raw)
    questions: list[dict[str, Any]] = []
    issues: list[str] = []
    for number, lines in enumerate(blocks, 1):
        stem_lines: list[str] = []
        options: list[dict[str, str]] = []
        correct_raw: str | None = None
        after_answer = False
        for line in lines:
            if not line.strip():
                continue
            answer_match = _ANSWER_START.match(line)
            if answer_match:
                correct_raw = (answer_match.group(1) or "").strip()
                after_answer = True
                continue
            option_match = _OPTION_START.match(line)
            if option_match and not after_answer:
                label = (option_match.group("label") or "").strip()
                option_text = (option_match.group("text") or "").strip()
                canonical = _canonical_label(label)
                options.append({"label": label, "canonical_label": canonical or "", "text": option_text})
                continue
            if options and not after_answer:
                options[-1]["text"] = (options[-1]["text"] + " " + line.strip()).strip()
            elif not after_answer:
                stem_lines.append(line.strip())
            elif line.strip():
                issues.append(f"السؤال {number}: يوجد نص بعد سطر الإجابة الصحيحة، راجعه يدوياً.")
        question_text = " ".join(stem_lines).strip()
        item_issues: list[str] = []
        if not question_text:
            item_issues.append("نص السؤال فارغ أو لم يُكتشف")
        if len(options) < 2:
            item_issues.append("يجب وجود اختيارين على الأقل")
        if len(options) > 6:
            item_issues.append("عدد الاختيارات أكبر من 6؛ يدعم هذا المحلل من 2 إلى 6")
        if len(question_text) > 300:
            item_issues.append("نص السؤال يتجاوز 300 حرف، وهو حد سؤال Telegram poll")
        labels_seen: set[str] = set()
        option_text_seen: set[str] = set()
        for option in options:
            label = option["canonical_label"]
            if not label:
                item_issues.append(f"معرّف الاختيار «{option['label']}» غير مدعوم؛ استخدم أ/ب/ج/د/هـ/و أو A-F")
            elif label in labels_seen:
                item_issues.append(f"معرّف الاختيار «{option['label']}» مكرر")
            labels_seen.add(label)
            text_value = option["text"]
            if not text_value:
                item_issues.append(f"نص الاختيار «{option['label']}» فارغ")
            if len(text_value) > 100:
                item_issues.append(f"نص الاختيار «{option['label']}» يتجاوز 100 حرف، وهو حد Telegram poll")
            normalized = _key(text_value)
            if normalized and normalized in option_text_seen:
                item_issues.append(f"يوجد اختيار مكرر بالنص في السؤال {number}")
            option_text_seen.add(normalized)
        correct_index: int | None = None
        if correct_raw is None or not correct_raw:
            item_issues.append("الإجابة الصحيحة غير محددة")
        else:
            answer_label = re.sub(r"^[\[(\s]+|[\])\s.،,]+$", "", correct_raw).strip()
            canonical = _canonical_label(answer_label)
            if canonical:
                matching = [i for i, option in enumerate(options) if option["canonical_label"] == canonical]
                if len(matching) == 1:
                    correct_index = matching[0]
                else:
                    item_issues.append(f"الإجابة «{correct_raw}» لا تشير إلى اختيار واحد موجود")
            else:
                matching = [i for i, option in enumerate(options) if _key(option["text"]) == _key(correct_raw)]
                if len(matching) == 1:
                    correct_index = matching[0]
                else:
                    item_issues.append(f"الإجابة «{correct_raw}» ليست حرف اختيار معروفاً ولا تطابق اختياراً واحداً")
        question = {
            "number": number,
            "text": question_text,
            "options": [{"label": option["label"], "text": option["text"]} for option in options],
            "correct_option_index": correct_index,
            "issues": item_issues,
        }
        questions.append(question)
        issues.extend(f"السؤال {number}: {issue}." for issue in item_issues)
    numeric_headers = _explicit_question_numbers(raw)
    if numeric_headers and numeric_headers[0] == 1:
        seen_numbers = set(numeric_headers)
        expected_numbers = set(range(1, max(numeric_headers) + 1))
        missing_numbers = sorted(expected_numbers - seen_numbers)
        if missing_numbers:
            preview = ", ".join(str(number) for number in missing_numbers[:10])
            suffix = "..." if len(missing_numbers) > 10 else ""
            issues.append(f"قد تكون أرقام الأسئلة التالية مفقودة من النص: {preview}{suffix}؛ راجع العدد والترقيم.")
        if len(numeric_headers) != len(seen_numbers):
            issues.append("يوجد رقم سؤال مكرر؛ راجع الترقيم قبل إنشاء الاختبار.")
    valid_count = sum(1 for question in questions if not question["issues"])
    return {"questions": questions, "issues": issues, "detected_count": len(blocks), "valid_count": valid_count}
