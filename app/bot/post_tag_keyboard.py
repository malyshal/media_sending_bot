"""Per-post tag keyboard: first N tags as toggle buttons + "show all" (TS UX).

Telegram limits callback_data to 64 BYTES. Tag names (especially Cyrillic)
plus chat_id + post_id can overflow it, which makes Telegram reject the whole
message with BUTTON_DATA_INVALID. Solution: tags are referenced by INDEX into
a per-post tag list stored in FSM data (Redis-backed in production) whenever
the full payload would not fit.
"""
import base64 as _b64
import structlog
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logger = structlog.get_logger()

POST_TAGS_SHOWN = 3
CB_LIMIT = 64


def short_post_id(post_id: str) -> str:
    """Numeric part of a global post id: 'UG9zdDo2Mzc1Njkw' -> '6375690'."""
    try:
        if post_id.startswith("UG9zdDo"):
            return _b64.b64decode(post_id).decode().split(":", 1)[1]
        if ":" in post_id:
            return post_id.split(":", 1)[1]
    except Exception:
        pass
    return post_id


def build_post_tag_buttons(chat_id: int, post_id: str, tags: list,
                           include: set, exclude: set) -> list:
    """One row per tag; callback_data shortened to fit the Telegram limit.

    Returns (rows, tags_stored): tags_stored=True means the tag list is
    referenced by index and must be kept in FSM data under
    'post_tags:<post_num>'.
    """
    post_num = short_post_id(post_id)
    rows = []
    stored = False
    for i, t in enumerate(tags):
        if t in include:
            label, op = f"✅ {t}", "rem"
        elif t in exclude:
            label, op = f"🚫 {t}", "rem"
        else:
            label, op = f"➕ {t}", "add"
        full = f"ptag_{op}:{chat_id}:{post_num}:{t}"
        if len(full.encode("utf-8")) > CB_LIMIT:
            cb = f"ptagi_{op}:{chat_id}:{post_num}:{i}"
            stored = True
        else:
            cb = full
        rows.append([InlineKeyboardButton(text=label, callback_data=cb)])
    return rows, stored


def build_post_tags_keyboard(chat_id: int, post, include: list | None = None,
                             exclude: list | None = None, show_all: bool = False) -> InlineKeyboardMarkup | None:
    """Keyboard that reflects the chat's current include/exclude lists.

    When index-based callbacks are used (long tags), the caller must keep the
    full tag list in FSM data under 'post_tags:<post_num>' so handlers can
    resolve them.
    """
    include = include or []
    exclude = exclude or []
    tags = [t for t in (getattr(post, "tags", None) or []) if t]
    if not tags:
        return None
    shown = tags if show_all else tags[:POST_TAGS_SHOWN]
    post_num = short_post_id(post.id)
    rows, stored = build_post_tag_buttons(chat_id, post_num, shown, set(include), set(exclude))
    if stored:
        rows.insert(0, [InlineKeyboardButton(text="ⓘ", callback_data="noop")])
    if not show_all and len(tags) > POST_TAGS_SHOWN:
        rows.append([InlineKeyboardButton(
            text=f"👁 Показать все ({len(tags)})",
            callback_data=f"ptag_all:{chat_id}:{post_num}",
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def full_post_tags_keyboard(chat_id: int, post, include: list, exclude: list) -> InlineKeyboardMarkup | None:
    """Show-all variant: all tags referenced by index, stored in FSM."""
    tags = [t for t in (getattr(post, "tags", None) or []) if t]
    if not tags:
        return None
    post_num = short_post_id(post.id)
    rows, stored = build_post_tag_buttons(chat_id, post_num, tags, set(include), set(exclude))
    return InlineKeyboardMarkup(inline_keyboard=rows)
