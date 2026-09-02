"""Первичное наполнение прайса при пустой базе."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Service

DEFAULT_SERVICES = [
    {"title": "Стрижка мужская", "duration_min": 45, "price": 1200, "sort": 10},
    {"title": "Стрижка женская", "duration_min": 90, "price": 2500, "sort": 20},
    {"title": "Окрашивание", "duration_min": 150, "price": 5000, "sort": 30},
    {"title": "Укладка", "duration_min": 60, "price": 1500, "sort": 40},
    {"title": "Моделирование бороды", "duration_min": 30, "price": 800, "sort": 50},
]


async def seed_services(s: AsyncSession) -> int:
    """Добавляет базовые услуги, если прайс пуст. Возвращает число добавленных."""
    if await s.scalar(select(func.count()).select_from(Service)):
        return 0
    s.add_all([Service(**item) for item in DEFAULT_SERVICES])
    await s.commit()
    return len(DEFAULT_SERVICES)
