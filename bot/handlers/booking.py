"""Запись клиентов: выбор услуги, дня и времени, подтверждение и отмена."""

from datetime import date, datetime, timedelta

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot import config, db, repo
from bot.models import Booking, utcnow
from bot.services.notify import notify_cancelled, notify_new_booking

router = Router()

WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
MONTHS_SHORT = ("янв.", "фев.", "мар.", "апр.", "мая", "июн.",
                "июл.", "авг.", "сент.", "окт.", "нояб.", "дек.")
MONTHS_GEN = ("января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря")

SERVICE_GONE = "Эта услуга больше недоступна. Выберите другую."
SLOTS_PER_ROW = 3


# --- Форматирование ---

def day_label(day: date, today: date) -> str:
    """Подпись дня для кнопки: «Сегодня», «Завтра» или «Пт, 5 сент.»."""
    if day == today:
        return "Сегодня"
    if day == today + timedelta(days=1):
        return "Завтра"
    return f"{WEEKDAYS[day.weekday()]}, {day.day} {MONTHS_SHORT[day.month - 1]}"


def full_date(day: date) -> str:
    """Дата словами: «5 сентября»."""
    return f"{day.day} {MONTHS_GEN[day.month - 1]}"


def money(value: int) -> str:
    """Цена с пробелом между тысячами: «12 000 ₽»."""
    return f"{value:,}".replace(",", " ") + " ₽"


def duration_label(minutes: int) -> str:
    """Длительность по-человечески: «45 мин», «1 ч 30 мин»."""
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"{hours} ч {rest} мин"
    return f"{hours} ч" if hours else f"{rest} мин"


def hours_word(count: int) -> str:
    """Склонение слова «час» для числа часов."""
    if count % 10 == 1 and count % 100 != 11:
        return "час"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "часа"
    return "часов"


def _kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    """Клавиатура с кнопкой возврата в главное меню последней строкой."""
    menu = [InlineKeyboardButton(text="🏠 В меню", callback_data="menu:main")]
    return InlineKeyboardMarkup(inline_keyboard=[*rows, menu])


def _days_button(service_id: int) -> InlineKeyboardButton:
    """Кнопка «назад к выбору дня» для выбранной услуги."""
    return InlineKeyboardButton(text="⬅️ К выбору дня", callback_data=f"book:svc:{service_id}")


def _services_button() -> InlineKeyboardButton:
    """Кнопка возврата к списку услуг."""
    return InlineKeyboardButton(text="⬅️ К услугам", callback_data="menu:book")


def _parse(data: str) -> tuple[int, str]:
    """Разбирает 'book:<шаг>:<id услуги>:<дата или время>' (в ISO есть двоеточия)."""
    _, _, service_id, rest = data.split(":", 3)
    return int(service_id), rest


# --- Шаг 1: услуга ---

@router.callback_query(F.data == "menu:book")
async def choose_service(callback: CallbackQuery) -> None:
    """Показывает активные услуги прайса."""
    async with db.session() as s:
        services = await repo.list_services(s)
    if not services:
        await callback.message.edit_text("Запись пока недоступна: услуги не настроены.", reply_markup=_kb([]))
        await callback.answer()
        return
    rows = [
        [InlineKeyboardButton(
            text=f"{service.title} · {duration_label(service.duration_min)} · {money(service.price)}",
            callback_data=f"book:svc:{service.id}",
        )]
        for service in services
    ]
    await callback.message.edit_text("Выберите услугу:", reply_markup=_kb(rows))
    await callback.answer()


# --- Шаг 2: день ---

