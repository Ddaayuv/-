import asyncio
import datetime
from unittest.mock import patch

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import Chat, Message, Update, User

from bot.security import install_owner_guard

OWNER = 111


def _run(uid: int, text: str) -> list[str]:
    hits: list[str] = []
    admin, user = Router(), Router()
    install_owner_guard(admin, [OWNER])

    @admin.message(Command("adm"))
    async def _adm(m):
        hits.append("admin")

    @user.message(Command("start"))
    async def _start(m):
        hits.append("user-start")

    @user.message(F.text)
    async def _text(m):
        hits.append("user-text")

    dp = Dispatcher()
    dp.include_router(admin)  # same order as main.py: admin before user
    dp.include_router(user)

    async def fake_answer(self, *a, **k):
        hits.append("denied")

    async def go():
        bot = Bot("123456:ABCdefGhIJKlmNoPQRsTUVwxyZ")
        msg = Message(message_id=1, date=datetime.datetime.now(), chat=Chat(id=uid, type="private"),
                      from_user=User(id=uid, is_bot=False, first_name="x"), text=text).as_(bot)
        with patch.object(Message, "answer", new=fake_answer):
            await dp.feed_update(bot, Update(update_id=1, message=msg))
        await bot.session.close()

    asyncio.run(go())
    return hits


def test_regular_user_still_reaches_user_router():
    assert _run(222, "/start") == ["user-start"]
    assert _run(222, "hello") == ["user-text"]


def test_non_owner_is_denied_on_admin_command():
    assert _run(222, "/adm") == ["denied"]


def test_owner_reaches_admin_command():
    assert _run(OWNER, "/adm") == ["admin"]
