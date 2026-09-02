import asyncio
import structlog
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis
from app.core.config import settings
from app.core.logging import setup_logging
from app.bot.handlers.next_handler import router as next_router
from app.bot.handlers.settings_handler import router as settings_router
from app.bot.handlers.admin_handler import router as admin_router
from app.bot.handlers.onboarding_handler import router as onboarding_router
from app.bot.handlers.help_handler import router as help_router
from app.core.cleanup import cleanup_worker
from app.db.session import async_session, engine
from app.db.models.base import Base
from app.core.bootstrap import bootstrap_admins
from app.bot.bot_commands import setup_bot_commands

logger = structlog.get_logger()

async def scheduler_loop(bot: Bot, queue: 'APIQueue', client: 'JoyReactorClient'):
    """Background task that checks schedules every minute (TS #39).

    A fresh AsyncSession is opened per iteration: a single long-lived
    session would hold DB locks (idle-in-transaction) and break other
    writers (TS #50/#65).
    """
    from app.services.media_manager import MediaManager
    from app.services.post_service import PostService
    from app.services.delivery_service import DeliveryService
    from app.services.scheduler_service import SchedulerService
    from app.db.repositories.post_repository import PostRepository
    from app.db.repositories.chat_repository import ChatRepository

    media_manager = MediaManager()

    while True:
        try:
            async with async_session() as session:
                repo = PostRepository(session)
                chat_repo = ChatRepository(session)
                post_service = PostService(client, queue, repo)
                delivery_service = DeliveryService(bot, post_service, media_manager)
                scheduler = SchedulerService(bot, delivery_service, chat_repo)
                await scheduler.check_and_send_scheduled(session)
        except Exception as e:
            logger.error("scheduler_loop_error", error=str(e))
        await asyncio.sleep(60)

async def main():
    setup_logging()
    logger.info("bot_starting")
    
    # 0. Config sanity check (TS #66: QUEUE_TYPE memory|redis)
    if settings.queue_type != "memory":
        logger.error("unsupported_queue_type", queue_type=settings.queue_type)
        raise SystemExit(f"QUEUE_TYPE={settings.queue_type!r} is not supported yet; use 'memory'")
    
    # 1. Database Initialization
    # TS #102: create_all is only a convenience for the first dev run; schema
    # changes in production go through migrations (scripts/run_migrations.py).
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async with async_session() as session:
        await bootstrap_admins(session)
    
    # 2. Global Singleton Services (TS #67)
    from app.queue.api_queue import APIQueue
    from app.joyreactor.client import JoyReactorClient
    
    global_queue = APIQueue(interval=settings.api_request_interval)
    global_jr_client = JoyReactorClient()
    
    bot = Bot(token=settings.bot_token)
    
    # 3. Redis FSM Storage
    redis = Redis.from_url(settings.redis_url)
    storage = RedisStorage(redis)
    
    dp = Dispatcher(storage=storage)
    
    # Pass global services to handlers via dp.workflow_data
    dp.workflow_data.update({
        "api_queue": global_queue,
        "jr_client": global_jr_client
    })
    
    dp.include_router(onboarding_router)
    dp.include_router(next_router)
    dp.include_router(settings_router)
    dp.include_router(admin_router)
    dp.include_router(help_router)
    
    # Register the "/" command menu (quick access for users)
    await setup_bot_commands(bot)
    
    # Start background tasks (TS #39, #61)
    scheduler_task = asyncio.create_task(scheduler_loop(bot, global_queue, global_jr_client))
    cleanup_task = asyncio.create_task(cleanup_worker())

    # Canonical tag resolution worker (runs alongside the scheduler every 60s)
    from app.services.tag_resolver import TagResolverService

    async def tag_resolver_loop():
        from app.db.session import async_session as _sess
        from app.db.repositories.post_repository import PostRepository
        from app.services.post_service import PostService
        while True:
            try:
                async with _sess() as session:
                    ps = PostService(global_jr_client, global_queue, PostRepository(session))
                    resolver = TagResolverService(bot, global_queue, global_jr_client, ps)
                    await resolver.resolve_pending(limit=5)
            except Exception as e:
                logger.error("tag_resolver_loop_error", error=str(e))
            await asyncio.sleep(60)

    tag_resolver_task = asyncio.create_task(tag_resolver_loop())
    
    try:
        await dp.start_polling(bot)
    finally:
        # TS #68: graceful shutdown — close everything we opened
        for task in (scheduler_task, cleanup_task, tag_resolver_task):
            task.cancel()
        for task in (scheduler_task, cleanup_task, tag_resolver_task):
            try:
                await task
            except asyncio.CancelledError:
                pass
        await bot.session.close()
        await global_jr_client.close()
        await global_queue.stop()
        await storage.close()
        await redis.aclose()
        await engine.dispose()
        logger.info("bot_stopped")

if __name__ == "__main__":
    asyncio.run(main())