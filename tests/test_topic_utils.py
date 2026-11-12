from types import SimpleNamespace

from bot.topic_utils import notification_topic_kwargs, topic_thread_id


def test_topic_message_returns_its_thread_id():
    message = SimpleNamespace(is_topic_message=True, message_thread_id=42)

    assert topic_thread_id(message) == 42


def test_regular_message_never_uses_accidental_thread_id():
    message = SimpleNamespace(is_topic_message=False, message_thread_id=42)

    assert topic_thread_id(message) is None


def test_regular_message_without_topic_fields_is_safe():
    message = SimpleNamespace()

    assert topic_thread_id(message) is None


def test_notification_uses_thread_only_when_destination_is_same_group():
    assert notification_topic_kwargs(-100123, -100123, 42) == {
        "message_thread_id": 42
    }


def test_notification_does_not_leak_thread_to_private_or_other_chat():
    assert notification_topic_kwargs(-100123, 998877, 42) == {}
    assert notification_topic_kwargs(-100123, -100123, None) == {}
