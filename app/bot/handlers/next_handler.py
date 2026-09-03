from aiogram import Router, types, Bot
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
import structlog
from app.services.delivery_service import DeliveryService
from app.db.session import async_session
from app.bot.console import delete_user_message, send_ephemeral

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
