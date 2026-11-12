import asyncio
import os

os.environ.setdefault("BOT_TOKEN", "123456:local-test-token")
os.environ.setdefault("OWNER_IDS", "1")
os.environ["DB_PATH"] = ":memory:"
os.environ["REDIS_URL"] = ""

from aiogram.fsm.storage.base import StorageKey
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.db import FSMStorageRecord
from bot.fsm_storage import DatabaseFSMStorage


def test_database_fsm_storage_persists_state_and_data_in_sqlite_memory():
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(FSMStorageRecord.__table__.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        storage = DatabaseFSMStorage(factory)
        key = StorageKey(bot_id=1, chat_id=2, user_id=3)

        await storage.set_state(key, "Quiz:waiting_answer")
        assert await storage.get_state(key) == "Quiz:waiting_answer"
        await storage.set_data(key, {"question": 4, "answers": [1, 2]})
        merged = await storage.update_data(key, {"score": 1})
        assert merged == {"question": 4, "answers": [1, 2], "score": 1}
        assert await storage.get_data(key) == merged
        await storage.set_state(key, None)
        assert await storage.get_state(key) is None
        await engine.dispose()

    asyncio.run(scenario())
