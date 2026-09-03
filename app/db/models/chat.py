from sqlalchemy import BigInteger, String, Integer, Boolean, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from .base import Base

class ChatConfig(Base):
    __tablename__ = "chat_configs"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    
    # Delivery settings
    auto_send: Mapped[bool] = mapped_column(Boolean, default=False)
    schedule_max_posts: Mapped[int] = mapped_column(Integer, default=100)
    next_max_posts: Mapped[int] = mapped_column(Integer, default=1)
    show_post_links: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Filtering
    include_tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    exclude_tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    
    # Scheduling
    # schedule: "HH:MM" (chat-local time); for hourly modes the minute part is used
    schedule: Mapped[str] = mapped_column(String, nullable=True)
    # schedule_mode: daily | hourly | every_n_days | every_n_hours | weekly
    schedule_mode: Mapped[str] = mapped_column(String, default="daily")
    # schedule_interval: N for every_n_days/every_n_hours; weekday 0-6 (Mon=0) for weekly
    schedule_interval: Mapped[int] = mapped_column(Integer, default=1)
    timezone: Mapped[str] = mapped_column(String, default="UTC")
    
    # Tracking
    last_batch_time: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<ChatConfig(chat_id={self.chat_id}, auto_send={self.auto_send})>"
