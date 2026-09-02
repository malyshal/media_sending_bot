"""Per-post tag keyboard: first N tags as toggle buttons + "show all" (TS UX)."""
import structlog
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logger = structlog.get_logger()

POST_TAGS_SHOWN = 3


def _tag_buttons(chat_id: int, post_id: str, tags: list, include: set, exclude: set) -> list:
    """One row per tag: ✅ include (tap to remove), 🚫 excluded (tap to un-exclude), ➕ otherwise."""
    rows = []
    for t in tags:
        if t in include:
            rows.append([InlineKeyboardButton(text=f"✅ {t}", callback_data=f"ptag_rem:{chat_id}:{post_id}:{t}")])
        elif t in exclude:
            rows.append([InlineKeyboardButton(text=f"🚫 {t}", callback_data=f"ptag_rem:{chat_id}:{post_id}:{t}")])
        else:
            rows.append([InlineKeyboardButton(text=f"➕ {t}", callback_data=f"ptag_add:{chat_id}:{post_id}:{t}")])
    return rows


def build_post_tags_keyboard(chat_id: int, post, include: list | None = None,
                             exclude: list | None = None, show_all: bool = False) -> InlineKeyboardMarkup | None:
    """Keyboard that reflects the chat's current include/exclude lists."""
    include = include or []
    exclude = exclude or []
    tags = [t for t in (getattr(post, "tags", None) or []) if t]
    if not tags:
        return None
    shown = tags if show_all else tags[:POST_TAGS_SHOWN]
    rows = _tag_buttons(chat_id, post.id, shown, set(include), set(exclude))
    if not show_all and len(tags) > POST_TAGS_SHOWN:
        rows.append([InlineKeyboardButton(
            text=f"👁 Показать все ({len(tags)})",
            callback_data=f"ptag_all:{chat_id}:{post.id}",
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)
