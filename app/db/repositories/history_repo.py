from sqlalchemy import select, insert
from sqlalchemy.ext.asyncio import AsyncSession
from .models import PostHistory
from typing import List

class PostHistoryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def mark_as_sent(self, chat_id: int, post_id: int) -> None:
        await self.session.execute(
            insert(PostHistory).values(chat_id=chat_id, post_id=post_id)
        )
        await self.session.commit()

    async def get_sent_posts(self, chat_id: int) -> List[int]:
        result = await self.session.execute(
            select(PostHistory.post_id).where(PostHistory.chat_id == chat_id)
        )
        return result.scalars().all()
