from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage


def make_message(text: str = "") -> MagicMock:
    message = MagicMock()
    message.text = text
    message.answer = AsyncMock()
    message.edit_text = AsyncMock()
    message.from_user = MagicMock(id=100, username="testuser")
    return message


def make_callback(data: str) -> MagicMock:
    callback = MagicMock()
    callback.data = data
    callback.answer = AsyncMock()
    callback.message = make_message()
    callback.from_user = MagicMock(id=100, username="testuser")
    return callback


@pytest.fixture
def state() -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=100, user_id=100)
    return FSMContext(storage=storage, key=key)
