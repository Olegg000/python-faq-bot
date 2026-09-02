from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bot import config, repo
from bot.models import Booking, Lead, Service, User
from bot.services import notify
from bot.services.broadcast import send_broadcast


def fake_bot(blocked: set[int] = frozenset(), failing: set[int] = frozenset()) -> MagicMock:
    """Бот-заглушка: для указанных id падает с ошибкой Telegram."""

    def send(chat_id, text, **kwargs):
        if chat_id in blocked:
            raise TelegramForbiddenError(method=MagicMock(), message="bot was blocked by the user")
        if chat_id in failing:
            raise TelegramBadRequest(method=MagicMock(), message="chat not found")
        return MagicMock()

    return MagicMock(send_message=AsyncMock(side_effect=send))


async def three_clients(session) -> None:
    await repo.upsert_user(session, 100, "one", "Первый")
    await repo.upsert_user(session, 200, "two", "Второй")
    await repo.upsert_user(session, 300, "three", "Третий")


# --- Рассылка ---

async def test_broadcast_delivers_to_everyone(session):
    await three_clients(session)
    bot = fake_bot()

    result = await send_broadcast(bot, session, "Акция", throttle=0)

    assert result == {"sent": 3, "blocked": 0, "failed": 0}
    assert [call.args[0] for call in bot.send_message.await_args_list] == [100, 200, 300]
    assert bot.send_message.await_args.args[1] == "Акция"


async def test_blocked_user_is_marked_and_skipped_next_time(session):
    await three_clients(session)
    bot = fake_bot(blocked={200})

    first = await send_broadcast(bot, session, "Первая", throttle=0)
    assert first == {"sent": 2, "blocked": 1, "failed": 0}
    assert await repo.all_user_ids(session) == [100, 300]

    bot.send_message.reset_mock()
    second = await send_broadcast(bot, session, "Вторая", throttle=0)

    assert second == {"sent": 2, "blocked": 0, "failed": 0}
    assert [call.args[0] for call in bot.send_message.await_args_list] == [100, 300]


async def test_broadcast_counts_failures_and_continues(session):
    await three_clients(session)
    bot = fake_bot(failing={100})

    result = await send_broadcast(bot, session, "Акция", throttle=0)

    assert result == {"sent": 2, "blocked": 0, "failed": 1}
    # временная ошибка не блокирует клиента навсегда
    assert await repo.all_user_ids(session) == [100, 200, 300]


async def test_broadcast_without_clients(session):
    bot = fake_bot()
    assert await send_broadcast(bot, session, "Акция", throttle=0) == {
        "sent": 0,
        "blocked": 0,
        "failed": 0,
    }
    bot.send_message.assert_not_awaited()


# --- Уведомления менеджеру ---

@pytest.fixture
def manager(monkeypatch):
    monkeypatch.setattr(config, "MANAGER_CHAT_ID", -100500)
    return -100500


def sample() -> tuple[Booking, Service, User]:
    user = User(id=100, username="ivan", full_name="Иван Тестов")
    service = Service(id=1, title="Стрижка мужская", duration_min=45, price=1200)
    booking = Booking(id=7, user_id=100, service_id=1, starts_at=datetime(2026, 9, 2, 10, 30))
    return booking, service, user


async def test_notify_new_booking(manager):
    bot = fake_bot()
    booking, service, user = sample()

    assert await notify.notify_new_booking(bot, booking, service, user) is True

    chat_id, text = bot.send_message.await_args.args
    assert chat_id == manager
    assert "02.09 10:30" in text
    assert "Стрижка мужская" in text
    assert "1200" in text
    assert "Иван Тестов (@ivan)" in text


async def test_notify_cancelled(manager):
    bot = fake_bot()
    booking, service, user = sample()

    assert await notify.notify_cancelled(bot, booking, service, user) is True

    text = bot.send_message.await_args.args[1]
    assert "отменена" in text.lower()
    assert "02.09 10:30" in text
    assert "Стрижка мужская" in text


async def test_notify_new_lead(manager):
    bot = fake_bot()
    user = User(id=100, username=None, full_name="Иван Тестов")
    lead = Lead(id=3, user_id=100, name="Иван", question="Сколько стоит окрашивание?")

    assert await notify.notify_new_lead(bot, lead, user) is True

    text = bot.send_message.await_args.args[1]
    assert "№3" in text
    assert "Сколько стоит окрашивание?" in text
    assert "@" not in text  # username не указан — лишних скобок нет


async def test_notify_silent_without_manager_chat(monkeypatch):
    monkeypatch.setattr(config, "MANAGER_CHAT_ID", None)
    bot = fake_bot()
    booking, service, user = sample()
    lead = Lead(id=1, user_id=100, name="Иван", question="Вопрос")

    assert await notify.notify_new_booking(bot, booking, service, user) is False
    assert await notify.notify_cancelled(bot, booking, service, user) is False
    assert await notify.notify_new_lead(bot, lead, user) is False
    bot.send_message.assert_not_awaited()


async def test_notify_survives_telegram_error(manager):
    bot = fake_bot(failing={manager})
    booking, service, user = sample()

    assert await notify.notify_new_booking(bot, booking, service, user) is False
    bot.send_message.assert_awaited_once()


def test_client_label_falls_back_to_id():
    assert notify.client_label(User(id=42, username=None, full_name=None)) == "id 42"
    assert notify.client_label(User(id=42, username="ivan", full_name="Иван")) == "Иван (@ivan)"
