"""Операции с базой: клиенты, услуги, слоты, записи, заявки, статистика."""

from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Booking, Lead, Service, User, utcnow

REMINDER_WINDOWS = {"24h": timedelta(hours=24), "2h": timedelta(hours=2)}


class SlotTaken(Exception):
    """Время уже занято другой активной записью."""


# --- Клиенты ---

async def upsert_user(s: AsyncSession, tg_id: int, username: str | None, full_name: str | None) -> User:
    """Создаёт клиента или обновляет его имя и username."""
    user = await s.get(User, tg_id)
    if user is None:
        user = User(id=tg_id, username=username, full_name=full_name, created_at=utcnow())
        s.add(user)
    else:
        user.username = username
        user.full_name = full_name
        user.is_blocked = False
    await s.commit()
    return user


async def all_user_ids(s: AsyncSession, only_unblocked: bool = True) -> list[int]:
    """Список id клиентов — для рассылки."""
    query = select(User.id).order_by(User.id)
    if only_unblocked:
        query = query.where(User.is_blocked.is_(False))
    return list((await s.scalars(query)).all())


async def mark_blocked(s: AsyncSession, user_id: int) -> None:
    """Помечает клиента заблокировавшим бота, чтобы не слать ему рассылки."""
    user = await s.get(User, user_id)
    if user is not None:
        user.is_blocked = True
        await s.commit()


# --- Услуги ---

async def list_services(s: AsyncSession, only_active: bool = True) -> list[Service]:
    """Прайс в порядке сортировки."""
    query = select(Service).order_by(Service.sort, Service.id)
    if only_active:
        query = query.where(Service.is_active.is_(True))
    return list((await s.scalars(query)).all())


async def get_service(s: AsyncSession, service_id: int) -> Service | None:
    """Услуга по id."""
    return await s.get(Service, service_id)


async def set_service_active(s: AsyncSession, service_id: int, active: bool) -> Service | None:
    """Включает или скрывает услугу в прайсе."""
    service = await s.get(Service, service_id)
    if service is None:
        return None
    service.is_active = active
    await s.commit()
    return service


async def set_service_price(s: AsyncSession, service_id: int, price: int) -> Service | None:
    """Меняет цену услуги."""
    service = await s.get(Service, service_id)
    if service is None:
        return None
    service.price = price
    await s.commit()
    return service


# --- Слоты и записи ---

async def _busy_intervals(
    s: AsyncSession, since: datetime, until: datetime
) -> list[tuple[datetime, datetime]]:
    """Занятые интервалы активных записей, способные пересечь [since, until)."""
    rows = await s.execute(
        select(Booking.starts_at, Service.duration_min)
        .join(Service, Service.id == Booking.service_id)
        .where(
            Booking.status == "active",
            Booking.starts_at < until,
            Booking.starts_at >= since - timedelta(days=1),
        )
    )
    return [(starts_at, starts_at + timedelta(minutes=duration)) for starts_at, duration in rows]


def _overlaps(start: datetime, end: datetime, busy: list[tuple[datetime, datetime]]) -> bool:
    """Пересекается ли интервал [start, end) хотя бы с одним занятым."""
    return any(busy_start < end and start < busy_end for busy_start, busy_end in busy)


async def free_slots(
    s: AsyncSession,
    service: Service,
    day: date,
    work_start: time,
    work_end: time,
    step_min: int,
    now: datetime,
) -> list[datetime]:
    """Свободные слоты дня: услуга целиком влезает до конца смены и ни с чем не пересекается."""
    day_start = datetime.combine(day, work_start)
    day_end = datetime.combine(day, work_end)
    duration = timedelta(minutes=service.duration_min)
    step = timedelta(minutes=step_min)
    busy = await _busy_intervals(s, day_start, day_end)

    slots = []
    current = day_start
    while current + duration <= day_end:
        if current >= now and not _overlaps(current, current + duration, busy):
            slots.append(current)
        current += step
    return slots


async def create_booking(s: AsyncSession, user_id: int, service_id: int, starts_at: datetime) -> Booking:
    """Создаёт запись; если время занято — бросает SlotTaken."""
    service = await s.get(Service, service_id)
    if service is None:
        raise ValueError("Услуга не найдена")

    ends_at = starts_at + timedelta(minutes=service.duration_min)
    if _overlaps(starts_at, ends_at, await _busy_intervals(s, starts_at, ends_at)):
        raise SlotTaken("Это время уже занято")

    booking = Booking(user_id=user_id, service_id=service_id, starts_at=starts_at, created_at=utcnow())
    s.add(booking)
    try:
        await s.commit()
    except IntegrityError as exc:  # гонка: кто-то занял слот между проверкой и вставкой
        await s.rollback()
        raise SlotTaken("Это время уже занято") from exc
    return booking


