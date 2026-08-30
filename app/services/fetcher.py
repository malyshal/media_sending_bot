import asyncio
import structlog
from datetime import datetime, timedelta
from .joyreactor.service import joyreactor_service
from .db.session import async_session
from .db.repositories.post_repo import PostRepository

logger = structlog.get_logger()

class ContentFetcher:
    def __init__(self, interval_seconds: int = 300):
        self.interval = interval_seconds
        self._running = False

    async def fetch_and_cache(self):
        async with async_session() as session:
            repo = PostRepository(session)
            
            logger.info("fetching_new_content_started")
            try:
                # We could fetch from various popular tags or a general feed
                # For now, let's fetch the latest posts from the main feed
                posts_data = await joyreactor_service.get_latest_posts(first=50)
                
                for data in posts_data:
                    post_id = data["id"]
                    existing = await repo.get_by_id(post_id)
                    if existing:
                        continue
                    
                    # Transform GraphQL data to DB model
                    # Note: JoyReactor attributes can be complex, we simplify for now
                    media = data["attributes"][0]["image"]["url"] if data["attributes"] else None
                    if not media:
                        continue
                        
                    post_entry = {
                        "id": post_id,
                        "text": data.get("text"),
                        "media_url": media,
                        "media_type": data["attributes"][0]["type"] if data["attributes"] else "PICTURE",
                        "tags": [t["name"] for t in data.get("tags", [])],
                        "created_at": datetime.fromisoformat(data["createdAt"].replace("Z", "+00:00")),
                        "updated_at": datetime.fromisoformat(data["updatedAt"].replace("Z", "+00:00")),
                        "raw_data": data
                    }
                    await repo.create(post_entry)
                    logger.debug("post_cached", post_id=post_id)
                
                logger.info("fetching_new_content_finished", count=len(posts_data))
            except Exception as e:
                logger.error("fetching_error", error=str(e))

    async def run(self):
        self._running = True
        while self._running:
            await self.fetch_and_cache()
            await asyncio.sleep(self.interval)

    def stop(self):
        self._running = False

fetcher = ContentFetcher()
