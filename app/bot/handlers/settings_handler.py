from aiogram import Router, types, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
import structlog

from app.db.session import async_session
from app.db.repositories.chat_repository import ChatRepository
from app.db.repositories.user_repository import UserRepository
from app.db.repositories.post_repository import PostRepository
from app.queue.api_queue import APIQueue
from app.joyreactor.client import JoyReactorClient
from app.bot.states import ChatSettingsStates
from app.bot.menu import home_back_button
from app.bot.console import render_callback, render_message, prompt_input, delete_user_message, reset_state_keep_console

logger = structlog.get_logger()
router = Router()

TIMEZONE_PRESETS = [
    "Europe/Minsk",
    "Europe/Moscow",
    "Europe/Kyiv",
    "Europe/Berlin",
    "UTC",
]


def _tz_button_label(tz: str, current: str) -> str:
    marker = "✅ " if tz == current else ""
    return f"{marker}{tz}"


@router.message(Command("settings"))
async def cmd_settings(message: types.Message, state: FSMContext, bot: Bot):
    await reset_state_keep_console(state)
    async with async_session() as session:
        chat_repo = ChatRepository(session)
        config = await chat_repo.get_config(message.chat.id)
        await render_message(bot, message, state, build_settings_text(config), build_settings_keyboard().as_markup())


async def open_settings(callback: types.CallbackQuery, state: FSMContext):
    """Renders the settings screen into the console."""
    await reset_state_keep_console(state)
    chat_id = callback.message.chat.id
    async with async_session() as session:
        config = await ChatRepository(session).get_config(chat_id)
    await render_callback(callback, state, build_settings_text(config), build_settings_keyboard().as_markup())


def build_settings_text(config) -> str:
    if config.auto_send and config.schedule:
        schedule_line = f"🕒 Расписание: ежедневно в {config.schedule} ({config.timezone}) — вкл"
    elif config.schedule:
        schedule_line = f"🕒 Расписание: {config.schedule} ({config.timezone}) — выкл"
    else:
        schedule_line = f"🕒 Расписание: не задано ({config.timezone}) — выкл"

    return (
        f"⚙️ *Настройки JoyBot*\n\n"
        f"{schedule_line}\n"
        f"📦 Лимит (Регламент): {config.schedule_max_posts} постов\n"
        f"📩 Лимит (/next): {config.next_max_posts} постов\n"
        f"📥 Include: {', '.join(config.include_tags) if config.include_tags else 'все'}\n"
        f"🚫 Exclude: {', '.join(config.exclude_tags) if config.exclude_tags else 'нет'}"
    )


def build_settings_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔍 Теги: поиск и удаление", callback_data="open_tag_menu")
    kb.button(text="⏰ Расписание (время / вкл-выкл)", callback_data="open_schedule")
    kb.button(text="🌍 Часовой пояс", callback_data="set_timezone")
    kb.button(text="📦 Лимит (Регламент)", callback_data="set_schedule_max_posts")
    kb.button(text="📩 Лимит (/next)", callback_data="set_next_max_posts")
    kb.button(text="🗑 Удалить мои данные", callback_data="delete_my_data")
    kb.button(text="⬅️ В меню", callback_data="home")
    kb.adjust(1)
    return kb


# ---------------------------------------------------------------- tag menu

def build_tag_menu_text(config) -> str:
    inc = "\n".join(f"  • {t}" for t in config.include_tags) or "  (пусто)"
    exc = "\n".join(f"  • {t}" for t in config.exclude_tags) or "  (пусто)"
    return (
        f"🏷 *Управление тегами*\n\n"
        f"📥 Include:\n{inc}\n\n"
        f"🚫 Exclude:\n{exc}"
    )


def build_tag_menu_keyboard(config) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for t in (config.include_tags or [])[:10]:
        kb.button(text=f"➖ {t}", callback_data=f"tag_remove:{t}")
    for t in (config.exclude_tags or [])[:10]:
        kb.button(text=f"➖ {t} (исключ.)", callback_data=f"tag_unexclude:{t}")
    kb.button(text="🔍 Добавить тег (поиск)", callback_data="tag_start_search")
    kb.adjust(2, 2, 2, 2, 2, 1)
    kb.row(home_back_button())
    return kb


