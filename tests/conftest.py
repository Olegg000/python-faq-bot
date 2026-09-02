from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from bot import db


def make_message(text: str = "") -> MagicMock:
    message = MagicMock()
    message.text = text
    message.answer = AsyncMock()
    message.edit_text = AsyncMock()
    message.from_user = MagicMock(id=100, username="testuser", full_name="Иван Тестов")
    return message


def make_callback(data: str) -> MagicMock:
    callback = MagicMock()
    callback.data = data
    callback.answer = AsyncMock()
    callback.message = make_message()
    callback.from_user = MagicMock(id=100, username="testuser", full_name="Иван Тестов")
    return callback


@pytest.fixture
def state() -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=100, user_id=100)
    return FSMContext(storage=storage, key=key)


@pytest.fixture
async def session(monkeypatch):
    """База в памяти на один тест; хендлеры через db.session() пишут туда же."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    await db.init_models(engine)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, "_engine", engine)
    monkeypatch.setattr(db, "_sessionmaker", maker)
    async with maker() as s:
        yield s
    await engine.dispose()
