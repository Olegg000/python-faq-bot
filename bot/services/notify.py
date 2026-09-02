"""Уведомления менеджера о новых записях, отменах и заявках."""

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from bot import config
from bot.models import Booking, Lead, Service, User

logger = logging.getLogger(__name__)


def client_label(user: User) -> str:
    """Клиент одной строкой: имя и username, если он указан."""
    name = (user.full_name or "").strip() or f"id {user.id}"
    return f"{name} (@{user.username})" if user.username else name


async def _send(bot: Bot, text: str) -> bool:
    """Шлёт текст в чат менеджера; если чат не задан — тихо ничего не делает."""
    if config.MANAGER_CHAT_ID is None:
        return False
    try:
        await bot.send_message(config.MANAGER_CHAT_ID, text)
    except TelegramAPIError:
        logger.warning("Не удалось отправить уведомление менеджеру", exc_info=True)
        return False
    return True


async def notify_new_booking(bot: Bot, booking: Booking, service: Service, user: User) -> bool:
    """Сообщает менеджеру о новой записи."""
    return await _send(
        bot,
        "🆕 Новая запись\n"
        f"Когда: {booking.starts_at:%d.%m %H:%M}\n"
        f"Услуга: {service.title} — {service.price} ₽ ({service.duration_min} мин)\n"
        f"Клиент: {client_label(user)}",
    )


async def notify_cancelled(bot: Bot, booking: Booking, service: Service, user: User) -> bool:
    """Сообщает менеджеру об отмене записи — слот снова свободен."""
    return await _send(
        bot,
        "❌ Запись отменена\n"
        f"Когда: {booking.starts_at:%d.%m %H:%M}\n"
        f"Услуга: {service.title}\n"
        f"Клиент: {client_label(user)}",
    )


async def notify_new_lead(bot: Bot, lead: Lead, user: User) -> bool:
    """Сообщает менеджеру о новой заявке с вопросом."""
    return await _send(
        bot,
        f"✉️ Новая заявка №{lead.id}\n"
        f"Имя: {lead.name}\n"
        f"Клиент: {client_label(user)}\n"
        f"Вопрос: {lead.question}",
    )
