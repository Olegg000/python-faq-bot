from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from bot import repo
from bot.models import Booking, Service, User
from bot.seed import DEFAULT_SERVICES, seed_services

DAY = date(2026, 3, 10)
WORK_START = time(10, 0)
WORK_END = time(20, 0)
STEP = 30
MORNING = datetime(2026, 3, 10, 9, 0)  # «сейчас» до начала смены


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 3, 10, hour, minute)


async def add_service(session, title="Стрижка", duration_min=45, price=1200, **kwargs) -> Service:
    service = Service(title=title, duration_min=duration_min, price=price, **kwargs)
    session.add(service)
    await session.commit()
    return service


async def add_user(session, tg_id=100, created_at=None) -> User:
    user = User(id=tg_id, username=f"u{tg_id}", full_name="Клиент", created_at=created_at or MORNING)
    session.add(user)
    await session.commit()
    return user


async def test_upsert_user_creates_and_updates(session):
    created = await repo.upsert_user(session, 100, "vasya", "Вася Пупкин")
    assert created.id == 100 and created.full_name == "Вася Пупкин"

    updated = await repo.upsert_user(session, 100, "vasya_new", "Вася Пупкин мл.")
    assert updated.username == "vasya_new"
    assert len(await repo.all_user_ids(session)) == 1


async def test_all_user_ids_and_mark_blocked(session):
    await repo.upsert_user(session, 1, "a", "A")
    await repo.upsert_user(session, 2, "b", "B")

    await repo.mark_blocked(session, 2)

    assert await repo.all_user_ids(session) == [1]
    assert await repo.all_user_ids(session, only_unblocked=False) == [1, 2]


async def test_list_services_respects_active_and_sort(session):
    await add_service(session, "Вторая", sort=20)
    await add_service(session, "Первая", sort=10)
    await add_service(session, "Скрытая", sort=5, is_active=False)

    assert [s.title for s in await repo.list_services(session)] == ["Первая", "Вторая"]
    assert len(await repo.list_services(session, only_active=False)) == 3


async def test_set_service_active_and_price(session):
    service = await add_service(session)

    assert (await repo.set_service_active(session, service.id, False)).is_active is False
    assert (await repo.set_service_price(session, service.id, 1900)).price == 1900
    assert await repo.get_service(session, service.id) is service
    assert await repo.set_service_active(session, 999, True) is None
    assert await repo.set_service_price(session, 999, 100) is None


async def test_free_slots_fills_working_day(session):
    service = await add_service(session, duration_min=45)

    slots = await repo.free_slots(session, service, DAY, WORK_START, WORK_END, STEP, MORNING)

    assert slots[0] == at(10, 0)
    assert slots[-1] == at(19, 0)  # 19:00 + 45 мин влезает, 19:30 — уже нет
    assert len(slots) == 19


async def test_free_slots_long_service_fits_until_closing(session):
    service = await add_service(session, "Окрашивание", duration_min=150)

    slots = await repo.free_slots(session, service, DAY, WORK_START, WORK_END, STEP, MORNING)

    assert slots[-1] == at(17, 30)  # ровно до 20:00


async def test_free_slots_excludes_overlaps_from_both_sides(session):
    long_service = await add_service(session, "Окрашивание", duration_min=150)
    short = await add_service(session, "Борода", duration_min=45)
    await add_user(session)
    await repo.create_booking(session, 100, long_service.id, at(12, 0))  # занято 12:00–14:30

    slots = await repo.free_slots(session, short, DAY, WORK_START, WORK_END, STEP, MORNING)

    assert at(11, 0) in slots  # 11:00–11:45 — до занятого
    assert at(11, 30) not in slots  # 11:30–12:15 — заезжает началом на занятое
    assert at(12, 0) not in slots
    assert at(14, 0) not in slots  # 14:00–14:45 — заезжает хвостом
    assert at(14, 30) in slots  # впритык после занятого


async def test_free_slots_drops_past_time(session):
    service = await add_service(session, duration_min=45)

    slots = await repo.free_slots(session, service, DAY, WORK_START, WORK_END, STEP, at(13, 15))

    assert slots[0] == at(13, 30)
    assert at(13, 0) not in slots


