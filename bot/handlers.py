"""Все хендлеры бота: меню, FAQ, заявки (FSM), курсы валют."""

import json
from datetime import datetime, timezone
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import config, rates

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
LEADS_FILE = DATA_DIR / "leads.json"
FAQ = json.loads((DATA_DIR / "faq.json").read_text(encoding="utf-8"))

PRICES_TEXT = (
    "Наши цены:\n\n"
    "• Базовый пакет — от 5 000 ₽\n"
    "• Стандарт — от 12 000 ₽\n"
    "• Премиум — от 25 000 ₽\n\n"
    "Точная стоимость зависит от задачи — нажмите «Задать вопрос», и мы посчитаем."
)

router = Router()


class AskQuestion(StatesGroup):
    name = State()
    question = State()
    confirm = State()


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❓ FAQ", callback_data="menu:faq")],
        [InlineKeyboardButton(text="💰 Цены", callback_data="menu:prices")],
        [InlineKeyboardButton(text="✉️ Задать вопрос", callback_data="menu:ask")],
        [InlineKeyboardButton(text="💱 Курс валют", callback_data="menu:rates")],
    ])


def back_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:main")],
    ])


def save_lead(lead: dict) -> None:
    leads = json.loads(LEADS_FILE.read_text(encoding="utf-8")) if LEADS_FILE.exists() else []
    leads.append(lead)
    LEADS_FILE.write_text(json.dumps(leads, ensure_ascii=False, indent=2), encoding="utf-8")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Здравствуйте! Я бот-помощник компании.\n"
        "Отвечу на частые вопросы, покажу цены и приму вашу заявку.",
        reply_markup=main_menu(),
    )


@router.callback_query(F.data == "menu:main")
async def show_main(callback: CallbackQuery) -> None:
    await callback.message.edit_text("Главное меню:", reply_markup=main_menu())
    await callback.answer()


@router.callback_query(F.data == "menu:prices")
async def show_prices(callback: CallbackQuery) -> None:
    await callback.message.edit_text(PRICES_TEXT, reply_markup=back_button())
    await callback.answer()


@router.callback_query(F.data == "menu:faq")
async def show_faq(callback: CallbackQuery) -> None:
    keyboard = [
        [InlineKeyboardButton(text=item["question"], callback_data=f"faq:{i}")]
        for i, item in enumerate(FAQ)
    ]
    keyboard.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:main")])
    await callback.message.edit_text(
        "Частые вопросы:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("faq:"))
async def show_faq_answer(callback: CallbackQuery) -> None:
    item = FAQ[int(callback.data.split(":")[1])]
    await callback.message.edit_text(
        f"❓ {item['question']}\n\n{item['answer']}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ К вопросам", callback_data="menu:faq")],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="menu:main")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:rates")
async def show_rates(callback: CallbackQuery) -> None:
    provider = rates.get_provider(config.RATE_PROVIDER)
    data = await provider.get_rates()
    lines = "\n".join(f"{cur}: {value:.2f} ₽" for cur, value in data.items())
    await callback.message.edit_text(f"Курсы валют к рублю:\n\n{lines}", reply_markup=back_button())
    await callback.answer()


# --- FSM: заявка «Задать вопрос» ---

@router.callback_query(F.data == "menu:ask")
async def ask_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AskQuestion.name)
    await callback.message.edit_text("Как вас зовут?")
    await callback.answer()


@router.message(AskQuestion.name)
async def ask_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=message.text)
    await state.set_state(AskQuestion.question)
    await message.answer("Опишите ваш вопрос:")


@router.message(AskQuestion.question)
async def ask_question(message: Message, state: FSMContext) -> None:
    await state.update_data(question=message.text)
    await state.set_state(AskQuestion.confirm)
    data = await state.get_data()
    await message.answer(
        f"Проверьте заявку:\n\nИмя: {data['name']}\nВопрос: {data['question']}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Отправить", callback_data="ask:confirm")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="ask:cancel")],
        ]),
    )


@router.callback_query(AskQuestion.confirm, F.data == "ask:confirm")
async def ask_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    save_lead({
        "name": data["name"],
        "question": data["question"],
        "user_id": callback.from_user.id,
        "username": callback.from_user.username,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    await state.clear()
    await callback.message.edit_text(
        "Спасибо! Заявка принята, менеджер свяжется с вами.",
        reply_markup=main_menu(),
    )
    await callback.answer()


@router.callback_query(AskQuestion.confirm, F.data == "ask:cancel")
async def ask_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Заявка отменена.", reply_markup=main_menu())
    await callback.answer()