async def open_tag_menu(callback: types.CallbackQuery, state: FSMContext):
    await reset_state_keep_console(state)
    chat_id = callback.message.chat.id
    async with async_session() as session:
        config = await ChatRepository(session).get_config(chat_id)
    await render_callback(
        callback, state,
        build_tag_menu_text(config),
        build_tag_menu_keyboard(config).as_markup(),
    )


@router.callback_query(F.data == "open_tag_menu")
async def cb_open_tag_menu(callback: types.CallbackQuery, state: FSMContext):
    await open_tag_menu(callback, state)


@router.callback_query(F.data.startswith("tag_remove:"))
async def cb_tag_remove(callback: types.CallbackQuery, state: FSMContext):
    tag = callback.data.split(":", 1)[1]
    chat_id = callback.message.chat.id
    async with async_session() as session:
        chat_repo = ChatRepository(session)
        config = await chat_repo.get_config(chat_id)
        new_inc = [t for t in config.include_tags if t != tag]
        await chat_repo.update_tags(chat_id, new_inc, config.exclude_tags)
        config = await chat_repo.get_config(chat_id)
    await callback.answer(f"«{tag}» удалён из include")
    await render_callback(
        callback, state,
        build_tag_menu_text(config),
        build_tag_menu_keyboard(config).as_markup(),
    )


@router.callback_query(F.data.startswith("tag_unexclude:"))
async def cb_tag_unexclude(callback: types.CallbackQuery, state: FSMContext):
    tag = callback.data.split(":", 1)[1]
    chat_id = callback.message.chat.id
    async with async_session() as session:
        chat_repo = ChatRepository(session)
        config = await chat_repo.get_config(chat_id)
        new_exc = [t for t in config.exclude_tags if t != tag]
        await chat_repo.update_tags(chat_id, config.include_tags, new_exc)
        config = await chat_repo.get_config(chat_id)
    await callback.answer(f"«{tag}» удалён из exclude")
    await render_callback(
        callback, state,
        build_tag_menu_text(config),
        build_tag_menu_keyboard(config).as_markup(),
    )


@router.callback_query(F.data == "tag_start_search")
async def cb_tag_start_search(callback: types.CallbackQuery, state: FSMContext):
    await prompt_input(callback, state, "Введите название тега для поиска:")
    await state.set_state(ChatSettingsStates.waiting_for_tag_search)


async def _tag_autocomplete(message: types.Message, query: str, state: FSMContext, bot: Bot, api_queue: 'APIQueue', jr_client: 'JoyReactorClient'):
    """TS #56: search local cache first (case-insensitive, aggregated),
    fall back to JoyReactor API only when local results are empty."""
    async with async_session() as session:
        repo = PostRepository(session)
        local = await repo.search_local_tags(query, limit=6)

    if local:
        kb = InlineKeyboardBuilder()
        for tag in local:
            kb.button(text=tag, callback_data=f"tag_select:{tag}")
        kb.adjust(1)
        kb.row(home_back_button())
        await render_message(
            bot, message, state,
            f"Найдено в кэше по запросу «{query}»:",
            kb.as_markup(),
            parse_mode=None,
        )
        return

    # local cache empty -> JoyReactor API (through APIQueue, TS #22)
    try:
        tags = await api_queue.enqueue(jr_client.search_tags, query, priority=1)
        if not tags:
            await render_message(
                bot, message, state,
                f"По запросу «{query}» теги не найдены 😕 Попробуйте другой запрос.",
                None,
                parse_mode=None,
            )
            return

        kb = InlineKeyboardBuilder()
        for tag in tags[:6]:
            kb.button(text=tag.name, callback_data=f"tag_select:{tag.name}")
        kb.adjust(1)
        kb.row(home_back_button())

        await render_message(
            bot, message, state,
            f"Результаты API по запросу «{query}»:",
            kb.as_markup(),
            parse_mode=None,
        )
    except Exception as e:
        logger.error("tag_search_failed", error=str(e))
        await delete_user_message(message)
        await message.answer("Произошла ошибка при поиске тегов.")


