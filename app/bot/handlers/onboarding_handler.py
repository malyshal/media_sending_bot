import asyncio

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from app.bot.states import OnboardingStates, ChatSettingsStates
from app.bot.menu import build_home_text, build_home_keyboard
from app.bot.console import (render_callback, render_message, prompt_input, delete_user_message,
                            reset_state_keep_console, send_ephemeral)
from app.db.session import async_session
from app.db.repositories.user_repository import UserRepository
from app.db.repositories.chat_repository import ChatRepository
from app.db.repositories.post_repository import PostRepository
from app.db.models.post import Post
from app.services.post_service import PostService
from app.queue.api_queue import APIQueue
import structlog

logger = structlog.get_logger()

router = Router()


@router.message(F.text.startswith("/start"))
async def cmd_start(message: Message, state: FSMContext, bot: Bot):
    # Reset any lingering FSM state: /start is the universal "home" action.
    # Keeps the console reference so the existing screen gets edited, not duplicated.
    await reset_state_keep_console(state)

    async with async_session() as session:
        user_repo = UserRepository(session)
        chat_repo = ChatRepository(session)

        user = await user_repo.get_user(message.from_user.id)
        config = await chat_repo.get_config(message.chat.id)

        if user.is_frozen:
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔄 Восстановить", callback_data="restore_account")
            ]])
            await render_message(
                bot, message, state,
                "Ваш аккаунт заморожен (запрошено удаление данных).\n"
                "Вы можете восстановить его в течение 30 дней.",
                kb,
                parse_mode=None,
            )
            return

        if not config.include_tags:
            # Onboarding flow: ask for the first tag
            text = (
                "👋 Привет!\n\n"
                "Я JoyBot — бот для поиска и отправки постов с JoyReactor прямо в Telegram.\n\n"
                "Я умею:\n"
                "• находить посты по тегам;\n"
                "• исключать нежелательные темы;\n"
                "• отправлять посты вручную или по расписанию.\n\n"
                "Давай настроим тебя за пару шагов.\n\n"
                "🏷 Сначала добавь первый тег, который хочешь видеть в своей ленте."
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔎 Найти тег", callback_data="find_tag")]
            ])
            await render_message(bot, message, state, text, kb)
            await state.set_state(OnboardingStates.waiting_for_first_tag)
        else:
            await render_message(bot, message, state, build_home_text(config), build_home_keyboard())


@router.callback_query(F.data == "restore_account")
async def cb_restore_account(callback: CallbackQuery):
    async with async_session() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_user(callback.from_user.id)
        if user.is_frozen:
            await user_repo.cancel_deletion(callback.from_user.id)
            await callback.answer("Аккаунт восстановлен ✅", show_alert=True)
        else:
            await callback.answer("Аккаунт не заморожен")
    # Return to the home menu (settings router also handles this callback when
    # its console exists; aiogram dispatches the first matching handler)
    chat_id = callback.message.chat.id
    async with async_session() as session:
        config = await ChatRepository(session).get_config(chat_id)
    if config.include_tags:
        await callback.message.edit_text(build_home_text(config), reply_markup=build_home_keyboard())


@router.callback_query(F.data == "home")
async def cb_home(callback: CallbackQuery, state: FSMContext):
    await reset_state_keep_console(state)
    chat_id = callback.message.chat.id
    async with async_session() as session:
        config = await ChatRepository(session).get_config(chat_id)
    await render_callback(callback, state, build_home_text(config), build_home_keyboard())


