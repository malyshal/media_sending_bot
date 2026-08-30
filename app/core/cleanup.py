import asyncio
import structlog
from app.db.session import async_session
from app.db.repositories.post_repository import PostRepository
from app.core.config import settings

logger = structlog.get_logger()

async def cleanup_worker():
    """
    Background task to perform database retention policy cleanup.
    """
    while True:
        try:
            async with async_session() as session:
                repo = PostRepository(session)
                
                # 1. Cleanup cached posts
                await repo.cleanup_old_posts(settings.cache_retention_hours)
                
                # 2. Cleanup old history (e.g., 30 days)
                await repo.cleanup_old_history(retention_days=30)
                
                logger.info("global_cleanup_cycle_finished")
        except Exception as e:
            logger.error("cleanup_worker_error", error=str(e))
            
        # Run cleanup once every 6 hours
        await asyncio.sleep(settings.cache_retention_hours * 3600)
