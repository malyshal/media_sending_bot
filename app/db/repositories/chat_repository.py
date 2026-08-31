from typing import Optional, List
from sqlalchemy import select, update, insert
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models.chat import ChatConfig

class ChatRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_config(self, chat_id: int) -> ChatConfig:
        query = select(ChatConfig).where(ChatConfig.chat_id == chat_id)
        result = await self.session.execute(query)
        config = result.scalar_one_or_none()
        
        if not config:
            # Create default config if not exists
            config = ChatConfig(
                chat_id=chat_id,
                auto_send=True,
                max_posts_per_batch=3,
                include_tags=[],
                exclude_tags=[],
                schedule=None,
                timezone="Europe/Moscow"
            )
            self.session.add(config)
            await self.session.commit()
            
        return config

    async def update_tags(self, chat_id: int, include_tags: List[str], exclude_tags: List[str]):
        # Rule: A tag cannot be in both lists
        # Last action wins: if we update both, we just save them. 
        # If the logic is called from separate methods, we'd handle conflicts there.
        query = update(ChatConfig).where(ChatConfig.chat_id == chat_id).values(
            include_tags=include_tags,
            exclude_tags=exclude_tags
        )
        await self.session.execute(query)
        await self.session.commit()

    async def set_auto_send(self, chat_id: int, enabled: bool):
        query = update(ChatConfig).where(ChatConfig.chat_id == chat_id).values(auto_send=enabled)
        await self.session.execute(query)
        await self.session.commit()

    async def update_schedule(self, chat_id: int, schedule: str, timezone: str):
        query = update(ChatConfig).where(ChatConfig.chat_id == chat_id).values(
            schedule=schedule,
            timezone=timezone
        )
        await self.session.execute(query)
        await self.session.commit()
