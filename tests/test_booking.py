from datetime import date, datetime, time, timedelta
from unittest.mock import AsyncMock

import pytest

from bot import config, repo
from bot.handlers import booking as handlers
from bot.models import Booking, Service, User, utcnow
from tests.conftest import make_callback

TODAY = utcnow().date()


def day(shift: int) -> date:
    """Дата через N дней от сегодня — тесты всегда смотрят в будущее."""
    return TODAY + timedelta(days=shift)


def at(shift: int, hour: int, minute: int = 0) -> datetime:
    """Момент времени внутри рабочего дня."""
    return datetime.combine(day(shift), time(hour, minute))


def texts(callback) -> list[str]:
    """Подписи всех кнопок последней отрисованной клавиатуры."""
    markup = callback.message.edit_text.call_args.kwargs["reply_markup"]
    return [button.text for row in markup.inline_keyboard for button in row]


def datas(callback) -> list[str]:
    """callback_data всех кнопок последней отрисованной клавиатуры."""
    markup = callback.message.edit_text.call_args.kwargs["reply_markup"]
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def body(callback) -> str:
    """Текст последнего сообщения."""
    return callback.message.edit_text.call_args.args[0]


async def add_service(session, title="Стрижка", duration_min=45, price=1200, **kwargs) -> Service:
    service = Service(title=title, duration_min=duration_min, price=price, **kwargs)
    session.add(service)
    await session.commit()
    return service


async def add_user(session, tg_id=100) -> User:
    user = User(id=tg_id, username=f"u{tg_id}", full_name="Клиент", created_at=utcnow())
    session.add(user)
    await session.commit()
    return user


async def add_booking(session, service: Service, starts_at: datetime, user_id: int = 100) -> Booking:
    if await session.get(User, user_id) is None:
        await add_user(session, user_id)
    item = Booking(user_id=user_id, service_id=service.id, starts_at=starts_at, created_at=utcnow())
    session.add(item)
    await session.commit()
    return item


@pytest.fixture(autouse=True)
def silent_notify(monkeypatch):
    """Уведомления менеджера подменяем моками — сети в тестах нет."""
    new = AsyncMock()
    cancelled = AsyncMock()
    monkeypatch.setattr(handlers, "notify_new_booking", new)
    monkeypatch.setattr(handlers, "notify_cancelled", cancelled)
    return new, cancelled


# --- Шаг 1: услуги ---

async def test_service_list_shows_only_active(session):
    await add_service(session, "Стрижка", 45, 1200, sort=10)
    await add_service(session, "Окрашивание", 90, 5000, sort=20)
    await add_service(session, "Архивная услуга", 30, 100, is_active=False, sort=30)

    callback = make_callback("menu:book")
    await handlers.choose_service(callback)

    labels = texts(callback)
    assert any("Стрижка" in t and "45 мин" in t and "1 200 ₽" in t for t in labels)
    assert any("Окрашивание" in t and "1 ч 30 мин" in t and "5 000 ₽" in t for t in labels)
    assert not any("Архивная" in t for t in labels)
    assert "book:svc:1" in datas(callback)
    callback.answer.assert_awaited_once()


async def test_service_list_empty(session):
    callback = make_callback("menu:book")
    await handlers.choose_service(callback)

    assert "услуги не настроены" in body(callback)
    assert datas(callback) == ["menu:main"]


# --- Шаг 2: дни ---

async def test_days_are_labelled_and_limited(session):
    service = await add_service(session)

    callback = make_callback(f"book:svc:{service.id}")
    await handlers.choose_day(callback)

    labels = texts(callback)
    assert labels[0] == "Сегодня" or labels[0] == "Завтра"  # сегодня могло уже закрыться
    assert "Завтра" in labels
    day_datas = [d for d in datas(callback) if d.startswith("book:day:")]
    assert len(day_datas) <= config.BOOKING_DAYS_AHEAD
    assert f"book:day:{service.id}:{day(2).isoformat()}" in day_datas


