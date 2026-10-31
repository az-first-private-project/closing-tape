"""
应用状态表（V6 T8）
====================
项目没有账号体系，自选名单与已读水位按**单租户单行**存储：
同一后端下所有浏览器共享一份状态，避免 localStorage 被清理/隐私模式导致丢失。

表内只有一行（id=1），`codes` / `seen_hits` 均为 JSONB 数组；
写入走 `PUT /api/v1/watchlist` 整份覆盖，原因为单用户场景下最后一写生效即可。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, SmallInteger, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class WatchlistState(Base):
    """单租户自选状态（单行）。"""

    __tablename__ = "app_watchlist_state"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    codes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    seen_hits: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_app_watchlist_state_singleton"),
    )


__all__ = ["WatchlistState"]
