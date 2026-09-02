import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import CallbackQuery
from sqlalchemy import text

from bot import db
from bot.middlewares import DbSessionMiddleware, LoggingMiddleware, ThrottlingMiddleware


def make_handler() -> tuple[callable, list]:
    """Хендлер-заглушка и список апдейтов, которые до него дошли."""
    seen: list = []

    async def handler(event, data):
        seen.append((event, data))
        return "ok"

    return handler, seen


def make_event(user_id: int = 1) -> MagicMock:
    """Простое событие с пользователем."""
    event = MagicMock()
    event.from_user = SimpleNamespace(id=user_id)
    return event


def make_callback_event(user_id: int = 1) -> MagicMock:
    """Колбэк, на который антифлуд обязан ответить всплывающим сообщением."""
    event = MagicMock(spec=CallbackQuery)
    event.from_user = SimpleNamespace(id=user_id)
    event.answer = AsyncMock()
    return event


async def test_throttling_passes_first_blocks_second():
    middleware = ThrottlingMiddleware(seconds=5)
    handler, seen = make_handler()

    assert await middleware(handler, make_event(), {}) == "ok"
    assert await middleware(handler, make_event(), {}) is None
    assert len(seen) == 1


async def test_throttling_passes_again_after_pause():
    middleware = ThrottlingMiddleware(seconds=0.05)
    handler, seen = make_handler()

    await middleware(handler, make_event(), {})
    await middleware(handler, make_event(), {})
    await asyncio.sleep(0.06)
    assert await middleware(handler, make_event(), {}) == "ok"
    assert len(seen) == 2


async def test_throttling_is_per_user():
    middleware = ThrottlingMiddleware(seconds=5)
    handler, seen = make_handler()

    await middleware(handler, make_event(user_id=1), {})
    await middleware(handler, make_event(user_id=2), {})
    assert len(seen) == 2


async def test_throttling_answers_callback():
    middleware = ThrottlingMiddleware(seconds=5)
    handler, _ = make_handler()
    event = make_callback_event()

    await middleware(handler, event, {})
    await middleware(handler, event, {})

    event.answer.assert_awaited_once()
    assert "часто" in event.answer.await_args.args[0].lower()


async def test_throttling_forgets_old_users():
    """Словарь не растёт бесконечно: протухшие записи вычищаются."""
    middleware = ThrottlingMiddleware(seconds=0.01, max_entries=5)
    handler, _ = make_handler()

    for user_id in range(20):
        await middleware(handler, make_event(user_id=user_id), {})
        await asyncio.sleep(0.002)

    assert len(middleware._last) < 20


async def test_throttling_skips_events_without_user():
    middleware = ThrottlingMiddleware(seconds=5)
    handler, seen = make_handler()
    event = MagicMock()
    event.from_user = None

    assert await middleware(handler, event, {}) == "ok"
    assert await middleware(handler, event, {}) == "ok"
    assert len(seen) == 2


async def test_db_session_middleware_gives_working_session(session):
    middleware = DbSessionMiddleware(db.get_sessionmaker())
    captured = {}

    async def handler(event, data):
        captured["session"] = data["session"]
        await data["session"].execute(text("select 1"))
        return "ok"

    assert await middleware(handler, make_event(), {}) == "ok"
    assert captured["session"] is not None


async def test_db_session_middleware_closes_session(session):
    """После апдейта транзакция закрыта, соединение возвращено в пул."""
    middleware = DbSessionMiddleware(db.get_sessionmaker())
    captured = {}

    async def handler(event, data):
        captured["session"] = data["session"]
        await data["session"].execute(text("select 1"))
        assert data["session"].in_transaction()

    await middleware(handler, make_event(), {})
    assert captured["session"].in_transaction() is False


async def test_db_session_middleware_closes_on_error(session):
    middleware = DbSessionMiddleware(db.get_sessionmaker())
    captured = {}

    async def handler(event, data):
        captured["session"] = data["session"]
        await data["session"].execute(text("select 1"))
        raise RuntimeError("падение хендлера")

    with pytest.raises(RuntimeError):
        await middleware(handler, make_event(), {})
    assert captured["session"].in_transaction() is False


async def test_logging_middleware_logs_user_and_result(caplog):
    middleware = LoggingMiddleware()
    handler, seen = make_handler()

    with caplog.at_level(logging.INFO, logger="bot.middlewares"):
        assert await middleware(handler, make_event(user_id=42), {}) == "ok"

    assert len(seen) == 1
    assert "42" in caplog.text


async def test_logging_middleware_logs_even_on_error(caplog):
    middleware = LoggingMiddleware()

    async def handler(event, data):
        raise RuntimeError("падение хендлера")

    with caplog.at_level(logging.INFO, logger="bot.middlewares"), pytest.raises(RuntimeError):
        await middleware(handler, make_event(user_id=7), {})

    assert "7" in caplog.text
