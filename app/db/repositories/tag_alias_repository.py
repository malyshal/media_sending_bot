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

    async def pending(self, limit: int = 20, locked: bool = False) -> List[TagAlias]:
        """Unresolved queries (for the resolution worker).

        locked=True uses SELECT ... FOR UPDATE SKIP LOCKED so concurrent
        workers (manual resolve + background loop) never pick the same rows.
        """
        q = (select(TagAlias)
             .where(TagAlias.resolved == False)  # noqa: E712
             .order_by(TagAlias.attempts, TagAlias.created_at)
             .limit(limit))
        if locked:
            q = q.with_for_update(skip_locked=True)
        rows = await self.session.execute(q)
        return rows.scalars().all()

    async def bump_attempts(self, query: str):
        row = await self.get(query)
        if row:
            row.attempts = (row.attempts or 0) + 1
            await self.session.commit()

    async def canonical_for(self, query: str) -> Optional[str]:
        row = await self.get(query)
        if row and row.resolved and row.canonical:
            return row.canonical
        return None

    async def is_broken(self, query: str) -> bool:
        """True once the resolver has confirmed the query has no real tag.
        Resolved=True and canonical=None means we've already tried and failed
        — don't keep retrying it on every /next."""
        row = await self.get(query)
        return bool(row and row.resolved and not row.canonical)

    async def is_known_broken(self, queries: List[str]) -> set[str]:
        """Bulk variant: return subset of `queries` that are confirmed broken."""
        if not queries:
            return set()
        rows = await self.session.execute(
            select(TagAlias).where(
                TagAlias.query.in_(queries),
                TagAlias.resolved == True,  # noqa: E712
                TagAlias.canonical.is_(None),
            )
        )
        return {r.query for r in rows.scalars().all()}

    async def resolve_queries(self, queries: List[str]) -> dict[str, Optional[str]]:
        """Bulk canonical lookup. Returns {query: canonical_or_None_if_broken}."""
        if not queries:
            return {}
        rows = await self.session.execute(
            select(TagAlias).where(TagAlias.query.in_(queries))
        )
        out: dict[str, Optional[str]] = {}
        for r in rows.scalars().all():
            if not r.resolved:
                continue
            out[r.query] = r.canonical
        return out
