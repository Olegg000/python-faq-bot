"""Массовая рассылка по клиентам с паузой между отправками и учётом блокировок."""

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession

from bot import repo

logger = logging.getLogger(__name__)


async def send_broadcast(bot: Bot, session: AsyncSession, text: str, throttle: float = 0.05) -> dict:
    """Шлёт текст всем незаблокированным клиентам, возвращает sent/blocked/failed."""
    result = {"sent": 0, "blocked": 0, "failed": 0}
    for user_id in await repo.all_user_ids(session, only_unblocked=True):
        try:
            await bot.send_message(user_id, text)
        except TelegramForbiddenError:
            # Клиент заблокировал бота — больше ему не пишем
            await repo.mark_blocked(session, user_id)
            result["blocked"] += 1
        except TelegramAPIError:
            logger.warning("Рассылка не дошла до %s", user_id, exc_info=True)
            result["failed"] += 1
        else:
            result["sent"] += 1
        await asyncio.sleep(throttle)
    return result
