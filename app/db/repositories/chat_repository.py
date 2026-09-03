from typing import Optional, List
import re
from sqlalchemy import select, update, insert
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models.chat import ChatConfig
from app.core.config import settings

TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

class ChatRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_config(self, chat_id: int) -> ChatConfig:
        query = select(ChatConfig).where(ChatConfig.chat_id == chat_id).execution_options(populate_existing=True)
        result = await self.session.execute(query)
        config = result.scalar_one_or_none()
        
        if not config:
            # Create default config if not exists
            config = ChatConfig(
                chat_id=chat_id,
                auto_send=False,
                schedule_max_posts=100,
                next_max_posts=1,
                show_post_links=False,
                include_tags=[],
                exclude_tags=[],
                schedule="12:00",
                schedule_mode="daily",
                schedule_interval=1,
                timezone="UTC",
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

    async def set_auto_send(self, chat_id: int, enabled: bool) -> ChatConfig:
        await self.get_config(chat_id)
        query = update(ChatConfig).where(ChatConfig.chat_id == chat_id).values(auto_send=enabled)
        await self.session.execute(query)
        await self.session.commit()
        return await self.get_config(chat_id)

    async def update_schedule(self, chat_id: int, schedule: str, timezone: str,
                              mode: str = "daily", interval: int = 1):
        """Stores a delivery time (strict 'HH:MM', or '*:MM' for hourly modes), mode and interval."""
        is_hourly = mode in ("hourly", "every_n_hours")
        pattern = re.compile(r"^\*:[0-5]\d$") if is_hourly else TIME_PATTERN
        if not pattern.match(schedule or ""):
            expected = "'*:MM'" if is_hourly else "'HH:MM'"
            raise ValueError(f"Invalid schedule format: {schedule!r}, expected {expected}")
        if mode not in ("daily", "hourly", "every_n_days", "every_n_hours", "weekly"):
            raise ValueError(f"Invalid schedule mode: {mode!r}")
        query = update(ChatConfig).where(ChatConfig.chat_id == chat_id).values(
            schedule=schedule,
            timezone=timezone,
            schedule_mode=mode,
            schedule_interval=interval,
        )
        await self.session.execute(query)
        await self.session.commit()

    async def set_timezone(self, chat_id: int, timezone: str):
        import zoneinfo
        try:
            zoneinfo.ZoneInfo(timezone)
        except Exception as e:
            raise ValueError(f"Invalid timezone: {timezone!r}") from e
        query = update(ChatConfig).where(ChatConfig.chat_id == chat_id).values(timezone=timezone)
        await self.session.execute(query)
        await self.session.commit()

    async def set_schedule_max_posts(self, chat_id: int, value: int) -> ChatConfig:
        await self.get_config(chat_id)
        query = update(ChatConfig).where(ChatConfig.chat_id == chat_id).values(
            schedule_max_posts=max(1, min(value, 100))
        )
        await self.session.execute(query)
        await self.session.commit()
        return await self.get_config(chat_id)

    async def set_show_post_links(self, chat_id: int, enabled: bool) -> ChatConfig:
        await self.get_config(chat_id)
        query = update(ChatConfig).where(ChatConfig.chat_id == chat_id).values(show_post_links=enabled)
        await self.session.execute(query)
        await self.session.commit()
        return await self.get_config(chat_id)

    async def set_next_max_posts(self, chat_id: int, value: int) -> ChatConfig:
        await self.get_config(chat_id)
        query = update(ChatConfig).where(ChatConfig.chat_id == chat_id).values(
            next_max_posts=max(1, min(value, 20))
        )
        await self.session.execute(query)
        await self.session.commit()
        return await self.get_config(chat_id)
