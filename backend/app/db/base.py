"""
SQLAlchemy 声明性基类（V5）
==========================
新增：
  - 统一命名约定（索引/唯一约束/外键），否则 Alembic autogenerate 会产生随机名字导致迁移对不上；
  - `TimestampMixin` 统一 UTC 时间列（方案 6.2 要求 TIMESTAMPTZ）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    """带时区的当前时间。禁止使用 datetime.utcnow()（naive，坑过 V4）。"""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """created_at / updated_at 双列，统一带时区。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


__all__ = ["Base", "TimestampMixin", "utcnow", "NAMING_CONVENTION"]