@router.callback_query(F.data == "home_next")
async def cb_home_next(callback: CallbackQuery, state: FSMContext, bot: Bot, api_queue: 'APIQueue', jr_client: 'JoyReactorClient'):
    await callback.answer("Ищу посты...")
    chat_id = callback.message.chat.id
    async with async_session() as session:
        config = await ChatRepository(session).get_config(chat_id)
        post_service = PostService(jr_client, api_queue, PostRepository(session))
        from app.services.delivery_service import DeliveryService
        from app.services.media_manager import MediaManager
        delivery = DeliveryService(bot, post_service, MediaManager())

        sent_count = await delivery.send_batch_posts(
            chat_id=chat_id,
            include_tags=config.include_tags,
            exclude_tags=config.exclude_tags,
            max_posts=config.next_max_posts,
            ignore_history=False,
            show_links=config.show_post_links,
        )
        # Store tag lists of recent posts so index-based tag buttons resolve
        from app.bot.post_tag_keyboard import short_post_id as _spid
        from sqlalchemy import select as _sel
        from app.db.models.history import PostHistory as _PH
        q = _sel(Post).outerjoin(_PH, _PH.post_id == Post.id).where(
            _PH.post_id.is_(None)).order_by(Post.created_at.desc()).limit(5)
        recent = (await session.execute(q)).scalars().all()
        for p in recent:
            tags = [t for t in (p.tags or []) if t]
            if tags:
                await state.update_data(**{f"post_tags:{_spid(p.id)}": tags})
    if sent_count == 0:
        await send_ephemeral(
            bot, chat_id, state,
            "Не нашлось новых постов по вашим фильтрам 😢 Попробуйте позже или измените теги.",
        )


@router.callback_query(F.data == "home_tags")
async def cb_home_tags(callback: CallbackQuery, state: FSMContext):
    from app.bot.handlers.settings_handler import open_tag_menu
    await open_tag_menu(callback, state)


@router.callback_query(F.data == "home_settings")
async def cb_home_settings(callback: CallbackQuery, state: FSMContext):
    from app.bot.handlers.settings_handler import open_settings
    await open_settings(callback, state)


@router.callback_query(F.data == "home_help")
async def cb_home_help(callback: CallbackQuery, state: FSMContext):
    from app.bot.handlers.help_handler import help_text
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ В меню", callback_data="home")
    kb.adjust(1)
    await render_callback(callback, state, help_text(), kb.as_markup())


@router.callback_query(F.data == "find_tag")
async def find_tag_start(callback: CallbackQuery, state: FSMContext):
    await prompt_input(callback, state, "Введите название тега для поиска:")
    await state.set_state(OnboardingStates.waiting_for_first_tag)


@router.message(OnboardingStates.waiting_for_first_tag)
async def process_tag_search(message: Message, state: FSMContext, bot: Bot, api_queue: 'APIQueue', jr_client: 'JoyReactorClient'):
    query = (message.text or "").strip()
    # TS #56: local cache search first, API fallback
    from app.db.repositories.post_repository import PostRepository
    async with async_session() as session:
        local = await PostRepository(session).search_local_tags(query, limit=10)
    if local:
        kb_list = [[InlineKeyboardButton(text=t, callback_data=f"tag_select:{t}")] for t in local]
        await render_message(
            bot, message, state,
            f"Найдено в кэше по запросу «{query}»:",
            InlineKeyboardMarkup(inline_keyboard=kb_list),
            parse_mode=None,
        )
        await state.set_state(OnboardingStates.selecting_tag)
        return
    try:
        tags = await api_queue.enqueue(jr_client.search_tags, query)
        if not tags:
            await send_ephemeral(
                bot, message.chat.id, state,
                "Ничего не найдено. Попробуйте другой запрос.",
            )
            return

        kb_list = []
        for tag in tags[:10]:
            kb_list.append([InlineKeyboardButton(text=tag.name, callback_data=f"tag_select:{tag.name}")])

        await render_message(
            bot, message, state,
            f"Найдены следующие теги по запросу «{query}»:",
            InlineKeyboardMarkup(inline_keyboard=kb_list),
            parse_mode=None,
        )
        await state.set_state(OnboardingStates.selecting_tag)
    except Exception as e:
        logger.error("tag_search_error", error=str(e))
        await send_ephemeral(
            bot, message.chat.id, state,
            "Произошла ошибка при поиске тегов. Попробуйте позже.",
        )


