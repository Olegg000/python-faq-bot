"""Подключение к базе: движок, фабрика сессий, создание таблиц."""

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from bot import config
from bot.models import Base

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _prepare_sqlite_dir(url: str) -> None:
    """Создаёт каталог для файла sqlite, иначе движок упадёт при первом запросе."""
    prefix = "sqlite+aiosqlite:///"
    if url.startswith(prefix) and not url.endswith(":memory:"):
        Path(url[len(prefix):]).parent.mkdir(parents=True, exist_ok=True)


def get_engine(url: str | None = None) -> AsyncEngine:
    """Движок-одиночка: создаётся при первом обращении."""
    global _engine
    if _engine is None:
        url = url or config.DB_URL
        _prepare_sqlite_dir(url)
        _engine = create_async_engine(url)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Фабрика сессий; expire_on_commit выключен — объекты живут после commit."""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


def session() -> AsyncSession:
    """Короткая запись: async with db.session() as s: ..."""
    return get_sessionmaker()()


async def init_models(engine: AsyncEngine) -> None:
    """Создаёт таблицы, которых ещё нет."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
