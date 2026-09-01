from aiogram import Bot

# Public commands shown in Telegram's command menu ("/" quick access)
USER_COMMANDS = [
    ("start", "Главное меню"),
    ("next", "Получить посты"),
    ("settings", "Настройки"),
    ("help", "Справка"),
    ("stop", "Отключить автоотправку"),
    ("delete_my_data", "Удалить мои данные"),
]


async def setup_bot_commands(bot) -> None:
    """Register the command menu so users see it when pressing '/'."""
    from aiogram.types import BotCommand
    await bot.set_my_commands(
        [BotCommand(command=cmd, description=desc) for cmd, desc in USER_COMMANDS]
    )