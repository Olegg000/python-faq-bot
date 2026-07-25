import asyncio
import logging

from aiogram import Bot, Dispatcher

from bot import config, handlers


async def main() -> None:
    if not config.BOT_TOKEN:
        raise SystemExit("Задайте переменную окружения BOT_TOKEN (см. .env.example)")
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(handlers.router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
