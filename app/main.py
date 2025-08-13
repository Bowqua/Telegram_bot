import asyncio
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties
from app.config import settings
from app.db.bootstrap import init_db_and_load_cache
from app.handlers.callbacks import router as cb_router

bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
dp.include_router(cb_router)

USER_UI_MESSAGE = {}

@dp.message(CommandStart())
async def start(message: Message):
    text = "👋 Добро пожаловать! Это черновик приветствия.\n\nВыберите действие ниже."
    kb = {
        "inline_keyboard": [[
            {"text": "Выбор ассортиментов", "callback_data": "catalog1|open|"},
        ], [
            {"text": "Связь с менеджером", "callback_data": "contacts|open|"}
        ]]
    }
    msg = await message.answer(text, reply_markup=kb)
    USER_UI_MESSAGE[message.from_user.id] = msg.message_id


async def main():
    await init_db_and_load_cache()
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
