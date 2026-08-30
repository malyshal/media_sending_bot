from sqlalchemy import BigInteger, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from .base import Base

class PostHistory(Base):
    __tablename__ = "post_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    post_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('chat_id', 'post_id', name='uq_chat_post'),
    )

