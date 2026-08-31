import asyncio
import structlog
from aiogram import Bot, Dispatcher
from app.core.config import settings
from app.core.logging import setup_logging
from app.bot.handlers.next_handler import router as next_router
from app.bot.handlers.settings_handler import router as settings_router
from app.bot.handlers.admin_handler import router as admin_router
from app.core.cleanup import cleanup_worker
from app.db.session import async_session, engine
from app.db.models.base import Base
from app.core.bootstrap import bootstrap_admins

logger = structlog.get_logger()

async def scheduler_loop(bot: Bot, queue: 'APIQueue'):
    """Background task that checks schedules every minute."""
    from app.joyreactor.client import JoyReactorClient
    from app.services.media_manager import MediaManager
    from app.services.post_service import PostService
    from app.services.delivery_service import DeliveryService
    from app.services.scheduler_service import SchedulerService
    from app.db.repositories.post_repository import PostRepository
    from app.db.repositories.chat_repository import ChatRepository
    
    client = JoyReactorClient()
    media_manager = MediaManager()
    
    async with async_session() as session:
        repo = PostRepository(session)
        chat_repo = ChatRepository(session)
        post_service = PostService(client, queue, repo)
        delivery_service = DeliveryService(bot, post_service, media_manager)
        scheduler = SchedulerService(bot, delivery_service, chat_repo)
        
        while True:
            try:
                await scheduler.check_and_send_scheduled(session)
            except Exception as e:
                logger.error("scheduler_loop_error", error=str(e))
            await asyncio.sleep(60)

async def main():
    setup_logging()
    logger.info("bot_starting", token=settings.bot_token[:5] + "...")
    
    # 1. Database Initialization
    async with engine.begin() as conn:
        # Using run_sync to call synchronous Base.metadata.create_all
        await conn.run_sync(Base.metadata.create_all)
    
    async with async_session() as session:
        # Bootstrap admins from .env
        await bootstrap_admins(session)
    
    # 2. Global API Queue (DI approach)
    from app.queue.api_queue import APIQueue
    global_queue = APIQueue(interval=settings.api_request_interval)
    
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    
    dp.include_router(next_router)
    dp.include_router(settings_router)
    dp.include_router(admin_router)
    
    # Start background tasks
    asyncio.create_task(scheduler_loop(bot, global_queue))
    asyncio.create_task(cleanup_worker())
    
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
