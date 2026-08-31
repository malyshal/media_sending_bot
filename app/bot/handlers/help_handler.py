from aiogram import Router, types
from aiogram.filters import Command
import structlog

logger = structlog.get_logger()
router = Router()

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    text = (
        "📖 Справка по командам JoyBot:\n\n"
        "• /start — запуск и основные действия\n"
        "• /next — получить следующий пост\n"
        "• /search_tags — найти тег\n"
        "• /settings — настройки\n"
        "• /help — справка\n"
        "• /delete_my_data — запросить удаление данных\n"
        "• /restore — восстановить данные\n"
        "• /stats — статистика для администратора"
    )
    await message.answer(text)