async def test_create_booking_on_taken_exact_time(session):
    service = await add_service(session, duration_min=45)
    await add_user(session)
    await repo.create_booking(session, 100, service.id, at(12, 0))

    with pytest.raises(repo.SlotTaken):
        await repo.create_booking(session, 100, service.id, at(12, 0))


async def test_create_booking_on_overlapping_tail(session):
    long_service = await add_service(session, "Окрашивание", duration_min=150)
    short = await add_service(session, "Борода", duration_min=30)
    await add_user(session)
    await repo.create_booking(session, 100, long_service.id, at(12, 0))

    with pytest.raises(repo.SlotTaken):
        await repo.create_booking(session, 100, short.id, at(14, 0))  # 14:00–14:30 внутри чужой

    booking = await repo.create_booking(session, 100, short.id, at(14, 30))
    assert booking.id is not None


async def test_create_booking_new_covers_existing_start(session):
    short = await add_service(session, "Борода", duration_min=30)
    long_service = await add_service(session, "Окрашивание", duration_min=150)
    await add_user(session)
    await repo.create_booking(session, 100, short.id, at(13, 0))  # занято 13:00–13:30

    with pytest.raises(repo.SlotTaken):
        await repo.create_booking(session, 100, long_service.id, at(11, 0))  # 11:00–13:30


async def test_create_booking_unknown_service(session):
    await add_user(session)
    with pytest.raises(ValueError):
        await repo.create_booking(session, 100, 999, at(12, 0))


async def test_unique_index_blocks_second_active_booking(session):
    service = await add_service(session)
    await add_user(session)
    session.add(Booking(user_id=100, service_id=service.id, starts_at=at(12, 0), status="cancelled"))
    session.add(Booking(user_id=100, service_id=service.id, starts_at=at(12, 0)))
    await session.commit()

    session.add(Booking(user_id=100, service_id=service.id, starts_at=at(12, 0)))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_user_bookings_upcoming_and_history(session):
    service = await add_service(session)
    await add_user(session)
    await repo.create_booking(session, 100, service.id, at(11, 0))
    future = await repo.create_booking(session, 100, service.id, at(16, 0))
    await repo.cancel_booking(session, future.id, 100, at(9, 0), deadline_hours=3)
    await repo.create_booking(session, 100, service.id, at(18, 0))

    upcoming = await repo.user_bookings(session, 100, now=at(12, 0))
    assert [b.starts_at for b in upcoming] == [at(18, 0)]

    history = await repo.user_bookings(session, 100, upcoming_only=False)
    assert len(history) == 3
    assert await repo.user_bookings(session, 999, now=MORNING) == []


async def test_cancel_booking_statuses(session):
    service = await add_service(session)
    await add_user(session)
    booking = await repo.create_booking(session, 100, service.id, at(18, 0))

    assert await repo.cancel_booking(session, booking.id, 100, at(17, 0), 3) == "too_late"
    assert await repo.cancel_booking(session, booking.id, 999, at(9, 0), 3) == "not_found"
    assert await repo.cancel_booking(session, booking.id, 100, at(9, 0), 3) == "ok"
    assert await repo.cancel_booking(session, booking.id, 100, at(9, 0), 3) == "not_found"


async def test_cancelled_slot_becomes_free_again(session):
    service = await add_service(session, duration_min=45)
    await add_user(session)
    booking = await repo.create_booking(session, 100, service.id, at(12, 0))
    await repo.cancel_booking(session, booking.id, 100, MORNING, 3)

    slots = await repo.free_slots(session, service, DAY, WORK_START, WORK_END, STEP, MORNING)
    assert at(12, 0) in slots
    assert (await repo.create_booking(session, 100, service.id, at(12, 0))).id != booking.id


