from typing import List, Optional
from app.joyreactor.client import JoyReactorClient
from app.joyreactor.models import JRPost
from app.queue.api_queue import APIQueue
from app.db.repositories.post_repository import PostRepository
from app.db.repositories.tag_alias_repository import TagAliasRepository
from app.db.models.post import Post
from datetime import datetime
import structlog

logger = structlog.get_logger()

class PostService:
    def __init__(self, client: JoyReactorClient, queue: APIQueue, repo: PostRepository):
        self.client = client
        self.queue = queue
        self.repo = repo

    def _alias_repo(self) -> TagAliasRepository:
        """TagAliasRepository shares the session with PostRepository (both
        constructed from the same `async_session()` by the caller)."""
        return TagAliasRepository(self.repo.session)

    async def get_first_post_for_onboarding(self, chat_id: int, include_tags: List[str], exclude_tags: List[str]) -> Optional[Post]:
        """
        Logic for obtaining the first post during onboarding.
        Shared with get_next_post_for_chat but logically isolated.
        """
        return await self.get_next_post_for_chat(chat_id, include_tags, exclude_tags)

    async def _resolve_fetch_tags(self, tags: List[str]) -> List[str]:
        """Filter out tags the resolver has already marked as broken, and
        rewrite user queries to their canonical (real-API) names when known.

        Tags that have never been resolved yet still go through — the resolver
        worker will pick them up on its next tick. Only after the resolver has
        explicitly confirmed "no real tag" (resolved=True, canonical=NULL) do
        we drop them, so /next stops hammering the API on dead queries."""
        if not tags:
            return tags
        alias_repo = self._alias_repo()
        alias_map = await alias_repo.resolve_queries(tags)
        resolved: List[str] = []
        for tag in tags:
            if tag in alias_map:
                canonical = alias_map[tag]
                if not canonical:
                    # Resolver already confirmed this query has no real tag —
                    # skip it (don't add to fetched tags).
                    continue
                # Use the canonical name so fetch_posts_by_tag hits a real tag.
                if canonical != tag and canonical not in resolved:
                    resolved.append(canonical)
                elif canonical == tag and tag not in resolved:
                    resolved.append(tag)
            else:
                # Never seen by the resolver yet — try it (resolver will catch up).
                if tag not in resolved:
                    resolved.append(tag)
        return resolved

    async def get_next_post_for_chat(self, chat_id: int, include_tags: List[str], exclude_tags: List[str], ignore_history: bool = False) -> Optional[Post]:
        # 0. Drop known-broken include_tags (resolver already confirmed
        # they have no real tag on JoyReactor). Otherwise /next would keep
        # retrying the API on the same dead queries every time.
        tags_for_fetch = await self._resolve_fetch_tags(include_tags)

        # 1. Try to find a post in cache that fits tags and isn't sent.
        # Use the ORIGINAL include_tags here: a broken tag has no real
        # posts in cache either, so the cache query returns 0 (correct).
        candidate_posts = await self.repo.get_posts_by_tags(include_tags, exclude_tags, limit=50)

        for post in candidate_posts:
            if ignore_history or not await self.repo.is_post_sent(chat_id, post.id):
                return post

        # 2. If no suitable post in cache, try to fetch new ones from API
        # Process all include tags to support multiple tags (TS Section 16)
        all_fetched_posts: List[JRPost] = []

        # If user explicitly has no tags (or all are known-broken), fall back
        # to "memes" so /next isn't a no-op for a brand-new / confused user.
        # When include_tags is non-empty but resolves to empty after filtering
        # broken tags, we honor the empty list (no fallback) — the user has
        # only bad tags and we shouldn't silently spam them with memes.
        tags_to_fetch = tags_for_fetch if tags_for_fetch else (
            [] if include_tags else ["memes"]
        )

        if not tags_to_fetch:
            return None

        for tag in tags_to_fetch:
            try:
                jr_posts = await self.queue.enqueue(
                    self.client.fetch_posts_by_tag,
                    tag,
                    priority=2
                )
                all_fetched_posts.extend(jr_posts)
            except Exception as e:
                logger.error("post_service_fetch_error", tag=tag, error=str(e))

        if not all_fetched_posts:
            return None

        # Deduplicate by post ID (TS Section 16)
        unique_posts = {}
        for p in all_fetched_posts:
            unique_posts[p.id] = p

        # TS #18: cache ALL fetched posts BEFORE selecting a candidate,
        # so subsequent /next requests hit the local cache instead of the API.
        from datetime import datetime as _dt
        for jr_p in unique_posts.values():
            if not jr_p.media_url:
                continue
            db_post = Post(
                id=jr_p.id,
                text=jr_p.text,
                media_url=jr_p.media_url,
                media_type=jr_p.media_type or "image",
                tags=jr_p.tags,
                created_at=jr_p.created_at,
                # TS #14: cache TTL is measured from CACHING time (updated_at),
                # not from the post's publication date.
                updated_at=_dt.utcnow(),
                raw_data=jr_p.raw_data
            )
            await self.repo.save_post(db_post)

        # Select candidate from cached posts (exclude after API, TS #29)
        for jr_p in unique_posts.values():
            # Skip posts without resolvable media
            if not jr_p.media_url:
                logger.warning("post_has_no_media", post_id=jr_p.id)
                continue

            # Check if post contains any exclude tags
            if any(ex_tag in jr_p.tags for ex_tag in exclude_tags):
                continue

            if ignore_history or not await self.repo.is_post_sent(chat_id, jr_p.id):
                db_post = await self.repo.save_post(Post(
                    id=jr_p.id,
                    text=jr_p.text,
                    media_url=jr_p.media_url,
                    media_type=jr_p.media_type or "image",
                    tags=jr_p.tags,
                    created_at=jr_p.created_at,
                    updated_at=_dt.utcnow(),
                    raw_data=jr_p.raw_data
                ))
                return db_post

        return None

    async def mark_post_sent(self, chat_id: int, post_id: int):
        await self.repo.mark_as_sent(chat_id, post_id)
