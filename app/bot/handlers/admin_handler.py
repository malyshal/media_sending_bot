from aiogram import Router, types, F, Bot
from aiogram.filters import Command
import structlog
from app.db.session import async_session
from app.db.repositories.user_repository import UserRepository
from app.db.repositories.chat_repository import ChatRepository
from app.db.repositories.post_repository import PostRepository
from app.db.models.chat import ChatConfig
from app.db.models.user import UserAccount
from app.db.models.post import Post
from sqlalchemy import select, func
from app.core.metrics import metrics

logger = structlog.get_logger()
router = Router()


async def admin_filter(message: types.Message):
    async with async_session() as session:
        user_repo = UserRepository(session)
        return await user_repo.is_admin(message.from_user.id)


@router.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if not await admin_filter(message):
        return

    async with async_session() as session:
        chat_repo = ChatRepository(session)
        post_repo = PostRepository(session)
        user_repo = UserRepository(session)

        # Basic Stats (state snapshot)
        chats_count = await session.execute(select(func.count()).select_from(ChatConfig))
        users_count = await session.execute(select(func.count()).select_from(UserAccount))
        posts_count = await session.execute(select(func.count()).select_from(Post))

        text = (
            f"📊 *Системная статистика*\n\n"
            f"👥 Пользователей: {users_count.scalar()}\n"
            f"💬 Активных чатов: {chats_count.scalar()}\n"
            f"📦 Постов в кэше: {posts_count.scalar()}\n\n"
            f"{metrics.render()}"
        )
        await message.answer(text, parse_mode="Markdown")


@router.message(Command("force_send"))
async def cmd_force_send(message: types.Message, bot: Bot, api_queue: 'APIQueue', jr_client: 'JoyReactorClient'):
    if not await admin_filter(message):
        return

    from app.services.delivery_service import DeliveryService
    from app.services.post_service import PostService
    from app.services.media_manager import MediaManager

    # /force_send [<chat_id>] — target chat defaults to the current one (TS #45)
    parts = (message.text or "").split()
    if len(parts) > 1:
        try:
            chat_id = int(parts[1])
        except ValueError:
            await message.answer("Использование: `/force_send <chat_id>`")
            return
    else:
        chat_id = message.chat.id

    async with async_session() as session:
        # TS #45: use the same delivery logic as the scheduler -> respect chat config
        config = await ChatRepository(session).get_config(chat_id)
        post_service = PostService(jr_client, api_queue, PostRepository(session))
        delivery_service = DeliveryService(bot, post_service, MediaManager())

        res = await delivery_service.send_batch_posts(
            chat_id=chat_id,
            include_tags=config.include_tags,
            exclude_tags=config.exclude_tags,
            max_posts=config.schedule_max_posts,
            ignore_history=False,
            show_links=config.show_post_links,
        )

    # TS #46: /force_send must NOT modify last_batch_time — it doesn't: only
    # SchedulerService.check_and_send_scheduled updates it.
    if res:
        await message.answer(f"🚀 Принудительная отправка в чат {chat_id}: {res} пост(ов).")
    else:
        await message.answer(f"❌ Не удалось найти посты для чата {chat_id}.")