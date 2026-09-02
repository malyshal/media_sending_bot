"""Canonical tag name resolution service.

User adds a tag via search -> if unknown, the resolver (background worker)
asks the API what canonical tag the posts found by this query actually carry,
stores the mapping in tag_aliases and applies it to the chats that requested
the tag. All API lookups go through APIQueue (TS #22/#24).
"""
from collections import Counter

import structlog

from app.core.metrics import metrics
from app.db.session import async_session
from app.db.repositories.tag_alias_repository import TagAliasRepository

logger = structlog.get_logger()

MAX_ATTEMPTS = 5
DOMINANT_SHARE = 0.7
DOMINANT_SHARE_STRONG = 0.9


class TagResolverService:
    def __init__(self, bot, api_queue, jr_client, post_service):
        self.bot = bot
        self.api_queue = api_queue
        self.jr_client = jr_client
        self.post_service = post_service

    async def resolve_pending(self, limit: int = 5):
        """Worker tick: resolve a few pending tag queries per cycle."""
        async with async_session() as session:
            repo = TagAliasRepository(session)
            pending = await repo.pending(limit=limit)

        for row in pending:
            if (row.attempts or 0) >= MAX_ATTEMPTS:
                continue
            query = row.query
            try:
                canonical = await self._canonical_for(query)
                async with async_session() as session:
                    await TagAliasRepository(session).resolve(query, canonical)
                logger.info("tag_alias_resolved", query=query, canonical=canonical)
                await self._apply_to_chats(query, canonical)
            except Exception as e:
                async with async_session() as session:
                    r = await TagAliasRepository(session).get(query)
                    if r:
                        r.attempts = (r.attempts or 0) + 1
                        await session.commit()
                logger.error("tag_alias_resolve_failed", query=query, error=str(e))

    async def _canonical_for(self, query: str) -> str | None:
        """1) If the API knows tag(query) verbatim -> canonical = query.
        2) Else: search posts by the query and find a dominant tag
           (>= 70% of posts, or >= 90% regardless of name similarity)."""
        try:
            info = await self.api_queue.enqueue(self.jr_client.get_tag_info, query, priority=2)
            if info:
                return info["name"]
        except Exception as e:
            logger.warning("tag_info_lookup_failed", query=query, error=str(e))

        posts = await self.api_queue.enqueue(self.jr_client.search_posts, query, 1, priority=2)
        if not posts:
            return None
        counter: Counter = Counter()
        for p in posts:
            for t in p.tags or []:
                counter[t] += 1
        if not counter:
            return None
        top_tag, top_count = counter.most_common(1)[0]
        share = top_count / len(posts)
        if share >= DOMINANT_SHARE_STRONG:
            return top_tag
        if share >= DOMINANT_SHARE and (
            top_tag.lower() in query.lower() or query.lower() in top_tag.lower()
        ):
            return top_tag
        return None

    async def _apply_to_chats(self, query: str, canonical: str | None):
        """Swap the raw query for the canonical tag in all chat filters that use it."""
        if not canonical or canonical == query:
            return
        from sqlalchemy import select
        from app.db.models.chat import ChatConfig
        from app.db.session import async_session
        from app.db.repositories.chat_repository import ChatRepository

        async with async_session() as session:
            rows = await session.execute(select(ChatConfig).where(
                (ChatConfig.include_tags.contains([query]))
                | (ChatConfig.exclude_tags.contains([query]))
            ))
            configs = rows.scalars().all()
            chat_repo = ChatRepository(session)
            for cfg in configs:
                new_inc = [canonical if t == query else t for t in cfg.include_tags]
                new_exc = [canonical if t == query else t for t in cfg.exclude_tags]
                # dedupe while preserving order (replacement may create duplicates)
                new_inc = list(dict.fromkeys(new_inc))
                new_exc = list(dict.fromkeys(new_exc))
                if new_inc != cfg.include_tags or new_exc != cfg.exclude_tags:
                    await chat_repo.update_tags(cfg.chat_id, new_inc, new_exc)
                    logger.info("tag_alias_applied", chat_id=cfg.chat_id,
                                query=query, canonical=canonical)
