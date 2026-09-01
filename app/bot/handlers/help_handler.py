from aiogram import Router, types
from aiogram.filters import Command
import structlog

logger = structlog.get_logger()
router = Router()


def help_text() -> str:
    return (
        "📖 *Справка JoyBot*\n\n"
        "Основное меню — нажмите /start или кнопку «Меню» рядом с полем ввода.\n\n"
        "• /next — получить посты (по вашим тегам)\n"
        "• /settings — настройки\n"
        "• /stop — выключить автоотправку\n"
        "• /delete_my_data — запросить удаление данных\n"
        "• /restore — восстановить аккаунт\n\n"
        "Все действия (теги, расписание, лимиты) доступны через кнопки меню — команды вводить не обязательно."
    )


@router.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(help_text(), parse_mode="Markdown")