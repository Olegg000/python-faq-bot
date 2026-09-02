from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError

from bot import db, repo
from bot.models import Service, User
from bot.scheduler import send_due_reminders, setup_scheduler

NOW = datetime(2026, 9, 2, 12, 0)


def make_bot() -> MagicMock:
    """Бот-заглушка: перехватывает отправку сообщений."""
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


async def prepare(s, hours_ahead: float = 20, status: str = "active"):
    """Клиент, услуга и одна запись через hours_ahead часов от NOW."""
    s.add(User(id=100, username="client", full_name="Иван Тестов", created_at=NOW))
    s.add(Service(id=1, title="Стрижка", duration_min=60, price=1500))
    await s.commit()
    booking = await repo.create_booking(s, 100, 1, NOW + timedelta(hours=hours_ahead))
    if status != "active":
        booking.status = status
        await s.commit()
    return booking


async def test_24h_reminder_sent_with_service_and_time(session):
    await prepare(session, hours_ahead=20)
    bot = make_bot()

    assert await send_due_reminders(bot, db.get_sessionmaker(), NOW) == 1
    chat_id, text = bot.send_message.await_args.args
    assert chat_id == 100
    assert "Стрижка" in text
    assert "03.09.2026 в 08:00" in text
    assert "завтра" in text


async def test_reminder_not_duplicated_on_second_run(session):
    booking = await prepare(session, hours_ahead=20)
    bot = make_bot()

    assert await send_due_reminders(bot, db.get_sessionmaker(), NOW) == 1
    assert await send_due_reminders(bot, db.get_sessionmaker(), NOW) == 0
    assert bot.send_message.await_count == 1

    await session.refresh(booking)
    assert booking.reminded_24h is True
    assert booking.reminded_2h is False


async def test_2h_reminder_is_separate(session):
    booking = await prepare(session, hours_ahead=1.5)
    bot = make_bot()

    assert await send_due_reminders(bot, db.get_sessionmaker(), NOW) == 1
    assert "через 2 часа" in bot.send_message.await_args.args[1]

    await session.refresh(booking)
    assert booking.reminded_2h is True
    # Второе напоминание за 24 часа тому же клиенту не уходит
    assert booking.reminded_24h is True


async def test_cancelled_booking_is_not_reminded(session):
    await prepare(session, hours_ahead=20, status="cancelled")
    bot = make_bot()

    assert await send_due_reminders(bot, db.get_sessionmaker(), NOW) == 0
    bot.send_message.assert_not_awaited()


async def test_far_booking_is_not_reminded(session):
    await prepare(session, hours_ahead=72)
    bot = make_bot()

    assert await send_due_reminders(bot, db.get_sessionmaker(), NOW) == 0
    bot.send_message.assert_not_awaited()


async def test_blocked_user_is_marked_and_not_retried(session):
    await prepare(session, hours_ahead=20)
    bot = make_bot()
    bot.send_message.side_effect = TelegramForbiddenError(method=None, message="bot was blocked")

    assert await send_due_reminders(bot, db.get_sessionmaker(), NOW) == 0

    user = await session.get(User, 100)
    await session.refresh(user)
    assert user.is_blocked is True

    bot.send_message.reset_mock()
    assert await send_due_reminders(bot, db.get_sessionmaker(), NOW) == 0
    bot.send_message.assert_not_awaited()


async def test_network_error_retried_next_run(session):
    await prepare(session, hours_ahead=20)
    bot = make_bot()
    bot.send_message.side_effect = TelegramAPIError(method=None, message="сеть недоступна")

    assert await send_due_reminders(bot, db.get_sessionmaker(), NOW) == 0

    bot.send_message.side_effect = None
    assert await send_due_reminders(bot, db.get_sessionmaker(), NOW) == 1


async def test_setup_scheduler_registers_job():
    scheduler = setup_scheduler(make_bot(), MagicMock())
    try:
        job = scheduler.get_job("reminders")
        assert job is not None
        assert job.trigger.interval == timedelta(minutes=5)
    finally:
        scheduler.shutdown(wait=False)
