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

MAX_SKIP_DEPTH = 5


class _DeliveryRetryable(Exception):
    """Internal: the candidate post is broken (dead CDN link etc.); try the next one."""


class DeliveryService:
    def __init__(self, bot: Bot, post_service: PostService, media_manager: MediaManager):
        self.bot = bot
        self.post_service = post_service
        self.media_manager = media_manager

    async def send_batch_posts(self, chat_id: int, include_tags: list[str], exclude_tags: list[str], max_posts: int, ignore_history: bool = False) -> int:
        """
        Sends a batch of posts to a chat. Returns number of posts successfully sent.
        Broken posts (dead CDN links, oversized media) are skipped, bounded by
        MAX_SKIP_DEPTH to obey TS #74 (no infinite recursive search).
        """
        sent_count = 0
        attempts = 0
        max_attempts = max_posts + MAX_SKIP_DEPTH
        while sent_count < max_posts and attempts < max_attempts:
            attempts += 1
            try:
                message = await self.send_next_post(chat_id, include_tags, exclude_tags, ignore_history=ignore_history)
            except _DeliveryRetryable:
                continue
            if message:
                sent_count += 1
            else:
                break
        return sent_count

    async def send_next_post(self, chat_id: int, include_tags: list[str], exclude_tags: list[str], ignore_history: bool = False, _depth: int = 0) -> Optional[types.Message]:
        # 1. Get candidate post
        post = await self.post_service.get_next_post_for_chat(chat_id, include_tags, exclude_tags, ignore_history=ignore_history)
        if not post:
            logger.info("no_suitable_post_found", chat_id=chat_id)
            return None

        # Media URL is already a direct CDN link (built from GraphQL attributes).
        media_url = post.media_url

        # 2. RACE CONDITION FIX: Try to lock the post in DB before processing
        # Only the process that successfully inserts into history can send the post.
        if not ignore_history:
            if not await self.post_service.repo.try_lock_post_for_chat(chat_id, post.id):
                logger.info("post_already_locked_by_another_process", chat_id=chat_id, post_id=post.id)
                return await self._retry_bounded(chat_id, include_tags, exclude_tags, ignore_history, _depth)

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
            # Unlock so the post can be retried later if the failure was transient
            if not ignore_history:
                await self._unlock_post(chat_id, post.id)
            # Broken candidate (dead CDN link, oversized media, Telegram refusal):
            # signal the caller to try the next post instead of aborting.
            raise _DeliveryRetryable() from e
        finally:
            if processed_path:
                await self.media_manager.cleanup_file(processed_path)

    async def _retry_bounded(self, chat_id: int, include_tags: list[str], exclude_tags: list[str], ignore_history: bool, depth: int) -> Optional[types.Message]:
        if depth >= MAX_SKIP_DEPTH:
            return None
        return await self.send_next_post(chat_id, include_tags, exclude_tags, ignore_history=ignore_history, _depth=depth + 1)

    async def _unlock_post(self, chat_id: int, post_id: str):
        async with async_session() as session:
            from app.db.models.history import PostHistory
            stmt = delete(PostHistory).where(
                and_(PostHistory.chat_id == chat_id, PostHistory.post_id == post_id)
            )
            await session.execute(stmt)
            await session.commit()