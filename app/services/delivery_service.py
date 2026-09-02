from typing import Optional
import structlog
from aiogram import Bot, types
from app.services.post_service import PostService
from app.services.media_manager import MediaManager
from app.core.metrics import metrics
from app.bot.post_tag_keyboard import build_post_tags_keyboard
from app.db.models.post import Post
from pathlib import Path
from app.db.session import async_session
from sqlalchemy import delete, and_

logger = structlog.get_logger()

MAX_SKIP_DEPTH = 5
TELEGRAM_CAPTION_LIMIT = 1024


def _post_link(post: Post) -> str:
    """Source post URL on joyreactor.cc (for debugging/moderation)."""
    try:
        import base64 as _b64
        numeric = post.id
        if post.id.startswith("UG9zdDo"):
            decoded = _b64.b64decode(post.id).decode("utf-8", "replace")
            numeric = decoded.split(":", 1)[-1]
        return f"https://joyreactor.cc/post/{numeric}"
    except Exception:
        return ""


class _DeliveryRetryable(Exception):
    """Internal: the candidate post is broken (dead CDN link etc.); try the next one."""


import re as _re
_ATTR_PLACEHOLDER = _re.compile(r"&attribute_insert_\d+&")


def _make_caption(text: Optional[str], link: str = "") -> str:
    """TS #34: post text as caption (+optional source link), truncated to the Telegram limit.
    The source link is always kept visible: text is truncated first.
    Media placeholders (&attribute_insert_N&) are removed — they mark where
    media is inserted on the site and are not human-readable text."""
    text = _ATTR_PLACEHOLDER.sub(" ", text or "").strip()
    if not text:
        return f"🔗 {link}" if link else ""
    link_block = f"\n\n🔗 {link}" if link else ""
    budget = TELEGRAM_CAPTION_LIMIT - len(link_block)
    if len(text) > budget:
        text = text[: budget - 1] + "…"
    return text + link_block


class DeliveryService:
    def __init__(self, bot: Bot, post_service: PostService, media_manager: MediaManager):
        self.bot = bot
        self.post_service = post_service
        self.media_manager = media_manager

    async def send_batch_posts(self, chat_id: int, include_tags: list[str], exclude_tags: list[str], max_posts: int, ignore_history: bool = False, show_links: bool = False) -> int:
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
                message = await self.send_next_post(chat_id, include_tags, exclude_tags, ignore_history=ignore_history, show_links=show_links)
            except _DeliveryRetryable:
                continue
            if message:
                sent_count += 1
            else:
                break
        return sent_count

    async def send_next_post(self, chat_id: int, include_tags: list[str], exclude_tags: list[str], ignore_history: bool = False, _depth: int = 0, show_links: bool = False) -> Optional[types.Message]:
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
                return await self._retry_bounded(chat_id, include_tags, exclude_tags, ignore_history, _depth, show_links)

        processed_paths: list[Path] = []
        try:
            # 3. Prepare media (TS #83: a post may contain several media items)
            media_items = self._post_media_items(post)
            caption = _make_caption(post.text, _post_link(post) if show_links else "")

            if len(media_items) > 1:
                processed = []
                try:
                    for url, mtype in media_items:
                        path, mime = await self._prepare_media(url, mtype)
                        processed.append((path, mime))
                    message = await self.send_media_group_with_tags(chat_id, processed, post, include_tags, exclude_tags)
                    metrics.inc("posts_sent")
                    return message
                finally:
                    for p, _ in processed:
                        if p:
                            await self.media_manager.cleanup_file(p)

            # Single media
            processed_path, mime_type = await self._prepare_media(media_items[0][0], media_items[0][1])
            processed_paths.append(processed_path)

            processed_paths.append(processed_path)

            tag_kb = build_post_tags_keyboard(chat_id, post, include_tags, exclude_tags)

            if mime_type == "video/mp4":
                message = await self.bot.send_video(
                    chat_id=chat_id,
                    video=types.FSInputFile(processed_path),
                    caption=caption,
                    reply_markup=tag_kb,
                )
            elif mime_type.startswith("image/"):
                message = await self.bot.send_photo(
                    chat_id=chat_id,
                    photo=types.FSInputFile(processed_path),
                    caption=caption,
                    reply_markup=tag_kb,
                )
            else:
                # TS #33: send_document fallback for unusual media
                message = await self.bot.send_document(
                    chat_id=chat_id,
                    document=types.FSInputFile(processed_path),
                    caption=caption,
                    reply_markup=tag_kb,
                )
            metrics.inc("posts_sent")
            return message

        except Exception as e:
            metrics.inc("delivery_failures")
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
            for p in processed_paths:
                if p:
                    await self.media_manager.cleanup_file(p)

    def _post_media_items(self, post: Post) -> list[tuple[str, str]]:
        """Media list for delivery: all media if available, else the single primary item.
        Capped at 10 items (Telegram media group limit)."""
        items = []
        raw = post.raw_data if isinstance(post.raw_data, dict) else None
        if raw:
            items = self.post_service.client._all_media_urls(post.id, raw.get("attributes", []))
        if not items:
            items = [(post.media_url, post.media_type or "image")]
        return items[:10]

    async def send_media_group_with_tags(self, chat_id: int, processed: list[tuple[Path, str]], post, include_tags: list | None = None, exclude_tags: list | None = None) -> types.Message:
        """Send album with tag keyboard on the last message."""
        caption = _make_caption(post.text)
        message = await self._send_media_group(chat_id, processed, caption)
        # Media group returns a list; attach tag keyboard to a follow-up mini message
        # is not possible on an album — send tags as a separate light message.
        tag_kb = build_post_tags_keyboard(chat_id, post, include_tags, exclude_tags)
        if tag_kb and isinstance(message, list) and message:
            await self.bot.send_message(chat_id=chat_id, text="🏷 Теги поста:", reply_markup=tag_kb)
        return message[0] if isinstance(message, list) else message

    async def _prepare_media(self, media_url: str, media_type: str) -> tuple[Path, str]:
        prepared, mime = await self.media_manager.process_media(media_url, media_type)
        return prepared, mime

    async def _send_media_group(self, chat_id: int, processed: list[tuple[Path, str]], caption: str) -> types.Message:
        """TS #83: text + multiple media -> send as an album. Caption goes on the first item."""
        from aiogram.utils.media_group import MediaGroupBuilder

        builder = MediaGroupBuilder(caption=caption)
        for i, (path, mime) in enumerate(processed):
            if mime.startswith("image/"):
                builder.add_photo(media=types.FSInputFile(path))
            else:
                builder.add_video(media=types.FSInputFile(path))
        return await self.bot.send_media_group(chat_id=chat_id, media=builder.build())

    async def _retry_bounded(self, chat_id: int, include_tags: list[str], exclude_tags: list[str], ignore_history: bool, depth: int, show_links: bool = False) -> Optional[types.Message]:
        if depth >= MAX_SKIP_DEPTH:
            return None
        return await self.send_next_post(chat_id, include_tags, exclude_tags, ignore_history=ignore_history, _depth=depth + 1, show_links=show_links)

    async def _unlock_post(self, chat_id: int, post_id: str):
        async with async_session() as session:
            from app.db.models.history import PostHistory
            stmt = delete(PostHistory).where(
                and_(PostHistory.chat_id == chat_id, PostHistory.post_id == post_id)
            )
            await session.execute(stmt)
            await session.commit()