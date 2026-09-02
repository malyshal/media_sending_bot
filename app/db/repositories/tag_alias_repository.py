"""Repository for canonical tag name resolution (user query -> API tag)."""
from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models.tag_alias import TagAlias


class TagAliasRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, query: str) -> Optional[TagAlias]:
        q = select(TagAlias).where(TagAlias.query == query).execution_options(populate_existing=True)
        result = await self.session.execute(q)
        return result.scalar_one_or_none()

    async def resolve(self, query: str, canonical: Optional[str]) -> TagAlias:
        """Insert or update the alias with the API answer."""
        row = await self.get(query)
        if row:
            row.canonical = canonical
            row.resolved = True
            row.attempts = (row.attempts or 0) + 1
        else:
            row = TagAlias(query=query, canonical=canonical, resolved=True, attempts=1)
            self.session.add(row)
        await self.session.commit()
        return row

    async def ensure_pending(self, query: str) -> TagAlias:
        """Mark a query as pending resolution (creates the row if missing)."""
        row = await self.get(query)
        if row:
            return row
        row = TagAlias(query=query, canonical=None, resolved=False, attempts=0)
        self.session.add(row)
        await self.session.commit()
        return row

    async def pending(self, limit: int = 20) -> List[TagAlias]:
        """Unresolved queries (for the resolution worker)."""
        q = (select(TagAlias)
             .where(TagAlias.resolved == False)  # noqa: E712
             .order_by(TagAlias.attempts, TagAlias.created_at)
             .limit(limit))
        rows = await self.session.execute(q)
        return rows.scalars().all()

    async def canonical_for(self, query: str) -> Optional[str]:
        row = await self.get(query)
        if row and row.resolved and row.canonical:
            return row.canonical
        return None
