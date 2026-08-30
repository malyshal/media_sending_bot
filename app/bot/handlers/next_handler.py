from aiogram import Router, types
from aiogram.filters import Command
import structlog
from app.services.delivery_service import DeliveryService
from app.db.repositories.chat_repository import ChatRepository # Will create this
from app.db.session import async_session

logger = structlog.get_logger()
router = Router()

@router.message(Command("next"))
async def cmd_next(message: types.Message, bot: types.Bot):
    chat_id = message.chat.id
    logger.info("cmd_next_received", chat_id=chat_id)
    
    async with async_session() as session:
        from app.services.post_service import PostService
        from app.services.media_manager import MediaManager
        from app.db.repositories.post_repository import PostRepository
        from app.db.repositories.chat_repository import ChatRepository
        from app.queue.api_queue import APIQueue
        from app.joyreactor.client import JoyReactorClient
        
        # Initialize components
        client = JoyReactorClient()
        queue = APIQueue()
        repo = PostRepository(session)
        chat_repo = ChatRepository(session)
        post_service = PostService(client, queue, repo)
        media_manager = MediaManager()
        delivery_service = DeliveryService(bot, post_service, media_manager)
        
        # Load actual chat settings
        config = await chat_repo.get_config(chat_id)
        
        res = await delivery_service.send_next_post(
            chat_id=chat_id, 
            include_tags=config.include_tags, 
            exclude_tags=config.exclude_tags
        )
        
        if not res:
            await message.answer("No new posts found for your filters right now! 😢")
        
        await client.close()
        await queue.stop()