async def test_days_empty_offers_lead(session, monkeypatch):
    # смена короче услуги — свободных дней нет вовсе
    monkeypatch.setattr(config, "WORK_END", time(10, 10))
    service = await add_service(session, duration_min=45)

    callback = make_callback(f"book:svc:{service.id}")
    await handlers.choose_day(callback)

    assert "свободного времени нет" in body(callback)
    assert "menu:ask" in datas(callback)


async def test_days_for_unknown_service(session):
    callback = make_callback("book:svc:999")
    await handlers.choose_day(callback)

    assert "недоступна" in body(callback)
    assert "menu:book" in datas(callback)


# --- Шаг 3: слоты ---

async def test_slots_grid_three_per_row(session):
    service = await add_service(session)

    callback = make_callback(f"book:day:{service.id}:{day(2).isoformat()}")
    await handlers.choose_slot(callback)

    markup = callback.message.edit_text.call_args.kwargs["reply_markup"]
    slot_rows = [row for row in markup.inline_keyboard if row[0].callback_data.startswith("book:slot:")]
    assert slot_rows and all(len(row) <= 3 for row in slot_rows)
    assert len(slot_rows[0]) == 3
    assert "10:00" in texts(callback)
    assert f"book:slot:{service.id}:{at(2, 10, 0).isoformat(timespec='minutes')}" in datas(callback)


async def test_slots_exclude_busy_time(session):
    service = await add_service(session, duration_min=45)
    await add_booking(session, service, at(2, 10, 0))

    callback = make_callback(f"book:day:{service.id}:{day(2).isoformat()}")
    await handlers.choose_slot(callback)

    labels = texts(callback)
    assert "10:00" not in labels  # занято
    assert "10:30" not in labels  # пересеклось бы с 45-минутной услугой
    assert "11:00" in labels


async def test_slots_empty_day(session, monkeypatch):
    monkeypatch.setattr(config, "WORK_END", time(10, 10))
    service = await add_service(session, duration_min=45)

    callback = make_callback(f"book:day:{service.id}:{day(3).isoformat()}")
    await handlers.choose_slot(callback)

    assert "Свободного времени" in body(callback)
    assert f"book:svc:{service.id}" in datas(callback)


# --- Шаг 4: подтверждение и запись ---

async def test_confirm_screen_shows_details(session):
    service = await add_service(session, "Окрашивание", 150, 5000)
    starts_at = at(2, 12, 30)

    callback = make_callback(f"book:slot:{service.id}:{starts_at.isoformat(timespec='minutes')}")
    await handlers.confirm_slot(callback)

    text = body(callback)
    assert "Окрашивание" in text and "12:30" in text
    assert "2 ч 30 мин" in text and "5 000 ₽" in text
    assert f"book:ok:{service.id}:{starts_at.isoformat(timespec='minutes')}" in datas(callback)


async def test_booking_created_and_manager_notified(session, silent_notify):
    notify_new, _ = silent_notify
    service = await add_service(session)
    starts_at = at(2, 11, 0)

    callback = make_callback(f"book:ok:{service.id}:{starts_at.isoformat(timespec='minutes')}")
    await handlers.create_booking(callback)

    saved = await repo.user_bookings(session, 100)
    assert len(saved) == 1
    assert saved[0].starts_at == starts_at and saved[0].status == "active"
    assert "Вы записаны" in body(callback)
    notify_new.assert_awaited_once()
    assert notify_new.await_args.args[1].id == saved[0].id


async def test_booking_race_slot_taken(session):
    service = await add_service(session)
    starts_at = at(2, 11, 0)
    await add_user(session, 200)
    await add_booking(session, service, starts_at, user_id=200)

    callback = make_callback(f"book:ok:{service.id}:{starts_at.isoformat(timespec='minutes')}")
    await handlers.create_booking(callback)  # исключение наружу не выходит

    assert "только что заняли" in body(callback)
    assert any(d.startswith("book:slot:") for d in datas(callback))  # снова показали слоты
    assert await repo.user_bookings(session, 100) == []
    callback.answer.assert_awaited_with("Время уже занято", show_alert=True)


