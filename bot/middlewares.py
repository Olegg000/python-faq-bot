"""Промежуточные слои: сессия базы на апдейт, антифлуд и лог обработки."""

import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot import config

log = logging.getLogger(__name__)

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


def _user_id(event: TelegramObject, data: dict[str, Any]) -> int | None:
    """Id пользователя апдейта: из данных aiogram, иначе прямо из события."""
    user = data.get("event_from_user") or getattr(event, "from_user", None)
    return user.id if user is not None else None


class DbSessionMiddleware(BaseMiddleware):
    """Открывает сессию на апдейт, кладёт в data['session'] и гарантированно закрывает."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self.sessionmaker = sessionmaker

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        async with self.sessionmaker() as session:
            data["session"] = session
            return await handler(event, data)


class ThrottlingMiddleware(BaseMiddleware):
    """Пропускает не чаще одного апдейта в THROTTLE_SECONDS от одного пользователя."""

    def __init__(self, seconds: float | None = None, max_entries: int = 10_000) -> None:
        self.seconds = config.THROTTLE_SECONDS if seconds is None else seconds
        self.max_entries = max_entries
        self._last: dict[int, float] = {}

    def _cleanup(self, now: float) -> None:
        """Выбрасывает пользователей, чья пауза давно истекла — чтобы словарь не рос."""
        if len(self._last) < self.max_entries:
            return
        self._last = {uid: ts for uid, ts in self._last.items() if now - ts < self.seconds}

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        user_id = _user_id(event, data)
        if user_id is None:
            return await handler(event, data)

        now = time.monotonic()
        last = self._last.get(user_id)
        if last is not None and now - last < self.seconds:
            if isinstance(event, CallbackQuery):
                await event.answer("Слишком часто, подождите секунду")
            return None  # обычные сообщения отбрасываем молча

        self._cleanup(now)
        self._last[user_id] = now
        return await handler(event, data)


class LoggingMiddleware(BaseMiddleware):
    """Пишет в лог тип апдейта, пользователя и время обработки."""

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        started = time.monotonic()
        try:
            return await handler(event, data)
        finally:
            log.info(
                "%s от %s обработан за %.3f c",
                type(event).__name__,
                _user_id(event, data),
                time.monotonic() - started,
            )


def setup_middlewares(dp: Any, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    """Вешает слои на сообщения и колбэки: лог -> антифлуд -> сессия."""
    throttling = ThrottlingMiddleware()
    session_layer = DbSessionMiddleware(sessionmaker)
    logging_layer = LoggingMiddleware()
    for observer in (dp.message, dp.callback_query):
        observer.outer_middleware(logging_layer)
        observer.outer_middleware(throttling)
        observer.outer_middleware(session_layer)
