"""Админ-панель: записи на день, статистика, заявки, услуги и рассылка."""

import functools

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import config, db, repo
from bot.models import utcnow
from bot.services.broadcast import send_broadcast
from bot.services.notify import client_label

router = Router()

DENIED = "Раздел доступен только администраторам."
STATS_DAYS = 30
LEADS_LIMIT = 10
TOP_SERVICES = 3


class BroadcastForm(StatesGroup):
    text = State()
    confirm = State()


def admin_only(handler):
    """Пускает в админку только id из ADMIN_IDS, остальным — вежливый отказ."""

    @functools.wraps(handler)
    async def wrapper(event, *args, **kwargs):
        if event.from_user.id not in config.ADMIN_IDS:
            await event.answer(DENIED)
            return None
        return await handler(event, *args, **kwargs)

    return wrapper


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Записи на сегодня", callback_data="adm:today")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats")],
        [InlineKeyboardButton(text="✉️ Заявки", callback_data="adm:leads")],
        [InlineKeyboardButton(text="🛠 Услуги", callback_data="adm:services")],
        [InlineKeyboardButton(text="📣 Рассылка", callback_data="adm:broadcast")],
    ])


def back_to_admin() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ В админку", callback_data="adm:menu")],
    ])


def money(value: int) -> str:
    """Число рублей с пробелом между разрядами: 12300 -> '12 300'."""
    return f"{value:,}".replace(",", " ")


# --- Меню ---

@router.message(Command("admin"))
@admin_only
async def cmd_admin(message: Message) -> None:
    await message.answer("Админ-панель:", reply_markup=admin_menu())


@router.callback_query(F.data == "adm:menu")
@admin_only
async def show_admin_menu(callback: CallbackQuery) -> None:
    await callback.message.edit_text("Админ-панель:", reply_markup=admin_menu())
    await callback.answer()


# --- Записи на сегодня ---

@router.callback_query(F.data == "adm:today")
@admin_only
async def show_today(callback: CallbackQuery) -> None:
    today = utcnow().date()
    async with db.session() as s:
        bookings = await repo.bookings_for_day(s, today)

    if bookings:
        lines = "\n".join(
            f"{b.starts_at:%H:%M} — {b.service.title} — {client_label(b.user)}" for b in bookings
        )
        text = f"Записи на {today:%d.%m}:\n\n{lines}"
    else:
        text = f"На {today:%d.%m} записей нет."
    await callback.message.edit_text(text, reply_markup=back_to_admin())
    await callback.answer()


# --- Статистика ---

@router.callback_query(F.data == "adm:stats")
@admin_only
async def show_stats(callback: CallbackQuery) -> None:
    async with db.session() as s:
        data = await repo.stats(s, utcnow(), days=STATS_DAYS)

    top = data["top_services"][:TOP_SERVICES]
    top_text = "\n".join(f"{i}. {title} — {count}" for i, (title, count) in enumerate(top, 1))
    await callback.message.edit_text(
        f"Статистика за {STATS_DAYS} дней:\n\n"
        f"Записей: {data['bookings']}\n"
        f"Выручка: {money(data['revenue'])} ₽\n"
        f"Новых клиентов: {data['new_users']}\n"
        f"Конверсия: {round(data['conversion'] * 100)}%\n\n"
        f"Топ услуг:\n{top_text or 'пока пусто'}",
        reply_markup=back_to_admin(),
    )
    await callback.answer()


# --- Заявки ---

@router.callback_query(F.data == "adm:leads")
@admin_only
async def show_leads(callback: CallbackQuery) -> None:
    async with db.session() as s:
        leads = await repo.list_leads(s, limit=LEADS_LIMIT)

    if leads:
        lines = "\n\n".join(
            f"№{lead.id} · {lead.created_at:%d.%m %H:%M} · {lead.name}\n{lead.question}"
            for lead in leads
        )
        text = f"Последние заявки:\n\n{lines}"
    else:
        text = "Заявок пока нет."
    await callback.message.edit_text(text, reply_markup=back_to_admin())
    await callback.answer()


# --- Услуги ---

def services_markup(services: list) -> InlineKeyboardMarkup:
    """Кнопка на услугу: тумблер «включена / скрыта»."""
    rows = [
        [InlineKeyboardButton(
            text=f"{'🟢' if item.is_active else '🔴'} {item.title}",
            callback_data=f"adm:svc:{item.id}",
        )]
        for item in services
    ]
    rows.append([InlineKeyboardButton(text="⬅️ В админку", callback_data="adm:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def services_text(services: list) -> str:
    if not services:
        return "Прайс пуст."
    lines = "\n".join(
        f"{'🟢' if item.is_active else '🔴'} {item.title} — {money(item.price)} ₽, {item.duration_min} мин"
        for item in services
    )
    return f"Услуги (нажмите, чтобы включить или скрыть):\n\n{lines}"


@router.callback_query(F.data == "adm:services")
@admin_only
async def show_services(callback: CallbackQuery) -> None:
    async with db.session() as s:
        services = await repo.list_services(s, only_active=False)
    await callback.message.edit_text(services_text(services), reply_markup=services_markup(services))
    await callback.answer()


@router.callback_query(F.data.startswith("adm:svc:"))
@admin_only
async def toggle_service(callback: CallbackQuery) -> None:
    service_id = int(callback.data.split(":")[2])
    async with db.session() as s:
        service = await repo.get_service(s, service_id)
        if service is None:
            await callback.answer("Услуга не найдена")
            return
        service = await repo.set_service_active(s, service_id, not service.is_active)
        services = await repo.list_services(s, only_active=False)

    await callback.message.edit_text(services_text(services), reply_markup=services_markup(services))
    await callback.answer("Услуга включена" if service.is_active else "Услуга скрыта")


# --- Рассылка ---

@router.callback_query(F.data == "adm:broadcast")
@admin_only
async def broadcast_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BroadcastForm.text)
    await callback.message.edit_text(
        "Пришлите текст рассылки одним сообщением.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="adm:bc:cancel")],
        ]),
    )
    await callback.answer()


@router.message(BroadcastForm.text)
@admin_only
async def broadcast_text(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Нужен текст сообщения. Пришлите текст рассылки.")
        return

    async with db.session() as s:
        recipients = len(await repo.all_user_ids(s))

    await state.update_data(text=message.text)
    await state.set_state(BroadcastForm.confirm)
    await message.answer(
        f"Предпросмотр рассылки ({recipients} получателей):\n\n{message.text}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Отправить", callback_data="adm:bc:send")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="adm:bc:cancel")],
        ]),
    )


@router.callback_query(BroadcastForm.confirm, F.data == "adm:bc:send")
@admin_only
async def broadcast_send(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    text = (await state.get_data()).get("text", "")
    await state.clear()
    await callback.message.edit_text("Рассылка пошла, это займёт время…")

    async with db.session() as s:
        result = await send_broadcast(bot, s, text)

    await callback.message.answer(
        "Рассылка завершена.\n"
        f"Доставлено: {result['sent']}\n"
        f"Заблокировали бота: {result['blocked']}\n"
        f"Ошибок: {result['failed']}",
        reply_markup=back_to_admin(),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:bc:cancel")
@admin_only
async def broadcast_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Рассылка отменена.", reply_markup=admin_menu())
    await callback.answer()
