from aiogram import Router, types, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
import structlog
from app.bot.console import render_message, reset_state_keep_console

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
        "Все действия (теги, расписание, лимиты) доступны через кнопки меню — команды вводить не обязательно.\n\n"
        "ℹ️ *Важно про новые теги:* информация по свежим постам и тегам на JoyReactor "
        "обновляется с задержкой (индексация сайта может отставать на несколько часов). "
        "Если новый тег не находит постов — подождите немного и попробуйте снова."
    )


@router.message(Command("help"))
async def cmd_help(message: types.Message, state: FSMContext, bot: Bot):
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ В меню", callback_data="home")
    kb.adjust(1)
    await reset_state_keep_console(state)
    await render_message(bot, message, state, help_text(), kb.as_markup())