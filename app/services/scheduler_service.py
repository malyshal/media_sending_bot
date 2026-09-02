from typing import List
import structlog
from datetime import datetime, timedelta
import zoneinfo
from sqlalchemy import select, delete, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models.chat import ChatConfig
from app.db.models.post import Post
from app.db.models.history import PostHistory
from app.services.delivery_service import DeliveryService
from app.db.repositories.chat_repository import ChatRepository

logger = structlog.get_logger()

class SchedulerService:
    def __init__(self, bot, delivery_service: DeliveryService, chat_repo: ChatRepository):
        self.bot = bot
        self.delivery_service = delivery_service
        self.chat_repo = chat_repo

    async def check_and_send_scheduled(self, session: AsyncSession):
        """
        Checks all active chats. Uses a window of 1 minute to ensure no post is missed
        even if the loop is slightly delayed.
        """
        query = select(ChatConfig).where(ChatConfig.auto_send == True)
        result = await session.execute(query)
        chats = result.scalars().all()
        
        now_utc = datetime.now(zoneinfo.ZoneInfo("UTC"))
        
        for config in chats:
            if config.schedule and self._should_send_now(config, now_utc):
                logger.info("scheduled_send_triggered", chat_id=config.chat_id)
                
                sent_count = await self.delivery_service.send_batch_posts(
                    chat_id=config.chat_id,
                    include_tags=config.include_tags,
                    exclude_tags=config.exclude_tags,
                    max_posts=config.schedule_max_posts,
                    show_links=config.show_post_links,
                )
                
                if sent_count > 0:
                    config.last_batch_time = now_utc.replace(tzinfo=None)
                    await session.commit()
                    logger.info("scheduled_send_completed", chat_id=config.chat_id, sent_count=sent_count)
                else:
                    logger.info("scheduled_send_no_posts", chat_id=config.chat_id)

    def _should_send_now(self, config: ChatConfig, now_utc: datetime) -> bool:
        if not config.schedule:
            return False
        # Schedule is stored in strict "HH:MM" format (see /settings).
        # Anything else (e.g. legacy cron) is invalid -> skip and log.
        parts = config.schedule.split(":")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            logger.error("invalid_schedule_format", chat_id=config.chat_id, schedule=config.schedule)
            return False
        try:
            tz = zoneinfo.ZoneInfo(config.timezone)
            local_now = now_utc.astimezone(tz)
            
            target_h, target_m = int(parts[0]), int(parts[1])
            
            # Check if we are in the target minute
            if local_now.hour == target_h and local_now.minute == target_m:
                # Critical: Prevent double-sending within the same minute
                if config.last_batch_time:
                    # Convert last_batch_time (UTC) to local to compare minutes
                    last_local = config.last_batch_time.replace(tzinfo=zoneinfo.ZoneInfo("UTC")).astimezone(tz)
                    if last_local.hour == target_h and last_local.minute == target_m:
                        return False
                return True
        except Exception as e:
            logger.error("schedule_check_error", chat_id=config.chat_id, error=str(e))
            
        return False
