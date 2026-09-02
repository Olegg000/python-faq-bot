"""Настройки бота: токен, база, режим работы салона — всё из окружения."""

import os
from datetime import time

from dotenv import load_dotenv

load_dotenv()


def _int_set(raw: str) -> set[int]:
    """Разбирает список id через запятую: '1, 2' -> {1, 2}."""
    return {int(part) for part in raw.replace(" ", "").split(",") if part}


def _time(raw: str, default: str) -> time:
    """Разбирает время вида '10:00'; при ошибке берёт значение по умолчанию."""
    try:
        return time.fromisoformat(raw or default)
    except ValueError:
        return time.fromisoformat(default)


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
RATE_PROVIDER = os.getenv("RATE_PROVIDER", "demo")  # demo | exchangerate

# Кто видит админку и куда падают уведомления о новых записях и заявках
ADMIN_IDS: set[int] = _int_set(os.getenv("ADMIN_IDS", ""))
_manager_chat = os.getenv("MANAGER_CHAT_ID", "").strip()
MANAGER_CHAT_ID: int | None = int(_manager_chat) if _manager_chat else None

DB_URL = os.getenv("DB_URL", "sqlite+aiosqlite:///data/bot.db")

# Расписание записи
WORK_START = _time(os.getenv("WORK_START", ""), "10:00")
WORK_END = _time(os.getenv("WORK_END", ""), "20:00")
SLOT_STEP_MIN = int(os.getenv("SLOT_STEP_MIN", "30"))
BOOKING_DAYS_AHEAD = int(os.getenv("BOOKING_DAYS_AHEAD", "7"))
CANCEL_DEADLINE_HOURS = int(os.getenv("CANCEL_DEADLINE_HOURS", "3"))

# Антифлуд: минимальная пауза между действиями одного пользователя
THROTTLE_SECONDS = float(os.getenv("THROTTLE_SECONDS", "0.7"))