async def test_booking_in_the_past_rejected(session):
    service = await add_service(session)
    starts_at = at(-1, 11, 0)

    callback = make_callback(f"book:ok:{service.id}:{starts_at.isoformat(timespec='minutes')}")
    await handlers.create_booking(callback)

    assert "уже прошло" in body(callback)
    assert await repo.user_bookings(session, 100, upcoming_only=False) == []


# --- Мои записи и отмена ---

async def test_my_bookings_empty(session):
    callback = make_callback("menu:mybookings")
    await handlers.my_bookings(callback)

    assert "нет предстоящих записей" in body(callback)
    assert "menu:book" in datas(callback)


async def test_my_bookings_lists_upcoming(session):
    service = await add_service(session, "Укладка", 60, 1500)
    await add_user(session)
    item = await add_booking(session, service, at(2, 15, 0))
    await add_booking(session, service, at(-2, 15, 0))  # прошлая — не показываем

    callback = make_callback("menu:mybookings")
    await handlers.my_bookings(callback)

    text = body(callback)
    assert "Укладка" in text and "15:00" in text and "1 500 ₽" in text
    cancel_datas = [d for d in datas(callback) if d.startswith("book:cancel:")]
    assert cancel_datas == [f"book:cancel:{item.id}"]


async def test_cancel_in_time(session, silent_notify):
    _, notify_cancel = silent_notify
    service = await add_service(session)
    await add_user(session)
    item = await add_booking(session, service, at(2, 16, 0))

    booking_id = item.id

    callback = make_callback(f"book:cancel:{booking_id}")
    await handlers.cancel_booking(callback)

    session.expire_all()
    assert (await session.get(Booking, booking_id)).status == "cancelled"
    assert "отменена" in body(callback)
    notify_cancel.assert_awaited_once()


async def test_cancel_too_late(session, silent_notify):
    _, notify_cancel = silent_notify
    service = await add_service(session)
    await add_user(session)
    item = await add_booking(session, service, utcnow() + timedelta(hours=1))

    booking_id = item.id

    callback = make_callback(f"book:cancel:{booking_id}")
    await handlers.cancel_booking(callback)

    session.expire_all()
    assert (await session.get(Booking, booking_id)).status == "active"
    text = body(callback)
    assert "не позднее чем за" in text and str(config.CANCEL_DEADLINE_HOURS) in text
    assert "menu:ask" in datas(callback)
    notify_cancel.assert_not_awaited()


async def test_cancel_foreign_booking(session):
    service = await add_service(session)
    await add_user(session, 200)
    item = await add_booking(session, service, at(2, 17, 0), user_id=200)

    booking_id = item.id

    callback = make_callback(f"book:cancel:{booking_id}")  # клиент 100 чужую запись не отменит
    await handlers.cancel_booking(callback)

    session.expire_all()
    assert (await session.get(Booking, booking_id)).status == "active"
    assert "не найдена" in body(callback)


# --- Форматирование ---

def test_day_label_variants():
    assert handlers.day_label(TODAY, TODAY) == "Сегодня"
    assert handlers.day_label(TODAY + timedelta(days=1), TODAY) == "Завтра"
    assert handlers.day_label(date(2026, 9, 4), date(2026, 9, 1)) == "Пт, 4 сент."


def test_money_and_duration():
    assert handlers.money(12000) == "12 000 ₽"
    assert handlers.money(800) == "800 ₽"
    assert handlers.duration_label(45) == "45 мин"
    assert handlers.duration_label(60) == "1 ч"
    assert handlers.duration_label(150) == "2 ч 30 мин"
    assert handlers.full_date(date(2026, 9, 4)) == "4 сентября"
    assert [handlers.hours_word(n) for n in (1, 3, 5, 11)] == ["час", "часа", "часов", "часов"]
