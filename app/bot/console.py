"""Single-console UI helper.

All screens render into ONE persistent bot message per chat ("the console").
The console message id is stored in FSM data and survives state resets
(reset_state_keep_console). User command/input messages are deleted from
history, so the chat only ever contains: the console + delivered posts.
"""
import structlog
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup

logger = structlog.get_logger()

CONSOLE_KEY = "console_message_id"


async def delete_user_message(message: Message) -> None:
    """Remove the user's command/query message from history."""
    try:
        await message.delete()
    except Exception:
        pass  # already deleted or too old


async def reset_state_keep_console(state: FSMContext) -> None:
    """Drop FSM state/data but keep the console message reference.

    Handlers must use this instead of state.clear(), otherwise the console
    id is lost and the old screen message can no longer be edited/removed.
    """
    data = await state.get_data()
    console_id = data.get(CONSOLE_KEY)
    await state.set_data({})
    if console_id is not None:
        await state.update_data(**{CONSOLE_KEY: console_id})


async def _delete_by_id(bot: Bot, chat_id: int, message_id: int | None) -> None:
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def _edit(bot: Bot, chat_id: int, message_id: int | None, text: str,
                keyboard: InlineKeyboardMarkup | None, parse_mode: str | None):
    if not message_id:
        return None
    try:
        return await bot.edit_message_text(
            text=text, chat_id=chat_id, message_id=message_id,
            parse_mode=parse_mode, reply_markup=keyboard,
        )
    except Exception:
        return None  # not modified / too old / not found


async def _send(bot: Bot, chat_id: int, state: FSMContext, text: str,
                keyboard: InlineKeyboardMarkup | None, parse_mode: str | None) -> Message:
    sent = await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode, reply_markup=keyboard)
    await state.update_data(**{CONSOLE_KEY: sent.message_id})
    return sent


async def render_callback(callback: CallbackQuery, state: FSMContext, text: str,
                          keyboard: InlineKeyboardMarkup, parse_mode: str | None = "Markdown") -> Message:
    """Callback path: edit the console in place; recreate only if it's lost."""
    await callback.answer()
    chat_id = callback.message.chat.id
    data = await state.get_data()
    console_id = data.get(CONSOLE_KEY)

    if console_id:
        # Buttons live on the console itself, so the cheap path is edit_text
        try:
            return await callback.message.edit_text(
                text, parse_mode=parse_mode, reply_markup=keyboard
            )
        except Exception:
            msg = await _edit(callback.bot, chat_id, console_id, text, keyboard, parse_mode)
            if msg:
                return msg
        await _delete_by_id(callback.bot, chat_id, console_id)

    return await _send(callback.bot, chat_id, state, text, keyboard, parse_mode)


async def render_message(bot: Bot, message: Message, state: FSMContext, text: str,
                         keyboard: InlineKeyboardMarkup | None, parse_mode: str | None = "Markdown") -> Message:
    """Message-triggered render (command or text input):
    consume the user's message, then edit the existing console — never spawn a new one
    unless the old console is unrecoverable."""
    await delete_user_message(message)
    chat_id = message.chat.id
    data = await state.get_data()
    console_id = data.get(CONSOLE_KEY)

    if console_id:
        msg = await _edit(bot, chat_id, console_id, text, keyboard, parse_mode)
        if msg:
            return msg
        await _delete_by_id(bot, chat_id, console_id)

    return await _send(bot, chat_id, state, text, keyboard, parse_mode)


async def prompt_input(callback: CallbackQuery, state: FSMContext, prompt: str,
                       parse_mode: str | None = None) -> Message:
    """Turn the console itself into the input prompt (no extra messages).

    The console stays the same message; after the user replies, the input
    handler renders the result into the same console via render_message.
    """
    return await render_callback(callback, state, prompt, None, parse_mode=parse_mode)