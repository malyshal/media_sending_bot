from sqlalchemy import select, update, insert
from sqlalchemy.ext.asyncio import AsyncSession
from .models import ChatConfig
from typing import Optional

class ChatConfigRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_config(self, chat_id: int) -> Optional[ChatConfig]:
        result = await self.session.execute(select(ChatConfig).where(ChatConfig.chat_id == chat_id))
        return result.scalar_one_or_none()

    async def update_config(self, chat_id: int, **kwargs) -> None:
        await self.session.execute(
            update(ChatConfig)
            .where(ChatConfig.chat_id == chat_id)
            .values(**kwargs)
        )
        await self.session.commit()

    async def create_config(self, chat_id: int, **kwargs) -> ChatConfig:
        config = ChatConfig(chat_id=chat_id, **kwargs)
        self.session.add(config)
        await self.session.commit()
        await self.session.refresh(config)
        return config