@router.callback_query(F.data.startswith("book:svc:"))
async def choose_day(callback: CallbackQuery) -> None:
    """Показывает ближайшие дни, где для услуги есть хотя бы один свободный слот."""
    service_id = int(callback.data.split(":")[2])
    now = utcnow()
    async with db.session() as s:
        service = await repo.get_service(s, service_id)
        if service is None or not service.is_active:
            await callback.message.edit_text(SERVICE_GONE, reply_markup=_kb([[_services_button()]]))
            await callback.answer()
            return
        days = [
            day
            for day in (now.date() + timedelta(days=shift) for shift in range(config.BOOKING_DAYS_AHEAD))
            if await repo.free_slots(
                s, service, day, config.WORK_START, config.WORK_END, config.SLOT_STEP_MIN, now
            )
        ]

    if not days:
        await callback.message.edit_text(
            f"На ближайшие {config.BOOKING_DAYS_AHEAD} дн. свободного времени нет.\n"
            "Оставьте заявку — подберём время вручную.",
            reply_markup=_kb([
                [InlineKeyboardButton(text="✉️ Оставить заявку", callback_data="menu:ask")],
                [_services_button()],
            ]),
        )
        await callback.answer()
        return

    rows = [
        [InlineKeyboardButton(
            text=day_label(day, now.date()),
            callback_data=f"book:day:{service_id}:{day.isoformat()}",
        )]
        for day in days
    ]
    rows.append([_services_button()])
    await callback.message.edit_text(
        f"{service.title}\n{duration_label(service.duration_min)} · {money(service.price)}\n\nВыберите день:",
        reply_markup=_kb(rows),
    )
    await callback.answer()


# --- Шаг 3: время ---

async def _show_slots(callback: CallbackQuery, service_id: int, day: date, note: str = "") -> None:
    """Рисует сетку свободных слотов дня по три в ряд."""
    now = utcnow()
    async with db.session() as s:
        service = await repo.get_service(s, service_id)
        if service is None or not service.is_active:
            await callback.message.edit_text(SERVICE_GONE, reply_markup=_kb([[_services_button()]]))
            return
        slots = await repo.free_slots(
            s, service, day, config.WORK_START, config.WORK_END, config.SLOT_STEP_MIN, now
        )

    if not slots:
        await callback.message.edit_text(
            f"{note}Свободного времени на {full_date(day)} не осталось. Выберите другой день.",
            reply_markup=_kb([[_days_button(service_id)]]),
        )
        return

    buttons = [
        InlineKeyboardButton(
            text=slot.strftime("%H:%M"),
            callback_data=f"book:slot:{service_id}:{slot.isoformat(timespec='minutes')}",
        )
        for slot in slots
    ]
    rows = [buttons[i:i + SLOTS_PER_ROW] for i in range(0, len(buttons), SLOTS_PER_ROW)]
    rows.append([_days_button(service_id)])
    await callback.message.edit_text(
        f"{note}{service.title} · {full_date(day)}\nВыберите время:",
        reply_markup=_kb(rows),
    )


@router.callback_query(F.data.startswith("book:day:"))
async def choose_slot(callback: CallbackQuery) -> None:
    """Слоты выбранного дня."""
    service_id, raw_day = _parse(callback.data)
    await _show_slots(callback, service_id, date.fromisoformat(raw_day))
    await callback.answer()


# --- Шаг 4: подтверждение ---

