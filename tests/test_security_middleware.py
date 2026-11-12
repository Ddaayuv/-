import asyncio
from types import SimpleNamespace

from bot.security import OwnerOnlyMiddleware


class FakeMessage:
    def __init__(self, user_id: int):
        self.from_user = SimpleNamespace(id=user_id)
        self.answers = []

    async def answer(self, text: str):
        self.answers.append(text)


class FakeCallback:
    def __init__(self, user_id: int, data: str):
        self.from_user = SimpleNamespace(id=user_id)
        self.data = data
        self.answers = []

    async def answer(self, text: str = "", show_alert: bool = False):
        self.answers.append((text, show_alert))


def _run(middleware, event):
    called = []

    async def handler(_event, _data):
        called.append(True)
        return "handled"

    result = asyncio.run(middleware(handler, event, {}))
    return result, called


def test_non_owner_message_is_blocked_before_fsm_handler():
    middleware = OwnerOnlyMiddleware({100})
    message = FakeMessage(200)

    result, called = _run(middleware, message)

    assert result is None
    assert called == []
    assert message.answers == ["⛔ هذه العملية للمالك فقط."]


def test_non_owner_admin_callback_is_blocked_with_alert():
    middleware = OwnerOnlyMiddleware({100})
    callback = FakeCallback(200, "adm:broadcast:cancel")

    result, called = _run(middleware, callback)

    assert result is None
    assert called == []
    assert callback.answers == [("⛔ هذه العملية للمالك فقط.", True)]


def test_owner_can_reach_admin_handler():
    middleware = OwnerOnlyMiddleware({100})
    message = FakeMessage(100)

    result, called = _run(middleware, message)

    assert result == "handled"
    assert called == [True]


def test_public_message_button_callback_remains_available():
    middleware = OwnerOnlyMiddleware({100})
    callback = FakeCallback(200, "mb:12")

    result, called = _run(middleware, callback)

    assert result == "handled"
    assert called == [True]
