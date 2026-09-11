import base64
import re

from aiogram import Router, F, types, Bot
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
import structlog
from app.services.delivery_service import DeliveryService
from app.db.session import async_session
from app.bot.states import ChatSettingsStates
from app.bot.menu import build_home_text, build_home_keyboard
from app.bot.console import delete_user_message, send_ephemeral, render_message, prompt_input, reset_state_keep_console
from app.core.metrics import metrics

logger = structlog.get_logger()
router = Router()

@router.message(Command("next"))
async def cmd_next(message: types.Message, state: FSMContext, bot: Bot, api_queue: 'APIQueue', jr_client: 'JoyReactorClient'):
    await delete_user_message(message)
    return await handle_next_request(message, state, bot, api_queue, jr_client)

async def handle_next_request(message: types.Message, state: FSMContext, bot: Bot, api_queue: 'APIQueue', jr_client: 'JoyReactorClient'):
    chat_id = message.chat.id
    logger.info("cmd_next_received", chat_id=chat_id)

    async with async_session() as session:
        from app.services.post_service import PostService
        from app.services.media_manager import MediaManager
        from app.db.repositories.post_repository import PostRepository
        from app.db.repositories.chat_repository import ChatRepository

        # Initialize components
        repo = PostRepository(session)
        chat_repo = ChatRepository(session)
        post_service = PostService(jr_client, api_queue, repo)
        media_manager = MediaManager()
        delivery_service = DeliveryService(bot, post_service, media_manager)

        # Load actual chat settings
        config = await chat_repo.get_config(chat_id)

        # Use batch sending for /next to respect next_max_posts.
        # History IS respected: previously sent posts are never repeated.
        sent_count = await delivery_service.send_batch_posts(
            chat_id=chat_id,
            include_tags=config.include_tags,
            exclude_tags=config.exclude_tags,
            max_posts=config.next_max_posts,
            ignore_history=False,
            show_links=config.show_post_links,
        )

        if sent_count == 0:
            await send_ephemeral(
                bot, chat_id, state,
                "Не нашлось новых постов по вашим фильтрам 😢 Попробуйте позже или измените теги.",
            )


# ---------------------------------------------------------------- post by link

_LINK_RE = re.compile(r"post/(\d+)")


@router.callback_query(F.data == "post_by_link")
async def cb_post_by_link(callback: types.CallbackQuery, state: FSMContext):
    await prompt_input(
        callback, state,
        "🔗 Отправьте ссылку на пост JoyReactor\n"
        "(например, https://joyreactor.cc/post/123456):",
        parse_mode=None,
    )
    await state.set_state(ChatSettingsStates.waiting_for_post_link)