@router.callback_query(F.data.startswith("book:slot:"))
async def confirm_slot(callback: CallbackQuery) -> None:
    """Показывает карточку записи перед подтверждением."""
    service_id, raw_time = _parse(callback.data)
    starts_at = datetime.fromisoformat(raw_time)
    async with db.session() as s:
        service = await repo.get_service(s, service_id)
    if service is None or not service.is_active:
        await callback.message.edit_text(SERVICE_GONE, reply_markup=_kb([[_services_button()]]))
        await callback.answer()
        return

    await callback.message.edit_text(
        "Проверьте запись:\n\n"
        f"Услуга: {service.title}\n"
        f"Дата: {full_date(starts_at.date())}\n"
        f"Время: {starts_at:%H:%M}\n"
        f"Длительность: {duration_label(service.duration_min)}\n"
        f"Стоимость: {money(service.price)}",
        reply_markup=_kb([
            [InlineKeyboardButton(
                text="✅ Подтвердить",
                callback_data=f"book:ok:{service_id}:{starts_at.isoformat(timespec='minutes')}",
            )],
            [InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=f"book:day:{service_id}:{starts_at.date().isoformat()}",
            )],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("book:ok:"))
async def create_booking(callback: CallbackQuery) -> None:
    """Создаёт запись; занятый слот возвращает клиента к выбору времени."""
    service_id, raw_time = _parse(callback.data)
    starts_at = datetime.fromisoformat(raw_time)
    if starts_at < utcnow():
        await _show_slots(callback, service_id, starts_at.date(), note="Это время уже прошло.\n\n")
        await callback.answer("Время уже прошло", show_alert=True)
        return

    async with db.session() as s:
        service = await repo.get_service(s, service_id)
        if service is None or not service.is_active:
            await callback.message.edit_text(SERVICE_GONE, reply_markup=_kb([[_services_button()]]))
            await callback.answer()
            return
        user = await repo.upsert_user(
            s, callback.from_user.id, callback.from_user.username, callback.from_user.full_name
        )
        try:
            booking = await repo.create_booking(s, user.id, service_id, starts_at)
        except repo.SlotTaken:
            booking = None

    if booking is None:
        await _show_slots(
            callback, service_id, starts_at.date(), note="Это время только что заняли.\n\n"
        )
        await callback.answer("Время уже занято", show_alert=True)
        return

    await notify_new_booking(callback.bot, booking, service, user)
    await callback.message.edit_text(
        "✅ Вы записаны!\n\n"
        f"Услуга: {service.title}\n"
        f"Когда: {full_date(starts_at.date())} в {starts_at:%H:%M}\n"
        f"Стоимость: {money(service.price)}\n\n"
        "Напомним о визите заранее. Отменить можно в разделе «Мои записи».",
        reply_markup=_kb([[InlineKeyboardButton(text="📋 Мои записи", callback_data="menu:mybookings")]]),
    )
    await callback.answer("Запись создана")


# --- Мои записи и отмена ---

@router.callback_query(F.data == "menu:mybookings")
async def my_bookings(callback: CallbackQuery) -> None:
    """Список будущих записей клиента с кнопками отмены."""
    now = utcnow()
    async with db.session() as s:
        bookings = await repo.user_bookings(s, callback.from_user.id, upcoming_only=True, now=now)

    if not bookings:
        await callback.message.edit_text(
            "У вас пока нет предстоящих записей.",
            reply_markup=_kb([[InlineKeyboardButton(text="🗓 Записаться", callback_data="menu:book")]]),
        )
        await callback.answer()
        return

    lines, rows = [], []
    for booking in bookings:
        when = f"{full_date(booking.starts_at.date())} в {booking.starts_at:%H:%M}"
        lines.append(f"• {booking.service.title}\n  {when} · {money(booking.service.price)}")
        rows.append([InlineKeyboardButton(
            text=f"❌ Отменить {booking.starts_at:%d.%m %H:%M}",
            callback_data=f"book:cancel:{booking.id}",
        )])

    await callback.message.edit_text("Ваши записи:\n\n" + "\n".join(lines), reply_markup=_kb(rows))
    await callback.answer()


@router.callback_query(F.data.startswith("book:cancel:"))
async def cancel_booking(callback: CallbackQuery) -> None:
    """Отменяет запись, если до визита осталось больше разрешённого срока."""
    booking_id = int(callback.data.split(":")[2])
    hours = config.CANCEL_DEADLINE_HOURS
    async with db.session() as s:
        booking = await s.get(Booking, booking_id)
        result = await repo.cancel_booking(s, booking_id, callback.from_user.id, utcnow(), hours)
        if result == "ok":
            await notify_cancelled(callback.bot, booking, booking.service, booking.user)

    if result == "ok":
        await callback.message.edit_text(
            "Запись отменена. Будем рады видеть вас в другое время.",
            reply_markup=_kb([[InlineKeyboardButton(text="🗓 Записаться", callback_data="menu:book")]]),
        )
        await callback.answer("Запись отменена")
        return

    if result == "too_late":
        await callback.message.edit_text(
            f"Отменить запись можно не позднее чем за {hours} {hours_word(hours)} до визита.\n"
            "Напишите нам — постараемся перенести.",
            reply_markup=_kb([
                [InlineKeyboardButton(text="✉️ Связаться с нами", callback_data="menu:ask")],
                [InlineKeyboardButton(text="📋 Мои записи", callback_data="menu:mybookings")],
            ]),
        )
        await callback.answer("Слишком поздно для отмены", show_alert=True)
        return

    await callback.message.edit_text(
        "Запись не найдена — возможно, она уже отменена.",
        reply_markup=_kb([[InlineKeyboardButton(text="📋 Мои записи", callback_data="menu:mybookings")]]),
    )
    await callback.answer()
