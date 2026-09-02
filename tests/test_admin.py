from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from bot import config, repo
from bot.handlers import admin
from bot.models import Service, utcnow
from tests.conftest import make_callback, make_message
from tests.test_broadcast import fake_bot

ADMIN_ID = 100
STRANGER_ID = 999


@pytest.fixture(autouse=True)
def admins(monkeypatch):
    """В тестах админ — тот же id 100, что и у моков из conftest."""
    monkeypatch.setattr(config, "ADMIN_IDS", {ADMIN_ID})


def stranger(event: MagicMock) -> MagicMock:
    event.from_user = MagicMock(id=STRANGER_ID, username="chuzhoy", full_name="Посторонний")
    return event


async def setup_price(session) -> list[Service]:
    """Две услуги в прайсе: одна активная, одна скрытая."""
    session.add_all([
        Service(id=1, title="Стрижка", duration_min=30, price=1200, is_active=True, sort=10),
        Service(id=2, title="Окрашивание", duration_min=60, price=5000, is_active=False, sort=20),
    ])
    await session.commit()
    return await repo.list_services(session, only_active=False)


async def setup_bookings(session) -> None:
    """Два клиента, три записи на сегодня: 2 стрижки и 1 окрашивание."""
    await setup_price(session)
    await repo.set_service_active(session, 2, True)
    await repo.upsert_user(session, 100, "ivan", "Иван Тестов")
    await repo.upsert_user(session, 200, None, "Пётр Петров")
    today = utcnow().replace(hour=10, minute=0, second=0, microsecond=0)
    await repo.create_booking(session, 100, 1, today)
    await repo.create_booking(session, 200, 1, today + timedelta(hours=2))
    await repo.create_booking(session, 100, 2, today + timedelta(hours=4))


# --- Доступ ---

async def test_admin_menu_lists_all_sections(session):
    message = make_message("/admin")
    await admin.cmd_admin(message)

    markup = message.answer.call_args.kwargs["reply_markup"]
    actions = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert actions == ["adm:today", "adm:stats", "adm:leads", "adm:services", "adm:broadcast"]


async def test_admin_command_denied_for_stranger(session):
    message = stranger(make_message("/admin"))
    await admin.cmd_admin(message)

    message.answer.assert_called_once_with(admin.DENIED)
    assert "reply_markup" not in message.answer.call_args.kwargs


@pytest.mark.parametrize("handler_name,data", [
    ("show_admin_menu", "adm:menu"),
    ("show_today", "adm:today"),
    ("show_stats", "adm:stats"),
    ("show_leads", "adm:leads"),
    ("show_services", "adm:services"),
    ("toggle_service", "adm:svc:1"),
])
async def test_sections_denied_for_stranger(handler_name, data, session):
    callback = stranger(make_callback(data))
    await getattr(admin, handler_name)(callback)

    callback.answer.assert_called_once_with(admin.DENIED)
    callback.message.edit_text.assert_not_called()


async def test_broadcast_denied_for_stranger(state, session):
    callback = stranger(make_callback("adm:broadcast"))
    await admin.broadcast_start(callback, state)

    callback.answer.assert_called_once_with(admin.DENIED)
    assert await state.get_state() is None

    bot = fake_bot()
    send = stranger(make_callback("adm:bc:send"))
    await admin.broadcast_send(send, state, bot)

    send.answer.assert_called_once_with(admin.DENIED)
    bot.send_message.assert_not_awaited()


async def test_stranger_cannot_toggle_service(session):
    await setup_price(session)
    callback = stranger(make_callback("adm:svc:1"))
    await admin.toggle_service(callback)

    services = await repo.list_services(session, only_active=False)
    assert [item.is_active for item in services] == [True, False]  # ничего не изменилось


# --- Записи на сегодня ---

async def test_today_lists_bookings(session):
    await setup_bookings(session)
    callback = make_callback("adm:today")
    await admin.show_today(callback)

    text = callback.message.edit_text.call_args.args[0]
    lines = text.splitlines()[2:]
    assert len(lines) == 3
    assert "Стрижка" in lines[0] and "Иван Тестов (@ivan)" in lines[0]
    assert "Пётр Петров" in lines[1] and "@" not in lines[1]
    assert "Окрашивание" in lines[2]
    assert lines[0].split(" ")[0] < lines[1].split(" ")[0]  # по возрастанию времени


async def test_today_reports_empty_day(session):
    callback = make_callback("adm:today")
    await admin.show_today(callback)

    text = callback.message.edit_text.call_args.args[0]
    assert "записей нет" in text
    callback.answer.assert_called_once()


async def test_today_ignores_cancelled(session):
    await setup_bookings(session)
    booking = (await repo.user_bookings(session, 200, upcoming_only=False))[0]
    booking.status = "cancelled"
    await session.commit()

    callback = make_callback("adm:today")
    await admin.show_today(callback)

    assert "Пётр Петров" not in callback.message.edit_text.call_args.args[0]


# --- Статистика ---

async def test_stats_numbers(session):
    await setup_bookings(session)
    callback = make_callback("adm:stats")
    await admin.show_stats(callback)

    text = callback.message.edit_text.call_args.args[0]
    assert "Записей: 3" in text
    assert f"Выручка: {admin.money(1200 * 2 + 5000)} ₽" in text  # 7 400
    assert "Новых клиентов: 2" in text
    assert "Конверсия: 100%" in text
    assert "1. Стрижка — 2" in text
    assert "2. Окрашивание — 1" in text