async def user_bookings(
    s: AsyncSession, user_id: int, upcoming_only: bool = True, now: datetime | None = None
) -> list[Booking]:
    """Записи клиента: по умолчанию только активные и будущие, иначе вся история."""
    query = select(Booking).where(Booking.user_id == user_id).order_by(Booking.starts_at)
    if upcoming_only:
        query = query.where(Booking.status == "active", Booking.starts_at >= (now or utcnow()))
    return list((await s.scalars(query)).all())


async def cancel_booking(
    s: AsyncSession, booking_id: int, user_id: int, now: datetime, deadline_hours: int
) -> str:
    """Отменяет запись клиента: 'ok' | 'too_late' | 'not_found'."""
    booking = await s.scalar(
        select(Booking).where(
            Booking.id == booking_id,
            Booking.user_id == user_id,
            Booking.status == "active",
        )
    )
    if booking is None:
        return "not_found"
    if booking.starts_at - now < timedelta(hours=deadline_hours):
        return "too_late"
    booking.status = "cancelled"
    await s.commit()
    return "ok"


async def bookings_for_day(s: AsyncSession, day: date) -> list[Booking]:
    """Активные записи на день по возрастанию времени."""
    day_start = datetime.combine(day, time.min)
    return list(
        (
            await s.scalars(
                select(Booking)
                .where(
                    Booking.status == "active",
                    Booking.starts_at >= day_start,
                    Booking.starts_at < day_start + timedelta(days=1),
                )
                .order_by(Booking.starts_at)
            )
        ).all()
    )


# --- Напоминания ---

async def due_reminders(s: AsyncSession, now: datetime, kind: str) -> list[Booking]:
    """Активные записи, которым пора отправить напоминание ('24h' или '2h')."""
    window = REMINDER_WINDOWS[kind]
    flag = Booking.reminded_24h if kind == "24h" else Booking.reminded_2h
    return list(
        (
            await s.scalars(
                select(Booking)
                .where(
                    Booking.status == "active",
                    flag.is_(False),
                    Booking.starts_at >= now,
                    Booking.starts_at <= now + window,
                )
                .order_by(Booking.starts_at)
            )
        ).all()
    )


async def mark_reminded(s: AsyncSession, booking_id: int, kind: str) -> None:
    """Отмечает, что напоминание отправлено — повторно не пошлём."""
    booking = await s.get(Booking, booking_id)
    if booking is None:
        return
    if kind == "24h":
        booking.reminded_24h = True
    else:
        booking.reminded_2h = True
    await s.commit()


# --- Заявки ---

async def create_lead(s: AsyncSession, user_id: int, name: str, question: str) -> Lead:
    """Сохраняет заявку клиента."""
    lead = Lead(user_id=user_id, name=name, question=question, created_at=utcnow())
    s.add(lead)
    await s.commit()
    return lead


async def list_leads(s: AsyncSession, limit: int = 20) -> list[Lead]:
    """Последние заявки, свежие сверху."""
    return list((await s.scalars(select(Lead).order_by(Lead.id.desc()).limit(limit))).all())


# --- Статистика ---

async def stats(s: AsyncSession, now: datetime, days: int = 30) -> dict:
    """Сводка за период: записи, выручка, новые клиенты, топ услуг, конверсия."""
    since = now - timedelta(days=days)
    period = (Booking.status == "active", Booking.created_at >= since)

    bookings = await s.scalar(select(func.count()).select_from(Booking).where(*period)) or 0
    revenue = await s.scalar(
        select(func.coalesce(func.sum(Service.price), 0))
        .select_from(Booking)
        .join(Service, Service.id == Booking.service_id)
        .where(*period)
    ) or 0
    new_users = await s.scalar(select(func.count()).select_from(User).where(User.created_at >= since)) or 0
    top_rows = await s.execute(
        select(Service.title, func.count(Booking.id).label("cnt"))
        .select_from(Booking)
        .join(Service, Service.id == Booking.service_id)
        .where(*period)
        .group_by(Service.title)
        .order_by(func.count(Booking.id).desc(), Service.title)
        .limit(5)
    )
    # Конверсия: доля новых клиентов периода, дошедших до записи
    converted = await s.scalar(
        select(func.count(func.distinct(Booking.user_id)))
        .select_from(Booking)
        .join(User, User.id == Booking.user_id)
        .where(*period, User.created_at >= since)
    ) or 0

    return {
        "bookings": bookings,
        "revenue": revenue,
        "new_users": new_users,
        "top_services": [(title, count) for title, count in top_rows],
        "conversion": round(converted / new_users, 2) if new_users else 0.0,
    }
