from typing import Optional
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models.user import UserAccount

class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_user(self, telegram_id: int) -> UserAccount:
        query = select(UserAccount).where(UserAccount.telegram_id == telegram_id)
        result = await self.session.execute(query)
        user = result.scalar_one_or_none()
        
        if not user:
            user = UserAccount(telegram_id=telegram_id)
            self.session.add(user)
            await self.session.commit()
        return user

    async def request_deletion(self, telegram_id: int):
        from datetime import datetime
        query = update(UserAccount).where(UserAccount.telegram_id == telegram_id).values(
            deletion_requested_at=datetime.utcnow(),
            is_frozen=True
        )
        await self.session.execute(query)
        await self.session.commit()

    async def cancel_deletion(self, telegram_id: int):
        query = update(UserAccount).where(UserAccount.telegram_id == telegram_id).values(
            deletion_requested_at=None,
            is_frozen=False
        )
        await self.session.execute(query)
        await self.session.commit()

    async def set_role(self, telegram_id: int, role: str):
        query = update(UserAccount).where(UserAccount.telegram_id == telegram_id).values(role=role)
        await self.session.execute(query)
        await self.session.commit()

    async def is_admin(self, telegram_id: int) -> bool:
        user = await self.get_user(telegram_id)
        return user.role == "admin"
