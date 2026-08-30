from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from .models import Post
from datetime import datetime
from typing import List, Optional

class PostRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, post_data: dict) -> Post:
        post = Post(**post_data)
        self.session.add(post)
        await self.session.commit()
        await self.session.refresh(post)
        return post

    async def get_by_id(self, post_id: int) -> Optional[Post]:
        result = await self.session.execute(select(Post).where(Post.id == post_id))
        return result.scalar_one_or_none()

    async def get_candidate_posts(self, include_tags: List[str], exclude_tags: List[str], limit: int, since: Optional[datetime] = None) -> List[Post]:
        # To avoid loading the entire DB, we fetch a reasonable number of recent posts.
        # In a real production system, we would use JSONB containment queries in PostgreSQL.
        query = select(Post).order_by(Post.created_at.desc()).limit(1000)
        result = await self.session.execute(query)
        posts = result.scalars().all()
        
        filtered = []
        for p in posts:
            # Exclude tags check: if any exclude tag is in post tags, skip
            if any(tag in p.tags for tag in exclude_tags):
                continue
            # Include tags check: if include tags are specified, post must have at least one
            if include_tags and not any(tag in p.tags for tag in include_tags):
                continue
            # Since check
            if since and p.created_at < since:
                continue
            filtered.append(p)
            if len(filtered) >= limit:
                break
                
        return filtered

