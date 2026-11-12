from datetime import datetime, timedelta
from types import SimpleNamespace

from bot.middleware_logic import is_private_chat, should_refresh_last_active


def test_only_private_chat_is_tracked_as_a_user():
    assert is_private_chat(SimpleNamespace(type="private"))
    assert not is_private_chat(SimpleNamespace(type="group"))
    assert not is_private_chat(SimpleNamespace(type="supergroup"))
    assert not is_private_chat(None)


def test_activity_timestamp_is_rate_limited():
    now = datetime(2026, 11, 12, 10, 0, 0)
    last_active = now - timedelta(seconds=120)

    assert not should_refresh_last_active(last_active, now, 300)
    assert should_refresh_last_active(last_active, now, 120)
    assert should_refresh_last_active(None, now, 300)
