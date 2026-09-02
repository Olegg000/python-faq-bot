import asyncio
import logging

from aiogram import Bot, Dispatcher

from bot import config, db, handlers
from bot.middlewares import setup_middlewares
from bot.scheduler import setup_scheduler
from bot.seed import seed_services


async def main() -> None:
    if not config.BOT_TOKEN:
        raise SystemExit("Задайте переменную окружения BOT_TOKEN (см. .env.example)")

    await db.init_models(db.get_engine())
    async with db.session() as s:
        added = await seed_services(s)
    if added:
        logging.info("Прайс пуст — добавлено услуг: %s", added)

    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()
    setup_middlewares(dp, db.get_sessionmaker())
    dp.include_router(handlers.router)

    scheduler = setup_scheduler(bot, db.get_sessionmaker())
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
