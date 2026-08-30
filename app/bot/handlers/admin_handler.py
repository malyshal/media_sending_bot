from aiogram import Router, types, F
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

logger = structlog.get_logger()
router = Router()

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
        
        # Basic Stats
        chats_count = await session.execute(select(func.count()).select_from(ChatConfig))
        users_count = await session.execute(select(func.count()).select_from(UserAccount))
        posts_count = await session.execute(select(func.count()).select_from(Post))
        
        text = (
            f"📊 *Системная статистика*\n\n"
            f"👥 Пользователей: {users_count.scalar()}\n"
            f"💬 Активных чатов: {chats_count.scalar()}\n"
            f"📦 Постов в кэше: {posts_count.scalar()}\n"
        )
        await message.answer(text, parse_mode="Markdown")

@router.message(Command("force_send"))
async def cmd_force_send(message: types.Message, bot: types.Bot):
    if not await admin_filter(message):
        return

    chat_id = message.chat.id
    # Reuse the logic from /next but for admin
    from app.services.delivery_service import DeliveryService
    from app.services.post_service import PostService
    from app.services.media_manager import MediaManager
    from app.queue.api_queue import APIQueue
    from app.joyreactor.client import JoyReactorClient
    
    async with async_session() as session:
        client = JoyReactorClient()
        queue = APIQueue()
        repo = PostRepository(session)
        post_service = PostService(client, queue, repo)
        media_manager = MediaManager()
        delivery_service = DeliveryService(bot, post_service, media_manager)
        
        # Force send usually ignores filters or uses default
        res = await delivery_service.send_next_post(chat_id, [], [])
        
        if res:
            await message.answer("🚀 Принудительная отправка выполнена!")
        else:
            await message.answer("❌ Не удалось найти пост для отправки.")
        
        await client.close()