@router.message(ChatSettingsStates.waiting_for_tag_search)
async def proc_tag_search_from_menu(message: types.Message, state: FSMContext, bot: Bot, api_queue: 'APIQueue', jr_client: 'JoyReactorClient'):
    query = (message.text or "").strip()
    if not query:
        await render_message(
            bot, message, state,
            "Введите текст запроса.",
            None,
            parse_mode=None,
        )
        return
    await _tag_autocomplete(message, query, state, bot, api_queue, jr_client)


@router.callback_query(F.data.startswith("tag_select:"))
async def cb_tag_selected(callback: types.CallbackQuery, state: FSMContext):
    tag_name = callback.data.split(":", 1)[1]
    await state.update_data(selected_tag=tag_name)
    await state.set_state(ChatSettingsStates.confirming_tag_action)

    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить в INCLUDE", callback_data="tag_add_inc")
    kb.button(text="🚫 Добавить в EXCLUDE", callback_data="tag_add_exc")
    kb.button(text="❌ Отмена", callback_data="tag_cancel")
    kb.adjust(1)

    await render_callback(
        callback, state,
        f"Что сделать с тегом *{tag_name}*?",
        kb.as_markup(),
    )


@router.callback_query(F.data == "tag_add_inc")
async def cb_tag_inc(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tag = data.get("selected_tag")
    chat_id = callback.message.chat.id

    async with async_session() as session:
        chat_repo = ChatRepository(session)
        config = await chat_repo.get_config(chat_id)

        new_inc = list(set(config.include_tags + [tag]))
        new_exc = [t for t in config.exclude_tags if t != tag]

        await chat_repo.update_tags(chat_id, new_inc, new_exc)
        config = await chat_repo.get_config(chat_id)

    await reset_state_keep_console(state)
    await callback.answer(f"Тег «{tag}» добавлен в include ✅")
    await render_callback(
        callback, state,
        build_tag_menu_text(config),
        build_tag_menu_keyboard(config).as_markup(),
    )


@router.callback_query(F.data == "tag_add_exc")
async def cb_tag_exc(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tag = data.get("selected_tag")
    chat_id = callback.message.chat.id

    async with async_session() as session:
        chat_repo = ChatRepository(session)
        config = await chat_repo.get_config(chat_id)

        new_exc = list(set(config.exclude_tags + [tag]))
        new_inc = [t for t in config.include_tags if t != tag]

        await chat_repo.update_tags(chat_id, new_inc, new_exc)
        config = await chat_repo.get_config(chat_id)

    await reset_state_keep_console(state)
    await callback.answer(f"Тег «{tag}» добавлен в exclude 🚫")
    await render_callback(
        callback, state,
        build_tag_menu_text(config),
        build_tag_menu_keyboard(config).as_markup(),
    )


@router.callback_query(F.data == "tag_cancel")
async def cb_tag_cancel(callback: types.CallbackQuery, state: FSMContext):
    await reset_state_keep_console(state)
    await callback.answer("Действие отменено")
    chat_id = callback.message.chat.id
    async with async_session() as session:
        config = await ChatRepository(session).get_config(chat_id)
    await render_callback(
        callback, state,
        build_tag_menu_text(config),
        build_tag_menu_keyboard(config).as_markup(),
    )


# ---------------------------------------------------------------- schedule menu

def build_schedule_text(config) -> str:
    if config.auto_send:
        state_line = "🟢 Вкл"
        if not config.schedule:
            state_line += " (время не задано — включите после установки)"
    else:
        state_line = "🔴 Выкл"
    time_line = config.schedule or "не задано"
    return (
        f"⏰ *Расписание автоотправки*\n\n"
        f"Состояние: {state_line}\n"
        f"Время: {time_line} ({config.timezone})\n"
        f"Лимит за раз: {config.schedule_max_posts} постов\n\n"
        f"Бот отправит посты автоматически каждый день в указанное время."
    )


def build_schedule_keyboard(config) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(
        text=("🔴 Выключить" if config.auto_send else "🟢 Включить"),
        callback_data="schedule_toggle",
    )
    kb.button(text="⏰ Изменить время", callback_data="set_schedule_time")
    kb.button(text="⬅️ В меню", callback_data="home")
    kb.adjust(1)
    return kb


async def open_schedule_menu(callback: types.CallbackQuery, state: FSMContext):
    await reset_state_keep_console(state)
    chat_id = callback.message.chat.id
    async with async_session() as session:
        config = await ChatRepository(session).get_config(chat_id)
    await render_callback(
        callback, state,
        build_schedule_text(config),
        build_schedule_keyboard(config).as_markup(),
    )


@router.callback_query(F.data == "open_schedule")
async def cb_open_schedule(callback: types.CallbackQuery, state: FSMContext):
    await open_schedule_menu(callback, state)


@router.callback_query(F.data == "schedule_toggle")
async def cb_schedule_toggle(callback: types.CallbackQuery, state: FSMContext):
    chat_id = callback.message.chat.id
    async with async_session() as session:
        chat_repo = ChatRepository(session)
        config = await chat_repo.get_config(chat_id)

        if not config.auto_send and not config.schedule:
            await callback.answer("Сначала задайте время отправки!", show_alert=True)
            return

        new_state = not config.auto_send
        config = await chat_repo.set_auto_send(chat_id, new_state)

    await callback.answer("Расписание включено 🟢" if new_state else "Расписание выключено 🔴")
    await render_callback(
        callback, state,
        build_schedule_text(config),
        build_schedule_keyboard(config).as_markup(),
    )


@router.callback_query(F.data == "set_schedule_time")
async def cb_set_schedule_time(callback: types.CallbackQuery, state: FSMContext):
    await prompt_input(callback, state, "Введите время в формате HH:MM (например, 10:00):")
    await state.set_state(ChatSettingsStates.setting_schedule)


@router.message(ChatSettingsStates.setting_schedule)
async def proc_set_schedule(message: types.Message, state: FSMContext, bot: Bot):
    import re
    time_pattern = r"^(?:[01]\d|2[0-3]):[0-5]\d$"
    if not message.text or not re.match(time_pattern, message.text.strip()):
        kb = InlineKeyboardBuilder()
        kb.button(text="❌ Отмена", callback_data="open_schedule")
        await render_message(
            bot, message, state,
            "Неверный формат! Введите время как HH:MM (например, 18:30), либо отмените.",
            kb.as_markup(),
            parse_mode=None,
        )
        return

    chat_id = message.chat.id
    async with async_session() as session:
        chat_repo = ChatRepository(session)
        config = await chat_repo.get_config(chat_id)
        await chat_repo.update_schedule(chat_id, message.text.strip(), config.timezone)
        config = await chat_repo.get_config(chat_id)

    await reset_state_keep_console(state)
    await render_message(
        bot, message, state,
        f"✅ Время установлено: {config.schedule} ({config.timezone})\n\n" + build_schedule_text(config),
        build_schedule_keyboard(config).as_markup(),
    )


# ---------------------------------------------------------------- timezone presets

@router.callback_query(F.data == "set_timezone")
async def cb_set_timezone(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    chat_id = callback.message.chat.id
    async with async_session() as session:
        config = await ChatRepository(session).get_config(chat_id)

    kb = InlineKeyboardBuilder()
    for tz in TIMEZONE_PRESETS:
        kb.button(text=_tz_button_label(tz, config.timezone), callback_data=f"tz_select:{tz}")
    kb.adjust(1)
    kb.row(home_back_button())
    await render_callback(
        callback, state,
        f"🌍 Текущий часовой пояс: *{config.timezone}*\nВыберите из списка:",
        kb.as_markup(),
    )


@router.callback_query(F.data.startswith("tz_select:"))
async def cb_tz_selected(callback: types.CallbackQuery, state: FSMContext):
    tz = callback.data.split(":", 1)[1]
    if tz not in TIMEZONE_PRESETS:
        await callback.answer("Недопустимый пояс", show_alert=True)
        return
    chat_id = callback.message.chat.id
    async with async_session() as session:
        chat_repo = ChatRepository(session)
        config = await chat_repo.get_config(chat_id)
        if config.schedule:
            await chat_repo.update_schedule(chat_id, config.schedule, tz)
        else:
            # no schedule yet: store timezone only
            await chat_repo.set_timezone(chat_id, tz)
        config = await chat_repo.get_config(chat_id)
    await callback.answer(f"Часовой пояс: {tz}")
    await render_callback(
        callback, state,
        build_settings_text(config),
        build_settings_keyboard().as_markup(),
    )


# ---------------------------------------------------------------- limits

@router.callback_query(F.data == "set_schedule_max_posts")
async def cb_set_schedule_max_posts(callback: types.CallbackQuery, state: FSMContext):
    await prompt_input(
        callback, state,
        "Введите лимит постов для регламентной отправки (число от 1 до 20):",
        parse_mode=None,
    )
    await state.set_state(ChatSettingsStates.setting_schedule_max_posts)


@router.message(ChatSettingsStates.setting_schedule_max_posts)
async def proc_set_schedule_max_posts(message: types.Message, state: FSMContext, bot: Bot):
    value = _parse_limit(message.text)
    if value is None:
        kb = InlineKeyboardBuilder()
        kb.button(text="❌ Отмена", callback_data="home_settings")
        await render_message(
            bot, message, state,
            "Неверное значение! Введите целое число от 1 до 20, либо отмените.",
            kb.as_markup(),
            parse_mode=None,
        )
        return

    chat_id = message.chat.id
    async with async_session() as session:
        chat_repo = ChatRepository(session)
        config = await chat_repo.set_schedule_max_posts(chat_id, value)

    await reset_state_keep_console(state)
    await render_message(
        bot, message, state,
        f"📦 Лимит (Регламент) установлен: {config.schedule_max_posts} постов\n\n" + build_settings_text(config),
        build_settings_keyboard().as_markup(),
    )


@router.callback_query(F.data == "set_next_max_posts")
async def cb_set_next_max_posts(callback: types.CallbackQuery, state: FSMContext):
    await prompt_input(
        callback, state,
        "Введите лимит постов для команды /next (число от 1 до 20):",
        parse_mode=None,
    )
    await state.set_state(ChatSettingsStates.setting_next_max_posts)


@router.message(ChatSettingsStates.setting_next_max_posts)
async def proc_set_next_max_posts(message: types.Message, state: FSMContext, bot: Bot):
    value = _parse_limit(message.text)
    if value is None:
        kb = InlineKeyboardBuilder()
        kb.button(text="❌ Отмена", callback_data="home_settings")
        await render_message(
            bot, message, state,
            "Неверное значение! Введите целое число от 1 до 20, либо отмените.",
            kb.as_markup(),
            parse_mode=None,
        )
        return

    chat_id = message.chat.id
    async with async_session() as session:
        chat_repo = ChatRepository(session)
        config = await chat_repo.set_next_max_posts(chat_id, value)

    await reset_state_keep_console(state)
    await render_message(
        bot, message, state,
        f"📩 Лимит (/next) установлен: {config.next_max_posts} постов\n\n" + build_settings_text(config),
        build_settings_keyboard().as_markup(),
    )


def _parse_limit(text: str | None) -> int | None:
    if not text:
        return None
    text = text.strip()
    if not text.isdigit():
        return None
    value = int(text)
    if value < 1 or value > 20:
        return None
    return value


# ---------------------------------------------------------------- /search_tags (keep command from TS #56)

@router.message(Command("search_tags"))
async def cmd_search_tags(message: types.Message, state: FSMContext, bot: Bot, api_queue: 'APIQueue', jr_client: 'JoyReactorClient'):
    if not message.text or len(message.text.split()) < 2:
        await delete_user_message(message)
        await message.answer("Используйте: `/search_tags <запрос>` или добавьте теги через меню")
        return

    query = " ".join(message.text.split()[1:])
    await _tag_autocomplete(message, query, state, bot, api_queue, jr_client)


# ---------------------------------------------------------------- stop / delete data

@router.message(Command("stop"))
async def cmd_stop(message: types.Message, state: FSMContext, bot: Bot):
    chat_id = message.chat.id
    async with async_session() as session:
        chat_repo = ChatRepository(session)
        await chat_repo.set_auto_send(chat_id, False)
    await reset_state_keep_console(state)
    await render_message(
        bot, message, state,
        "🛑 Автоматическая рассылка выключена.",
        build_settings_keyboard().as_markup(),
        parse_mode=None,
    )


@router.message(Command("delete_my_data"))
async def cmd_delete_data(message: types.Message, state: FSMContext, bot: Bot):
    await delete_user_message(message)
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, удалить", callback_data="confirm_delete_yes")
    kb.button(text="❌ Отмена", callback_data="confirm_delete_no")

    await message.answer(
        "⚠️ *Внимание!*\n\nВы действительно хотите удалить все свои данные? "
        "Ваш аккаунт будет заморожен на 30 дней, после чего данные будут удалены окончательно.",
        parse_mode="Markdown",
        reply_markup=kb.as_markup()
    )
    await state.set_state(ChatSettingsStates.confirm_deletion)


@router.callback_query(F.data == "delete_my_data")
async def cb_delete_my_data(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(ChatSettingsStates.confirm_deletion)
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, удалить", callback_data="confirm_delete_yes")
    kb.button(text="❌ Отмена", callback_data="confirm_delete_no")
    await render_callback(
        callback, state,
        "⚠️ *Внимание!*\n\nВы действительно хотите удалить все свои данные? "
        "Ваш аккаунт будет заморожен на 30 дней, после чего данные будут удалены окончательно.",
        kb.as_markup(),
    )


@router.callback_query(F.data == "confirm_delete_yes")
async def cb_delete_yes(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id

    async with async_session() as session:
        user_repo = UserRepository(session)
        chat_repo = ChatRepository(session)

        await user_repo.request_deletion(user_id)
        await chat_repo.set_auto_send(chat_id, False)

    from aiogram.utils.keyboard import InlineKeyboardBuilder as _B
    kb = _B()
    kb.button(text="🔄 Восстановить", callback_data="restore_account")
    await callback.message.edit_text(
        "🗑 Данные заморожены. Вы можете отменить удаление в течение 30 дней.",
        reply_markup=kb.as_markup(),
    )
    await reset_state_keep_console(state)


@router.callback_query(F.data == "confirm_delete_no")
async def cb_delete_no(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Запрос на удаление отменен.")
    await reset_state_keep_console(state)


@router.callback_query(F.data == "restore_account")
async def cb_restore_account_settings(callback: CallbackQuery, state: FSMContext):
    # Handles the "restore" button when the settings console is the active screen
    # (onboarding router handles the same callback for the frozen-user flow first;
    # aiogram dispatches to the first matching router, so this one rarely fires).
    async with async_session() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_user(callback.from_user.id)
        if user.is_frozen:
            await user_repo.cancel_deletion(callback.from_user.id)
            await callback.answer("Аккаунт восстановлен ✅", show_alert=True)
        else:
            await callback.answer("Аккаунт не заморожен")


@router.message(Command("restore"))
async def cmd_restore(message: types.Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id

    async with async_session() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_user(user_id)

        if not user.is_frozen:
            await delete_user_message(message)
            await message.answer("Ваш аккаунт не находится в состоянии удаления.")
            return

        await user_repo.cancel_deletion(user_id)
        config = await ChatRepository(session).get_config(message.chat.id)

    await reset_state_keep_console(state)
    await render_message(
        bot, message, state,
        "✅ Удаление отменено! Ваши данные восстановлены.\n\n" + build_settings_text(config),
        build_settings_keyboard().as_markup(),
    )