"""Pure helpers for group quiz policy, ranking and schedule calculations."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def can_start_quiz(
    policy: str,
    member_status: str,
    *,
    enabled: bool = True,
    is_owner_id: bool = False,
    allowed_admin_ids: set[int] | None = None,
    user_id: int = 0,
    anonymous_admin: bool = False,
) -> bool:
    if not enabled:
        return False
    if is_owner_id:
        return True
    if anonymous_admin:
        return policy == "all_admins"
    if member_status == "creator":
        return True
    if member_status != "administrator":
        return False
    if policy == "owner_only":
        return False
    if policy == "selected":
        return user_id in (allowed_admin_ids or set())
    return policy == "all_admins"


def rank_leaderboard(rows: list[tuple[int, str, int, int]]) -> list[tuple[int, str, int, int]]:
    """Stable descending score ranking, then answered count, preserving query order for ties."""
    return sorted(rows, key=lambda row: (-int(row[2]), -int(row[3])))


def local_schedule_to_utc(value: datetime, timezone_name: str) -> datetime:
    if value.tzinfo is not None:
        raise ValueError("expected a naive local datetime")
    return value.replace(tzinfo=ZoneInfo(timezone_name)).astimezone(timezone.utc).replace(tzinfo=None)


def next_schedule_time(value: datetime, repeat_mode: str) -> datetime | None:
    if repeat_mode == "daily":
        return value + timedelta(days=1)
    if repeat_mode == "weekly":
        return value + timedelta(days=7)
    if repeat_mode == "none":
        return None
    raise ValueError("unsupported repeat mode")
