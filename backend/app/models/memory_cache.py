from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class MemoryCache(Base):
    """缓存最近行程的语义摘要，避免每次规划都重复调用 LLM。"""

    __tablename__ = "memory_caches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    trip_summary: Mapped[str] = mapped_column(Text, default="")
    trip_summary_hash: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
