"""Background media prefetcher.

Warms the media cache for cached posts that have not been delivered yet, so
that /next and the scheduled delivery serve files from disk instantly
(TS #22: downloads go through the single APIQueue with lowest priority 3 —
users may wait).
"""
import structlog
from datetime import datetime

from sqlalchemy import select

from app.core.metrics import metrics
from app.db.session import async_session
from app.db.repositories.post_repository import PostRepository
from app.db.repositories.chat_repository import ChatRepository
from app.services.media_manager import MediaManager

logger = structlog.get_logger()


class MediaPrefetcher:
    def __init__(self, bot, api_queue, jr_client, media_manager: MediaManager):
        self.bot = bot
        self.api_queue = api_queue
        self.jr_client = jr_client
        self.media_manager = media_manager

    async def prefetch_cycle(self, limit: int = 6):
        """Download media for undelivered cached posts (lowest priority)."""
        from sqlalchemy import select
        from app.db.models.post import Post
        from app.db.models.history import PostHistory
        from app.db.session import async_session

        async with async_session() as session:
            # posts without any delivery record (not sent to any chat)
            sent_subq = select(PostHistory.post_id)
            rows = await session.execute(
                select(Post)
                .where(Post.id.not_in(select(PostHistory.post_id)))
                .order_by(Post.created_at.desc())
                .limit(limit)
            )
            posts = rows.scalars().all()

        downloaded = 0
        for post in posts:
            for url, mtype in self._media_items(post):
                if self.media_manager.cached_path(url):
                    continue
                try:
                    await self.api_queue.enqueue(
                        self.media_manager.process_media, url, mtype, priority=3
                    )
                    downloaded += 1
                except Exception as e:
                    # New posts use slugged CDN paths not derivable from the API:
                    # resolve real media URLs from the public post page.
                    if "HTTP 404" in str(e):
                        try:
                            resolved = await self.jr_client.resolve_media_via_post_page(post.id)
                            if resolved:
                                for u2, t2 in resolved:
                                    await self.api_queue.enqueue(
                                        self.media_manager.process_media, u2, mtype or u2.split(".")[-1], priority=3
                                    )
                                    downloaded += 1
                                continue
                        except Exception as e2:
                            logger.error("media_prefetch_resolve_failed", post_id=post.id, error=str(e2))
                    logger.error("media_prefetch_failed", post_id=post.id, url=url, error=str(e))
        if downloaded:
            logger.info("media_prefetch_downloaded", count=downloaded)

    def _media_items(self, post) -> list:
        from app.joyreactor.client import JoyReactorClient
        raw = post.raw_data if isinstance(post.raw_data, dict) else None
        items = []
        if raw:
            items = JoyReactorClient._all_media_urls(post.id, raw.get("attributes", []))
        if not items:
            items = [(post.media_url, post.media_type or "image")]
        return items[:10]

    async def prefetch_posts(self):
        """Background fetch of NEW posts by the tags of active chats.

        Keeps the cache warm for /next and scheduled delivery: posts are
        fetched through the single APIQueue (priority 3) and cached before
        any user asks for them.
        """
        from app.joyreactor.models import JRPost
        from app.db.models.post import Post
        from app.db.repositories.chat_repository import ChatRepository

        async with async_session() as session:
            rows = await session.execute(select(ChatConfig))
            configs = rows.scalars().all()

        tags: set[str] = set()
        for cfg in configs:
            if cfg.include_tags:
                tags.update(cfg.include_tags)
        if not tags:
            tags = {"memes"}

        cached = 0
        for tag in tags:
            try:
                jr_posts = await self.api_queue.enqueue(
                    self.jr_client.fetch_posts_by_tag, tag, priority=3
                )
            except Exception as e:
                logger.error("post_prefetch_fetch_failed", tag=tag, error=str(e))
                continue
            for jr_p in jr_posts:
                if not jr_p.media_url:
                    continue
                db_post = Post(
                    id=jr_p.id,
                    text=jr_p.text,
                    media_url=jr_p.media_url,
                    media_type=jr_p.media_type or "image",
                    tags=jr_p.tags,
                    created_at=jr_p.created_at,
                    updated_at=datetime.utcnow(),
                    raw_data=jr_p.raw_data,
                )
                async with async_session() as s2:
                    await PostRepository(s2).save_post(db_post)
            cached += len(jr_posts)
        if cached:
            logger.info("post_prefetch_cached", count=cached)
