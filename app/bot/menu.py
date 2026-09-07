from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.db.models.chat import ChatConfig


def md_escape(text: str) -> str:
    """Escape legacy-Markdown special chars so user data (tag names) can't
    break Telegram entities."""
    for ch in ("\\", "*", "_", "`", "["):
        text = text.replace(ch, "\\" + ch)
    return text


def build_home_text(config: ChatConfig) -> str:
    inc = ", ".join(md_escape(t) for t in config.include_tags) if config.include_tags else "все"
    exc = ", ".join(md_escape(t) for t in config.exclude_tags) if config.exclude_tags else "нет"

    schedule = config.schedule or ""
    if config.auto_send and schedule:
        schedule_line = f"🕒 Автоотправка: ежедневно в {schedule.replace('*', '')} ({config.timezone})"
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
    kb.button(text="🔗 Пост по ссылке", callback_data="post_by_link")
    kb.button(text="🏷 Управление тегами", callback_data="home_tags")
    kb.button(text="⚙️ Настройки", callback_data="home_settings")
    kb.button(text="❓ Справка", callback_data="home_help")
    kb.adjust(1)
    return kb.as_markup()


def home_back_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(text="⬅️ В меню", callback_data="home")