import structlog
from typing import List
from datetime import datetime
from .db.session import async_session
from .db.repositories.post_repo import PostRepository
from .db.repositories.chat_repo import ChatConfigRepository
from .db.repositories.history_repo import PostHistoryRepository
from .db.models import Post

logger = structlog.get_logger()

class DeliveryService:
    async def get_posts_for_chat(self, chat_id: int) -> List[Post]:
        async with async_session() as session:
            chat_repo = ChatConfigRepository(session)
            post_repo = PostRepository(session)
            hist_repo = PostHistoryRepository(session)
            
            config = await chat_repo.get_config(chat_id)
            if not config or not config.auto_send:
                return []
                
            # Get posts that match tags and since last batch
            candidates = await post_repo.get_candidate_posts(
                include_tags=config.include_tags,
                exclude_tags=config.exclude_tags,
                limit=config.max_posts_per_batch * 10, # Fetch more to account for history
                since=config.last_batch_time
            )
            
            # History check
            sent_posts = await hist_repo.get_sent_posts(chat_id)
            
            final_list = []
            for p in candidates:
                if p.id not in sent_posts:
                    final_list.append(p)
                if len(final_list) >= config.max_posts_per_batch:
                    break
                    
            return final_list

    async def mark_delivered(self, chat_id: int, post_ids: List[int], last_batch_time: datetime):
        async with async_session() as session:
            hist_repo = PostHistoryRepository(session)
            chat_repo = ChatConfigRepository(session)
            
            for pid in post_ids:
                await hist_repo.mark_as_sent(chat_id, pid)
                
            await chat_repo.update_config(chat_id, last_batch_time=last_batch_time)

delivery_service = DeliveryService()