@router.message(ChatSettingsStates.waiting_for_post_link)
async def proc_post_by_link(message: types.Message, state: FSMContext, bot: Bot, api_queue: 'APIQueue', jr_client: 'JoyReactorClient'):
    chat_id = message.chat.id
    text = (message.text or "").strip()

    m = _LINK_RE.search(text)
    if not m:
        await render_message(
            bot, message, state,
            "Не похоже на ссылку на пост JoyReactor 😕\n"
            "Отправьте ссылку вида https://joyreactor.cc/post/123456",
            None,
            parse_mode=None,
        )
        # stay in the same state: let the user retry
        return

    post_id = base64.b64encode(f"Post:{m.group(1)}".encode()).decode()

    await render_message(bot, message, state, "⏳ Загружаю пост…", None, parse_mode=None)

    from app.services.post_service import PostService
    from app.services.media_manager import MediaManager
    from app.db.repositories.post_repository import PostRepository
    from app.db.repositories.chat_repository import ChatRepository
    from app.db.models.post import Post

    # 1. Fetch the post meta (through the APIQueue, TS #22)
    jr_post = await api_queue.enqueue(jr_client.fetch_post, post_id, priority=2)
    if not jr_post:
        await reset_state_keep_console(state)
        await render_message(
            bot, message, state,
            "Пост не найден 😢",
            None,
            parse_mode=None,
        )
        return

    # New JoyReactor posts (post >= ~5.0M era) carry slugged CDN paths that
    # the GraphQL API doesn't expose through PostAttributePicture — the
    # _media_from_attributes() helper returns (None, None) for them.
    # Fall back to scraping the public post page for the real media URLs.
    if not jr_post.media_url:
        resolved = await jr_client.resolve_media_via_post_page(post_id)
        if resolved:
            first_url, first_type = resolved[0]
            jr_post = jr_post.__class__(
                id=jr_post.id,
                text=jr_post.text,
                content=jr_post.content,
                tags=jr_post.tags,
                created_at=jr_post.created_at,
                media_url=first_url,
                media_type=first_type,
                media_urls=resolved,
                raw_data=jr_post.raw_data,
            )
            # Stash the full list so delivery_service can serve all media items
            # (not just the first one). _post_media_items() reads it back.
            if jr_post.raw_data is None:
                jr_post.raw_data = {}
            jr_post.raw_data["_resolved_media_urls"] = resolved

    # Text-only post (no media, only body text). Send as a plain Telegram
    # message with the source link appended (respecting show_post_links).
    # We don't cache text-only posts in the posts table: media_url is NOT NULL
    # there and these posts add no value to the /next cache.
    if not jr_post.media_url:
        from app.services.delivery_service import _make_caption, TELEGRAM_MESSAGE_LIMIT
        async with async_session() as session:
            chat_repo = ChatRepository(session)
            config = await chat_repo.get_config(chat_id)

        try:
            numeric = base64.b64decode(post_id).decode("utf-8", "replace").split(":", 1)[-1]
        except Exception:
            numeric = post_id
        link = f"https://joyreactor.cc/post/{numeric}" if config.show_post_links else ""
        # send_message allows 4096 chars (vs 1024 for captions).
        text = _make_caption(jr_post.text, link, limit=TELEGRAM_MESSAGE_LIMIT)
        if not text:
            await reset_state_keep_console(state)
            await render_message(
                bot, message, state,
                "Пост не найден или в нём нет ни медиа, ни текста 😢",
                None,
                parse_mode=None,
            )
            return
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                disable_web_page_preview=True,
            )
            metrics.inc("posts_sent")
        except Exception as e:
            logger.error("post_by_link_text_failed", chat_id=chat_id, error=str(e))
            await reset_state_keep_console(state)
            await render_message(
                bot, message, state,
                "Не удалось отправить пост 😢 Попробуйте позже.",
                None,
                parse_mode=None,
            )
            return
        await reset_state_keep_console(state)
        await render_message(
            bot, message, state,
            build_home_text(config),
            build_home_keyboard(),
        )
        return

    # 2. Cache it and deliver to the chat (media download itself goes
    # through the APIQueue inside DeliveryService).
    from datetime import datetime as _dt

    db_post = Post(
        id=jr_post.id,
        text=jr_post.text,
        media_url=jr_post.media_url,
        media_type=jr_post.media_type or "image",
        tags=jr_post.tags,
        created_at=jr_post.created_at,
        updated_at=_dt.utcnow(),
        raw_data=jr_post.raw_data,
    )

    try:
        async with async_session() as session:
            repo = PostRepository(session)
            await repo.save_post(db_post)
            chat_repo = ChatRepository(session)
            config = await chat_repo.get_config(chat_id)

        delivery_service = DeliveryService(
            bot, PostService(jr_client, api_queue, repo), MediaManager()
        )
        await delivery_service.send_next_post(
            chat_id,
            config.include_tags,
            config.exclude_tags,
            ignore_history=True,
            show_links=config.show_post_links,
            post=db_post,
        )
    except Exception as e:
        logger.error("post_by_link_failed", chat_id=chat_id, error=str(e))
        await reset_state_keep_console(state)
        await render_message(
            bot, message, state,
            "Не удалось отправить пост 😢 Попробуйте позже.",
            None,
            parse_mode=None,
        )
        return

    # 3. Back to the home screen
    await reset_state_keep_console(state)
    await render_message(bot, message, state, build_home_text(config), build_home_keyboard())
