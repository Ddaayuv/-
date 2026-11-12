"""Validation helpers for versioned backups."""
from __future__ import annotations

from typing import Any

CURRENT_BACKUP_VERSION = 3
LIST_FIELDS = (
    "users", "subjects", "sections", "items", "attachments", "admins", "settings",
    "quizzes", "quiz_questions", "quiz_attempts", "quiz_polls", "quiz_responses",
    "quiz_sessions", "quiz_session_polls", "quiz_session_answers", "quiz_targets",
    "group_settings", "group_quiz_schedules", "menu_buttons",
)


def validate_backup_payload(data: Any) -> int:
    if not isinstance(data, dict):
        raise ValueError("ملف النسخة الاحتياطية يجب أن يكون كائن JSON")
    version = data.get("version", 1)
    if type(version) is not int or version < 1 or version > CURRENT_BACKUP_VERSION:
        raise ValueError(f"نسخة النسخة الاحتياطية غير مدعومة: {version!r}")
    if not isinstance(data.get("items"), list):
        raise ValueError("ملف النسخة الاحتياطية لا يحتوي قائمة items صالحة")
    for field in LIST_FIELDS:
        if field in data and not isinstance(data[field], list):
            raise ValueError(f"حقل {field} في النسخة الاحتياطية يجب أن يكون قائمة")
    return version