@router.callback_query(F.data == "get_first_post")
async def get_first_post_handler(callback: CallbackQuery, state: FSMContext, bot: Bot, api_queue: 'APIQueue', jr_client: 'JoyReactorClient'):
    await callback.answer("Ищу подходящий пост...")

    async with async_session() as session:
        config = await ChatRepository(session).get_config(callback.message.chat.id)

        from app.services.delivery_service import DeliveryService
        from app.services.media_manager import MediaManager

        post_service = PostService(jr_client, api_queue, PostRepository(session))
        media_manager = MediaManager()
        delivery = DeliveryService(bot, post_service, media_manager)

        sent_count = await delivery.send_batch_posts(
            chat_id=callback.message.chat.id,
            include_tags=config.include_tags,
            exclude_tags=config.exclude_tags,
            max_posts=1,
            ignore_history=False,
            show_links=config.show_post_links,
        )
        # Store tag lists of recent posts so index-based tag buttons resolve
        from app.bot.post_tag_keyboard import short_post_id as _spid
        from sqlalchemy import select as _sel
        from app.db.models.history import PostHistory as _PH
        q = _sel(Post).outerjoin(_PH, _PH.post_id == Post.id).where(
            _PH.post_id.is_(None)).order_by(Post.created_at.desc()).limit(5)
        recent = (await session.execute(q)).scalars().all()
        for p in recent:
            tags = [t for t in (p.tags or []) if t]
            if tags:
                await state.update_data(**{f"post_tags:{_spid(p.id)}": tags})

        if sent_count == 0:
            await send_ephemeral(
                bot, callback.message.chat.id, state,
                "К сожалению, не удалось найти подходящий пост прямо сейчас. Попробуйте позже!",
            )


@router.callback_query(F.data == "change_tags")
async def cb_change_tags(callback: CallbackQuery, state: FSMContext):
    # Open the tag management screen (search + current lists)
    from app.bot.handlers.settings_handler import open_tag_menu
    await open_tag_menu(callback, state)


# ---------------- per-post tag buttons ----------------

def _full_post_id(post_num: str) -> str:
    """Restore the global post id from its numeric part."""
    import base64 as _b64
    if post_num.isdigit():
        return _b64.b64encode(f"Post:{post_num}".encode()).decode()
    return post_num


async def _load_post_and_config(chat_id: int, post_num: str):
    """Post from cache by its global id + chat config."""
    from app.db.session import async_session
    from app.db.models.post import Post
    from app.db.repositories.chat_repository import ChatRepository
    async with async_session() as session:
        post_id = _full_post_id(post_num)
        post = await session.get(Post, post_id)
        config = await ChatRepository(session).get_config(chat_id)
    return post, config


async def _store_post_tags(state: FSMContext, post_num: str, tags: list):
    """Keep the post's tag list in FSM so index-based callbacks can resolve."""
    await state.update_data(**{f"post_tags:{post_num}": tags})


async def _resolve_tag(callback: CallbackQuery, state: FSMContext, post_num: str,
                       payload: str) -> str | None:
    """Resolve a tag from callback payload: either the tag text itself or an
    index into the FSM-stored list."""
    if not payload.isdigit():
        return payload
    data = await state.get_data()
    tags = data.get(f"post_tags:{post_num}") or []
    try:
        return tags[int(payload)]
    except (IndexError, TypeError):
        return None


async def _apply_tag_op(callback: CallbackQuery, state: FSMContext, op: str,
                        chat_id: int, post_num: str, payload: str):
    tag = await _resolve_tag(callback, state, post_num, payload)
    if not tag:
        await callback.answer("Не удалось определить тег", show_alert=True)
        return None, None
    async with async_session() as session:
        chat_repo = ChatRepository(session)
        config = await chat_repo.get_config(chat_id)
        if op == "add":
            new_inc = list(set(config.include_tags + [tag]))
            new_exc = [t for t in config.exclude_tags if t != tag]
            await chat_repo.update_tags(chat_id, new_inc, new_exc)
        else:
            new_inc = [t for t in config.include_tags if t != tag]
            new_exc = [t for t in config.exclude_tags if t != tag]
            await chat_repo.update_tags(chat_id, new_inc, new_exc)
    return config, tag


