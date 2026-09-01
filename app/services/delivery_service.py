from typing import Optional
import structlog
from aiogram import Bot, types
from app.services.post_service import PostService
from app.services.media_manager import MediaManager
from app.db.models.post import Post
from pathlib import Path
from app.db.session import async_session
from sqlalchemy import delete, and_

logger = structlog.get_logger()

class DeliveryService:
    def __init__(self, bot: Bot, post_service: PostService, media_manager: MediaManager):
        self.bot = bot
        self.post_service = post_service
        self.media_manager = media_manager

    async def send_batch_posts(self, chat_id: int, include_tags: list[str], exclude_tags: list[str], max_posts: int, ignore_history: bool = False) -> int:
        """
        Sends a batch of posts to a chat. Returns number of posts successfully sent.
        """
        sent_count = 0
        for _ in range(max_posts):
            message = await self.send_next_post(chat_id, include_tags, exclude_tags, ignore_history=ignore_history)
            if message:
                sent_count += 1
            else:
                break
        return sent_count

    async def send_next_post(self, chat_id: int, include_tags: list[str], exclude_tags: list[str], ignore_history: bool = False) -> Optional[types.Message]:
        # 1. Get candidate post
        post = await self.post_service.get_next_post_for_chat(chat_id, include_tags, exclude_tags, ignore_history=ignore_history)
        if not post:
            logger.info("no_suitable_post_found", chat_id=chat_id)
            return None
        
        # Media URL is already a direct CDN link (built from GraphQL attributes).
        media_url = post.media_url
        
        # 2. RACE CONDITION FIX: Try to lock the post in DB before processing
        # Only the process that successfully inserts into history can send the post.
        # If we are ignoring history (e.g. for /next), we don't need to lock it in DB, 
        # but we should still avoid sending the same post multiple times in one batch.
        if not ignore_history:
            if not await self.post_service.repo.try_lock_post_for_chat(chat_id, post.id):
                logger.info("post_already_locked_by_another_process", chat_id=chat_id, post_id=post.id)
                # Recursively try to get the next available post
                return await self.send_next_post(chat_id, include_tags, exclude_tags, ignore_history=ignore_history)
        
        processed_path: Optional[Path] = None
        try:
            # 3. Prepare media
            processed_path, mime_type = await self.media_manager.process_media(media_url, post.media_type)
            
            # 4. Send to Telegram
            caption = post.text or ""
            if "video" in mime_type:
                message = await self.bot.send_video(
                    chat_id=chat_id, 
                    video=types.FSInputFile(processed_path), 
                    caption=caption
                )
            else:
                message = await self.bot.send_photo(
                    chat_id=chat_id, 
                    photo=types.FSInputFile(processed_path), 
                    caption=caption
                )
            return message
        
        except Exception as e:
            logger.error(
                "delivery_failed", chat_id=chat_id, post_id=post.id,
                error=str(e) or repr(e), exc_type=type(e).__name__,
            )
            # IMPROVEMENT: Remove lock if Telegram failed, so the post can be retried later
            if not ignore_history:
                async with async_session() as session:
                    from sqlalchemy import delete
                    from app.db.models.history import PostHistory
                    stmt = delete(PostHistory).where(
                        and_(PostHistory.chat_id == chat_id, PostHistory.post_id == post.id)
                    )
                    await session.execute(stmt)
                    await session.commit()
            return None
        finally:
            if processed_path:
                await self.media_manager.cleanup_file(processed_path)
