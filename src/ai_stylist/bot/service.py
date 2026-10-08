from aiogram import Bot, Dispatcher

from ai_stylist.bot.handlers import make_router


async def run_bot(settings, pipeline):
    settings.require("telegram_bot_token")
    async with Bot(token=settings.telegram_bot_token.get_secret_value()) as bot:
        dispatcher = Dispatcher()
        dispatcher.include_router(make_router(pipeline))
        await dispatcher.start_polling(bot)