async def _refresh_kb(callback: CallbackQuery, state: FSMContext, chat_id: int, post_num: str, config):
    """Rebuild the reply markup from the cache and store tags in FSM."""
    from app.bot.post_tag_keyboard import build_post_tags_keyboard, short_post_id
    async with async_session() as session:
        config = await ChatRepository(session).get_config(chat_id)
        post = await session.get(Post, _full_post_id(post_num))
    if not post:
        return
    kb = build_post_tags_keyboard(chat_id, post, config.include_tags, config.exclude_tags)
    tags = [t for t in (post.tags or []) if t]
    await state.update_data(**{f"post_tags:{short_post_id(post.id)}": tags})
    try:
        await callback.message.edit_reply_markup(reply_markup=kb)
    except Exception:
        pass


@router.callback_query(F.data.startswith("ptag_add:"))
async def cb_ptag_add(callback: CallbackQuery, state: FSMContext):
    _, chat_id_s, post_num, payload = callback.data.split(":", 3)
    chat_id = int(chat_id_s)
    config, tag = await _apply_tag_op(callback, state, "add", chat_id, post_num, payload)
    if tag is None:
        return
    await callback.answer(f"«{tag}» добавлен в include ✅")
    _refresh_kb(callback, state, chat_id, post_num)


@router.callback_query(F.data.startswith("ptag_rem:"))
async def cb_ptag_rem(callback: CallbackQuery, state: FSMContext):
    _, chat_id_s, post_num, payload = callback.data.split(":", 3)
    chat_id = int(chat_id_s)
    config, tag = await _apply_tag_op(callback, state, "rem", chat_id, post_num, payload)
    if tag is None:
        return
    await callback.answer(f"«{tag}» удалён ✅")
    _refresh_kb(callback, state, chat_id, post_num)


@router.callback_query(F.data.startswith("ptagi_add:"))
async def cb_ptagi_add(callback: CallbackQuery, state: FSMContext):
    _, chat_id_s, post_num, idx = callback.data.split(":", 3)
    chat_id = int(chat_id_s)
    tag = await _resolve_tag(callback, state, post_num, idx)
    if not tag:
        await callback.answer("Тег не найден", show_alert=True)
        return
    config, _ = await _apply_tag_op(callback, state, "add", chat_id, post_num, tag)
    await callback.answer(f"«{tag}» добавлен в include ✅")
    _refresh_kb(callback, state, chat_id, post_num)


@router.callback_query(F.data.startswith("ptagi_rem:"))
async def cb_ptagi_rem(callback: CallbackQuery, state: FSMContext):
    _, chat_id_s, post_num, idx = callback.data.split(":", 3)
    chat_id = int(chat_id_s)
    tag = await _resolve_tag(callback, state, post_num, idx)
    if not tag:
        await callback.answer("Тег не найден", show_alert=True)
        return
    config, _ = await _apply_tag_op(callback, state, "rem", chat_id, post_num, tag)
    await callback.answer(f"«{tag}» удалён ✅")
    _refresh_kb(callback, state, chat_id, post_num)


@router.callback_query(F.data.startswith("ptag_all:"))
async def cb_ptag_all(callback: CallbackQuery, state: FSMContext):
    _, chat_id_s, post_num = callback.data.split(":", 2)
    chat_id = int(chat_id_s)
    from app.bot.post_tag_keyboard import full_post_tags_keyboard
    post, config = await _load_post_and_config(chat_id, post_num)
    if not post:
        await callback.answer("Пост уже не в кэше", show_alert=True)
        return
    tags = [t for t in (post.tags or []) if t]
    await state.update_data(**{f"post_tags:{post_num}": tags})
    kb = full_post_tags_keyboard(chat_id, post, config.include_tags, config.exclude_tags)
    try:
        await callback.message.edit_reply_markup(reply_markup=kb)
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "go_to_settings")
async def cb_go_to_settings(callback: CallbackQuery, state: FSMContext):
    from app.bot.handlers.settings_handler import open_settings
    await open_settings(callback, state)


@router.callback_query(F.data == "set_schedule")
async def cb_set_schedule_entry(callback: CallbackQuery, state: FSMContext):
    from app.bot.handlers.settings_handler import open_schedule_menu
    await open_schedule_menu(callback, state)