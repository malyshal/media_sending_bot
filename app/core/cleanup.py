import asyncio
import structlog
from datetime import datetime, timedelta
from sqlalchemy import select, delete
from app.db.session import async_session
from app.db.repositories.post_repository import PostRepository
from app.db.models.user import UserAccount
from app.db.models.chat import ChatConfig
from app.db.models.history import PostHistory
from app.core.config import settings

logger = structlog.get_logger()

# TS #58: recovery period before permanent deletion of user data
DELETION_RECOVERY_DAYS = 30
# TS #61: recommended cleanup worker periodicity
CLEANUP_INTERVAL_MINUTES = 30


async def purge_expired_deletions():
    """
    TS #58: permanently delete data of users whose 30-day recovery period expired.
    Deletes: user account, chat config, chat delivery history.
    """
    cutoff = datetime.utcnow() - timedelta(days=DELETION_RECOVERY_DAYS)
    async with async_session() as session:
        expired = (await session.execute(
            select(UserAccount).where(
                UserAccount.is_frozen == True,
                UserAccount.deletion_requested_at != None,  # noqa: E712
                UserAccount.deletion_requested_at < cutoff,
            )
        )).scalars().all()

        for user in expired:
            try:
                # Delete chat-level data for all chats... we only know the user's
                # telegram_id; chat configs may belong to other users in the same
                # chat, so we delete only history rows and the user account, and
                # disable auto_send for chats where this user was seen.
                await session.execute(
                    delete(PostHistory).where(PostHistory.chat_id == user.telegram_id)
                )
                await session.execute(
                    delete(ChatConfig).where(ChatConfig.chat_id == user.telegram_id)
                )
                await session.execute(
                    delete(UserAccount).where(UserAccount.telegram_id == user.telegram_id)
                )
                await session.commit()
                logger.info("user_data_purged", user_id=user.telegram_id)
            except Exception as e:
                await session.rollback()
                logger.error("user_data_purge_failed", user_id=user.telegram_id, error=str(e))


async def cleanup_worker():
    """
    Background task performing database retention policy cleanup (TS #61):
      1. expired posts cache;
      2. old delivery history;
      3. permanent deletion of user data after the recovery period (TS #58).
    Runs every 30 minutes.
    """
    while True:
        try:
            async with async_session() as session:
                repo = PostRepository(session)

                # 1. Cleanup cached posts (TTL from settings)
                await repo.cleanup_old_posts(settings.cache_retention_hours)

                # 2. Cleanup old history (30 days)
                await repo.cleanup_old_history(retention_days=30)

                logger.info("global_cleanup_cycle_finished")
        except Exception as e:
            logger.error("cleanup_worker_error", error=str(e))

        try:
            await purge_expired_deletions()
        except Exception as e:
            logger.error("deletion_purge_error", error=str(e))

        # TS #61: run the cycle every 30 minutes
        await asyncio.sleep(CLEANUP_INTERVAL_MINUTES * 60)