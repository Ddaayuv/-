"""Durable aiogram FSM storage backed by Redis or the application database."""
from __future__ import annotations

import json
import logging
from typing import Any

from aiogram.fsm.storage.base import BaseStorage, StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from .config import settings
from .db import FSMStorageRecord, Session

log = logging.getLogger(__name__)


class DatabaseFSMStorage(BaseStorage):
    """Persist FSM state/data using the same async SQLAlchemy database as the bot."""

    def __init__(self, session_factory=Session) -> None:
        self.session_factory = session_factory

    @staticmethod
    def _key(key: StorageKey) -> str:
        values = (
            key.bot_id, key.chat_id, key.user_id, key.thread_id,
            key.business_connection_id, key.destiny,
        )
        return json.dumps(values, ensure_ascii=True, separators=(",", ":"))

    async def set_state(self, key: StorageKey, state: str | None = None) -> None:
        value = getattr(state, "state", state)
        if value is not None:
            value = str(value)
        async with self.session_factory() as session:
            row = await session.get(FSMStorageRecord, self._key(key))
            if row is None:
                if value is None:
                    return
                session.add(FSMStorageRecord(key=self._key(key), state=value, data_json="{}"))
            else:
                row.state = value
            await session.commit()

    async def get_state(self, key: StorageKey) -> str | None:
        async with self.session_factory() as session:
            row = await session.get(FSMStorageRecord, self._key(key))
            return row.state if row is not None else None

    async def set_data(self, key: StorageKey, data: dict[str, Any]) -> None:
        if not isinstance(data, dict):
            raise TypeError("FSM data must be a dictionary")
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        async with self.session_factory() as session:
            row = await session.get(FSMStorageRecord, self._key(key))
            if row is None:
                session.add(FSMStorageRecord(key=self._key(key), state=None, data_json=payload))
            else:
                row.data_json = payload
            await session.commit()

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        async with self.session_factory() as session:
            row = await session.get(FSMStorageRecord, self._key(key))
            if row is None:
                return {}
            try:
                data = json.loads(row.data_json or "{}")
            except (TypeError, json.JSONDecodeError):
                log.exception("Invalid FSM data found for storage key")
                return {}
            return data if isinstance(data, dict) else {}

    async def update_data(self, key: StorageKey, data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise TypeError("FSM update data must be a dictionary")
        async with self.session_factory() as session:
            row = await session.get(FSMStorageRecord, self._key(key))
            if row is None:
                merged = dict(data)
                session.add(FSMStorageRecord(
                    key=self._key(key), state=None,
                    data_json=json.dumps(merged, ensure_ascii=False, separators=(",", ":")),
                ))
            else:
                try:
                    merged = json.loads(row.data_json or "{}")
                except (TypeError, json.JSONDecodeError):
                    log.exception("Invalid FSM data found during update")
                    merged = {}
                if not isinstance(merged, dict):
                    merged = {}
                merged.update(data)
                row.data_json = json.dumps(merged, ensure_ascii=False, separators=(",", ":"))
            await session.commit()
            return merged

    async def close(self) -> None:
        # The application owns the shared SQLAlchemy engine and session factory.
        return None


async def build_fsm_storage() -> tuple[BaseStorage, str]:
    """Use Redis when configured, DB otherwise, and MemoryStorage for local :memory: SQLite."""
    if settings.redis_url:
        storage = None
        try:
            from aiogram.fsm.storage.redis import RedisStorage

            storage = RedisStorage.from_url(settings.redis_url)
            await storage.redis.ping()
            return storage, "redis"
        except Exception:
            log.exception("Redis FSM backend unavailable; falling back to database storage")
            if storage is not None:
                try:
                    await storage.close()
                except Exception:
                    log.warning("Could not close failed Redis FSM connection", exc_info=True)
            return DatabaseFSMStorage(), "database-fallback"

    if settings.db_url.startswith("sqlite") and settings.db_path == ":memory:":
        log.info("Using MemoryStorage for local in-memory SQLite mode")
        return MemoryStorage(), "memory"
    return DatabaseFSMStorage(), "database"
