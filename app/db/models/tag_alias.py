from sqlalchemy import String, DateTime, Integer, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from .base import Base


class TagAlias(Base):
    """Canonical tag names resolved from user search queries.

    query     - what the user typed / picked (e.g. "тюлень")
    canonical - canonical tag name known to the API (e.g. "cats"); NULL until resolved
    resolved  - True once the API answered for this query
    attempts  - number of resolution attempts (for retry policy)
    """
    __tablename__ = "tag_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query: Mapped[str] = mapped_column(String, unique=True)
    canonical: Mapped[str] = mapped_column(String, nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<TagAlias(query={self.query!r}, canonical={self.canonical!r}, resolved={self.resolved})>"