async def test_stats_on_empty_base(session):
    callback = make_callback("adm:stats")
    await admin.show_stats(callback)

    text = callback.message.edit_text.call_args.args[0]
    assert "Записей: 0" in text
    assert "Выручка: 0 ₽" in text
    assert "Конверсия: 0%" in text
    assert "пока пусто" in text


# --- Заявки ---

async def test_leads_show_latest_first(session):
    await repo.upsert_user(session, 100, "ivan", "Иван Тестов")
    await repo.create_lead(session, 100, "Иван", "Первый вопрос")
    await repo.create_lead(session, 100, "Иван", "Второй вопрос")

    callback = make_callback("adm:leads")
    await admin.show_leads(callback)

    text = callback.message.edit_text.call_args.args[0]
    assert text.index("Второй вопрос") < text.index("Первый вопрос")


async def test_leads_empty(session):
    callback = make_callback("adm:leads")
    await admin.show_leads(callback)
    assert "Заявок пока нет" in callback.message.edit_text.call_args.args[0]


# --- Услуги ---

async def test_services_list_shows_state_and_price(session):
    await setup_price(session)
    callback = make_callback("adm:services")
    await admin.show_services(callback)

    text = callback.message.edit_text.call_args.args[0]
    assert "🟢 Стрижка — 1 200 ₽, 30 мин" in text
    assert "🔴 Окрашивание — 5 000 ₽, 60 мин" in text

    markup = callback.message.edit_text.call_args.kwargs["reply_markup"]
    actions = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert actions == ["adm:svc:1", "adm:svc:2", "adm:menu"]


async def test_toggle_service_switches_state(session):
    await setup_price(session)

    await admin.toggle_service(make_callback("adm:svc:1"))
    assert (await repo.get_service(session, 1)).is_active is False

    callback = make_callback("adm:svc:1")
    await admin.toggle_service(callback)
    assert (await repo.get_service(session, 1)).is_active is True
    assert "🟢 Стрижка" in callback.message.edit_text.call_args.args[0]
    callback.answer.assert_called_once_with("Услуга включена")


async def test_toggle_unknown_service(session):
    callback = make_callback("adm:svc:42")
    await admin.toggle_service(callback)

    callback.answer.assert_called_once_with("Услуга не найдена")
    callback.message.edit_text.assert_not_called()


# --- Рассылка ---

async def test_broadcast_full_flow(state, session):
    await repo.upsert_user(session, 100, "ivan", "Иван Тестов")
    await repo.upsert_user(session, 200, None, "Пётр Петров")

    await admin.broadcast_start(make_callback("adm:broadcast"), state)
    assert await state.get_state() == admin.BroadcastForm.text

    message = make_message("Скидка 20% на окрашивание")
    await admin.broadcast_text(message, state)
    assert await state.get_state() == admin.BroadcastForm.confirm
    preview = message.answer.call_args.args[0]
    assert "2" in preview and "Скидка 20% на окрашивание" in preview
    buttons = [
        btn.callback_data
        for row in message.answer.call_args.kwargs["reply_markup"].inline_keyboard
        for btn in row
    ]
    assert buttons == ["adm:bc:send", "adm:bc:cancel"]

    bot = fake_bot()
    callback = make_callback("adm:bc:send")
    await admin.broadcast_send(callback, state, bot)

    assert [call.args for call in bot.send_message.await_args_list] == [
        (100, "Скидка 20% на окрашивание"),
        (200, "Скидка 20% на окрашивание"),
    ]
    report = callback.message.answer.call_args.args[0]
    assert "Доставлено: 2" in report
    assert "Заблокировали бота: 0" in report
    assert await state.get_state() is None


async def test_broadcast_reports_blocked_clients(state, session):
    await repo.upsert_user(session, 100, "ivan", "Иван Тестов")
    await repo.upsert_user(session, 200, None, "Пётр Петров")

    await admin.broadcast_start(make_callback("adm:broadcast"), state)
    await admin.broadcast_text(make_message("Текст"), state)

    bot = fake_bot(blocked={200})
    callback = make_callback("adm:bc:send")
    await admin.broadcast_send(callback, state, bot)

    report = callback.message.answer.call_args.args[0]
    assert "Доставлено: 1" in report
    assert "Заблокировали бота: 1" in report
    assert await repo.all_user_ids(session) == [100]


async def test_broadcast_cancel_does_not_send(state, session):
    await repo.upsert_user(session, 100, "ivan", "Иван Тестов")
    await admin.broadcast_start(make_callback("adm:broadcast"), state)
    await admin.broadcast_text(make_message("Текст"), state)

    callback = make_callback("adm:bc:cancel")
    await admin.broadcast_cancel(callback, state)

    assert await state.get_state() is None
    assert await state.get_data() == {}
    assert "отменена" in callback.message.edit_text.call_args.args[0]


async def test_broadcast_requires_text_message(state, session):
    await repo.upsert_user(session, 100, "ivan", "Иван Тестов")
    await admin.broadcast_start(make_callback("adm:broadcast"), state)

    message = make_message("")
    message.text = None  # прислали, например, стикер
    await admin.broadcast_text(message, state)

    assert await state.get_state() == admin.BroadcastForm.text
    assert "Нужен текст" in message.answer.call_args.args[0]


async def test_broadcast_text_denied_for_stranger(state, session):
    message = stranger(make_message("Текст"))
    await admin.broadcast_text(message, state)

    message.answer.assert_called_once_with(admin.DENIED)
    assert await state.get_data() == {}
