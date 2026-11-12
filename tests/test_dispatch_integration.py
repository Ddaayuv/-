"""End-to-end routing checks through a real aiogram Dispatcher with the project's real routers,
registered in the same order as bot/main.py. These guard against middleware that swallows
updates meant for the public (user) router."""
import asyncio
import datetime
from unittest.mock import patch

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from bot.handlers import admin, quiz

OWNER = 111
STRANGER = 222


def _build():
    hits: list[str] = []
    probe = Router()  # stands in for user.router: the place a normal user's update must end up

    @probe.message(F.text)
    async def _probe_message(m):
        hits.append("reached-user-router")

    @probe.callback_query()
    async def _probe_callback(c):
        hits.append("reached-user-router")

    dp = Dispatcher()
    # Same order as bot/main.py: admin, quiz, user. admin/quiz routers are module singletons,
    # so detach them again afterwards (see _run).
    dp.include_router(admin.router)
    dp.include_router(quiz.router)
    dp.include_router(probe)
    return dp, hits


def _run(event: str, uid: int, payload: str) -> list[str]:
    dp, hits = _build()

    async def denied(self, *a, **k):
        hits.append("denied")

    async def go():
        bot = Bot("123456:ABCdefGhIJKlmNoPQRsTUVwxyZ")
        user = User(id=uid, is_bot=False, first_name="x")
        chat = Chat(id=uid, type="private")
        if event == "message":
            msg = Message(message_id=1, date=datetime.datetime.now(), chat=chat, from_user=user, text=payload).as_(bot)
            update = Update(update_id=1, message=msg)
        else:
            msg = Message(message_id=1, date=datetime.datetime.now(), chat=chat, from_user=user, text="m").as_(bot)
            cb = CallbackQuery(id="1", from_user=user, chat_instance="1", message=msg, data=payload).as_(bot)
            update = Update(update_id=1, callback_query=cb)
        with patch.object(Message, "answer", new=denied), patch.object(CallbackQuery, "answer", new=denied):
            try:
                await dp.feed_update(bot, update)
            finally:
                await bot.session.close()

    try:
        asyncio.run(go())
    finally:
        for r in (admin.router, quiz.router):
            r._parent_router = None  # allow reuse by the next test's Dispatcher
    return hits


def test_regular_user_start_is_not_swallowed_by_admin_router():
    assert _run("message", STRANGER, "/start") == ["reached-user-router"]


def test_regular_user_plain_text_is_not_swallowed():
    assert _run("message", STRANGER, "تجربة بحث") == ["reached-user-router"]


def test_owner_start_reaches_user_router_too():
    assert _run("message", OWNER, "/start") == ["reached-user-router"]


def test_non_owner_admin_command_is_denied_and_not_forwarded():
    assert _run("message", STRANGER, "/status") == ["denied"]


def test_non_owner_admin_callback_is_denied_and_not_forwarded():
    assert _run("callback", STRANGER, "adm:settings") == ["denied"]
