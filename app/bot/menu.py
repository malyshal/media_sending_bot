from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.db.models.chat import ChatConfig


def build_home_text(config: ChatConfig) -> str:
    inc = ", ".join(config.include_tags) if config.include_tags else "все"
    exc = ", ".join(config.exclude_tags) if config.exclude_tags else "нет"

    if config.auto_send and config.schedule:
        schedule_line = f"🕒 Автоотправка: ежедневно в {config.schedule} ({config.timezone})"
    elif config.auto_send:
        schedule_line = "🕒 Автоотправка: вкл (время не задано!)"
    else:
        schedule_line = "🕒 Автоотправка: выкл"

    return (
        "👋 *JoyBot*\n\n"
        f"📥 Показывать: {inc}\n"
        f"🚫 Исключать: {exc}\n"
        f"{schedule_line}\n\n"
        "Что делаем дальше?"
    )


def build_home_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="▶️ Следующий пост", callback_data="home_next")
    kb.button(text="🔍 Поиск тегов", callback_data="home_search_tags")
    kb.button(text="⏰ Расписание", callback_data="home_schedule")
    kb.button(text="⚙️ Настройки", callback_data="home_settings")
    kb.button(text="❓ Справка", callback_data="home_help")
    kb.adjust(1)
    return kb.as_markup()


def home_back_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(text="⬅️ В меню", callback_data="home")