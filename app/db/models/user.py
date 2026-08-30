from sqlalchemy import BigInteger, DateTime, Boolean, String
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from .base import Base

class UserAccount(Base):
    __tablename__ = "user_accounts"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role: Mapped[str] = mapped_column(String, default="user") # user, admin
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Deletion logic
    deletion_requested_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    is_frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