async def test_bookings_for_day_only_active_sorted(session):
    service = await add_service(session)
    await add_user(session)
    await repo.create_booking(session, 100, service.id, at(16, 0))
    await repo.create_booking(session, 100, service.id, at(11, 0))
    cancelled = await repo.create_booking(session, 100, service.id, at(13, 0))
    await repo.cancel_booking(session, cancelled.id, 100, MORNING, 3)
    await repo.create_booking(session, 100, service.id, datetime(2026, 3, 11, 12, 0))

    day = await repo.bookings_for_day(session, DAY)

    assert [b.starts_at for b in day] == [at(11, 0), at(16, 0)]


async def test_due_reminders_by_kind_and_mark(session):
    service = await add_service(session)
    await add_user(session)
    now = at(10, 0)
    soon = await repo.create_booking(session, 100, service.id, at(11, 30))  # через 1.5 часа
    tomorrow = await repo.create_booking(session, 100, service.id, datetime(2026, 3, 11, 9, 0))
    await repo.create_booking(session, 100, service.id, datetime(2026, 3, 12, 12, 0))  # далеко

    assert [b.id for b in await repo.due_reminders(session, now, "2h")] == [soon.id]
    assert [b.id for b in await repo.due_reminders(session, now, "24h")] == [soon.id, tomorrow.id]

    await repo.mark_reminded(session, soon.id, "2h")
    assert await repo.due_reminders(session, now, "2h") == []
    assert [b.id for b in await repo.due_reminders(session, now, "24h")] == [soon.id, tomorrow.id]

    await repo.mark_reminded(session, tomorrow.id, "24h")
    assert [b.id for b in await repo.due_reminders(session, now, "24h")] == [soon.id]


async def test_due_reminders_skip_past_and_cancelled(session):
    service = await add_service(session)
    await add_user(session)
    past = await repo.create_booking(session, 100, service.id, at(10, 0))
    cancelled = await repo.create_booking(session, 100, service.id, at(12, 0))
    await repo.cancel_booking(session, cancelled.id, 100, MORNING, 3)

    assert await repo.due_reminders(session, at(11, 0), "2h") == []
    assert past.starts_at < at(11, 0)


async def test_leads_saved_newest_first(session):
    await repo.create_lead(session, 100, "Иван", "Сколько стоит окрашивание?")
    await repo.create_lead(session, 101, "Пётр", "Работаете в воскресенье?")

    leads = await repo.list_leads(session)

    assert [lead.name for lead in leads] == ["Пётр", "Иван"]
    assert leads[0].status == "new"
    assert len(await repo.list_leads(session, limit=1)) == 1


async def test_stats_counts_revenue_and_conversion(session):
    haircut = await add_service(session, "Стрижка", duration_min=45, price=1200)
    color = await add_service(session, "Окрашивание", duration_min=150, price=5000)
    now = at(12, 0)
    await add_user(session, 100, created_at=now - timedelta(days=1))
    await add_user(session, 101, created_at=now - timedelta(days=2))
    await add_user(session, 102, created_at=now - timedelta(days=60))  # старый клиент
    await repo.create_booking(session, 100, haircut.id, at(15, 0))
    await repo.create_booking(session, 100, haircut.id, at(16, 0))
    await repo.create_booking(session, 101, color.id, at(17, 0))
    cancelled = await repo.create_booking(session, 102, color.id, datetime(2026, 3, 11, 12, 0))
    await repo.cancel_booking(session, cancelled.id, 102, now, 3)

    data = await repo.stats(session, now, days=30)

    assert data["bookings"] == 3
    assert data["revenue"] == 1200 + 1200 + 5000
    assert data["new_users"] == 2  # клиент 102 зарегистрирован раньше периода
    assert data["top_services"] == [("Стрижка", 2), ("Окрашивание", 1)]
    assert data["conversion"] == 1.0


async def test_stats_empty_period(session):
    data = await repo.stats(session, at(12, 0), days=30)

    assert data == {
        "bookings": 0,
        "revenue": 0,
        "new_users": 0,
        "top_services": [],
        "conversion": 0.0,
    }


async def test_seed_services_runs_once(session):
    assert await seed_services(session) == len(DEFAULT_SERVICES)
    assert await seed_services(session) == 0

    services = await repo.list_services(session)
    assert len(services) == len(DEFAULT_SERVICES)
    assert all(s.duration_min > 0 and s.price > 0 for s in services)
