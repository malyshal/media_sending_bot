from typing import List, Optional
from app.joyreactor.client import JoyReactorClient
from app.joyreactor.models import JRPost
from app.queue.api_queue import APIQueue
from app.db.repositories.post_repository import PostRepository
from app.db.models.post import Post
import structlog

logger = structlog.get_logger()

class PostService:
    def __init__(self, client: JoyReactorClient, queue: APIQueue, repo: PostRepository):
        self.client = client
        self.queue = queue
        self.repo = repo

    async def get_next_post_for_chat(self, chat_id: int, include_tags: List[str], exclude_tags: List[str]) -> Optional[Post]:
        # 1. Try to find a post in cache that fits tags and isn't sent
        candidate_posts = await self.repo.get_posts_by_tags(include_tags, exclude_tags, limit=50)
        
        for post in candidate_posts:
            if not await self.repo.is_post_sent(chat_id, post.id):
                return post
        
        # 2. If no suitable post in cache, try to fetch new ones from API
        # Process all include tags to support multiple tags (TS Section 16)
        all_fetched_posts: List[JRPost] = []
        
        tags_to_fetch = include_tags if include_tags else ["memes"]
        
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
        
        # Apply EXCLUDE and check if sent
        for jr_p in unique_posts.values():
            # Check if post contains any exclude tags
            if any(ex_tag in jr_p.tags for ex_tag in exclude_tags):
                continue
                
            # Map JRPost to DB Post
            db_post = Post(
                id=jr_p.id,
                text=jr_p.text,
                media_url=jr_p.media_url,
                media_type=jr_p.media_type,
                tags=jr_p.tags,
                created_at=jr_p.created_at,
                updated_at=jr_p.created_at,
                raw_data=jr_p.raw_data
            )
            await self.repo.save_post(db_post)
            
            if not await self.repo.is_post_sent(chat_id, db_post.id):
                return db_post
                
        return None

    async def mark_post_sent(self, chat_id: int, post_id: int):
        await self.repo.mark_as_sent(chat_id, post_id)
