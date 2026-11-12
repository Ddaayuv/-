from datetime import datetime

import pytest

from bot.group_quiz_logic import (
    can_start_quiz,
    local_schedule_to_utc,
    next_schedule_time,
    rank_leaderboard,
)


def test_group_start_policies():
    assert can_start_quiz("owner_only", "creator")
    assert not can_start_quiz("owner_only", "administrator", user_id=7)
    assert can_start_quiz("all_admins", "administrator")
    assert not can_start_quiz("all_admins", "member")
    assert can_start_quiz("selected", "administrator", user_id=7, allowed_admin_ids={7})
    assert not can_start_quiz("selected", "administrator", user_id=8, allowed_admin_ids={7})


def test_disabled_and_anonymous_policy():
    assert not can_start_quiz("all_admins", "administrator", enabled=False)
    assert can_start_quiz("all_admins", "unknown", anonymous_admin=True)
    assert not can_start_quiz("owner_only", "unknown", anonymous_admin=True)


def test_leaderboard_order():
    rows = [(3, "C", 2, 4), (2, "B", 4, 4), (1, "A", 4, 3)]
    assert [r[0] for r in rank_leaderboard(rows)] == [2, 1, 3]


def test_schedule_timezone_and_recurrence():
    local = datetime(2026, 11, 12, 10, 0)
    # Baghdad is UTC+3 on this date.
    assert local_schedule_to_utc(local, "Asia/Baghdad") == datetime(2026, 11, 12, 7, 0)
    assert next_schedule_time(local, "daily") == datetime(2026, 11, 13, 10, 0)
    assert next_schedule_time(local, "weekly") == datetime(2026, 11, 19, 10, 0)
    assert next_schedule_time(local, "none") is None


def test_invalid_timezone_or_repeat():
    with pytest.raises(Exception):
        local_schedule_to_utc(datetime(2026, 1, 1), "not/a-timezone")
    with pytest.raises(ValueError):
        next_schedule_time(datetime(2026, 1, 1), "monthly")
