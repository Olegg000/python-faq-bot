"""Планировщик: напоминания клиентам за 24 часа и за 2 часа до визита."""

import logging
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot import repo
from bot.models import Booking, utcnow

log = logging.getLogger(__name__)

CHECK_INTERVAL_MIN = 5
LEAD_TEXT = {"24h": "Напоминаем: вы записаны завтра.", "2h": "Напоминаем: вы записаны через 2 часа."}


def reminder_text(booking: Booking, kind: str) -> str:
    """Текст напоминания: услуга, дата и время визита."""
    return (
        f"{LEAD_TEXT[kind]}\n\n"
        f"Услуга: {booking.service.title}\n"
        f"Когда: {booking.starts_at.strftime('%d.%m.%Y в %H:%M')}\n\n"
        "Если планы изменились — откройте «Мои записи» и отмените визит."
    )


async def _remind(bot: Bot, s: AsyncSession, booking: Booking, kind: str) -> bool:
    """Шлёт одно напоминание; True — если отправлено, False — если пробуем позже."""
    # Клиенту, которому только что напомнили за 2 часа, второе письмо не нужно
    if kind == "24h" and booking.reminded_2h:
        await repo.mark_reminded(s, booking.id, kind)
        return False

    try:
        await bot.send_message(booking.user_id, reminder_text(booking, kind))
    except TelegramForbiddenError:
        # Бот заблокирован — больше не беспокоим этого клиента
        await repo.mark_blocked(s, booking.user_id)
        await repo.mark_reminded(s, booking.id, kind)
        return False
    except TelegramAPIError as exc:
        log.warning("Напоминание %s по записи %s не ушло: %s", kind, booking.id, exc)
        return False  # флаг не ставим — попробуем в следующий заход

    await repo.mark_reminded(s, booking.id, kind)
    return True


async def send_due_reminders(
    bot: Bot,
    sessionmaker: async_sessionmaker[AsyncSession],
    now: datetime | None = None,
) -> int:
    """Рассылает все назревшие напоминания и возвращает число отправленных."""
    now = now or utcnow()
    sent = 0
    async with sessionmaker() as s:
        for kind in ("2h", "24h"):  # сначала срочные, чтобы не задвоить сообщение
            for booking in await repo.due_reminders(s, now, kind):
                if await _remind(bot, s, booking, kind):
                    sent += 1
    return sent


def setup_scheduler(bot: Bot, sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncIOScheduler:
    """Запускает проверку напоминаний раз в 5 минут."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        send_due_reminders,
        "interval",
        minutes=CHECK_INTERVAL_MIN,
        args=(bot, sessionmaker),
        id="reminders",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    return scheduler
