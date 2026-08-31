from typing import Optional, List
from sqlalchemy import select, update, delete, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models.post import Post
from app.db.models.history import PostHistory
from app.db.models.chat import ChatConfig
from datetime import datetime, timedelta
import structlog

logger = structlog.get_logger()

class PostRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_post(self, post_data: Post) -> Post:
        await self.session.merge(post_data)
        await self.session.commit()
        return post_data

    async def get_posts_by_tags(self, include_tags: List[str], exclude_tags: List[str], limit: int = 10) -> List[Post]:
        # Use JSONB operations for tags
        # Simplified logic for PostgreSQL JSON arrays
        query = select(Post)
        
        if include_tags:
            # Any of include_tags must be present
            include_filter = or_(*[Post.tags.contains([t]) for t in include_tags])
            query = query.where(include_filter)
            
        if exclude_tags:
            # None of exclude_tags must be present
            exclude_filter = and_(*[~Post.tags.contains([t]) for t in exclude_tags])
            query = query.where(exclude_filter)
            
        result = await self.session.execute(query.limit(limit))
        return result.scalars().all()

    async def try_lock_post_for_chat(self, chat_id: int, post_id: str) -> bool:
        """
        Attempts to mark a post as sent using ON CONFLICT DO NOTHING.
        Returns True if this process won the right to send the post.
        """
        from app.db.models.history import PostHistory
        try:
            # We use a raw execute or a specific insert to ensure ON CONFLICT
            # Since SQLAlchemy's insert().on_conflict_do_nothing is dialect specific (PG)
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            
            stmt = pg_insert(PostHistory).values(
                chat_id=chat_id, 
                post_id=post_id
            ).on_conflict_do_nothing(index_elements=['chat_id', 'post_id'])
            
            result = await self.session.execute(stmt)
            await self.session.commit()
            
            # rowcount is 1 if inserted, 0 if conflict occurred
            return result.rowcount == 1
        except Exception as e:
            await self.session.rollback()
            logger.error("lock_post_failed", error=str(e))
            return False

    async def is_post_sent(self, chat_id: int, post_id: int) -> bool:
        query = select(PostHistory).where(
            and_(PostHistory.chat_id == chat_id, PostHistory.post_id == post_id)
        )
        result = await self.session.execute(query)
        return result.first() is not None

    async def cleanup_old_posts(self, ttl_hours: int):
        """
        Deletes cached posts older than ttl_hours.
        """
        cutoff = datetime.utcnow() - timedelta(hours=ttl_hours)
        query = delete(Post).where(Post.updated_at < cutoff)
        await self.session.execute(query)
        await self.session.commit()
        logger.info("cache_cleanup_completed", cutoff=cutoff)

    async def cleanup_old_history(self, retention_days: int):
        """
        Deletes sent history older than retention_days.
        Note: This might lead to repeats if a very old post reappears in cache.
        But according to TS, we must prevent infinite DB growth.
        """
        cutoff = datetime.utcnow() - timedelta(days=retention_days)
        query = delete(PostHistory).where(PostHistory.sent_at < cutoff)
        await self.session.execute(query)
        await self.session.commit()
        logger.info("history_cleanup_completed", cutoff=cutoff)
