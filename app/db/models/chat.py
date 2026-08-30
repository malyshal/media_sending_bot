from sqlalchemy import BigInteger, String, Integer, Boolean, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from .base import Base

class ChatConfig(Base):
    __tablename__ = "chat_configs"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    
    # Delivery settings
    auto_send: Mapped[bool] = mapped_column(Boolean, default=True)
    max_posts_per_batch: Mapped[int] = mapped_column(Integer, default=3)
    
    # Filtering
    include_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    exclude_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    
    # Scheduling
    # We can store the schedule as a cron string or a JSON list of times
    schedule: Mapped[str] = mapped_column(String, nullable=False) 
    timezone: Mapped[str] = mapped_column(String, default="UTC")
    
    # Tracking
    last_batch_time: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<ChatConfig(chat_id={self.chat_id}, auto_send={self.auto_send})>"
