"""
元数据 / 治理表（方案 6.4）
===========================
- trade_calendar   真实交易日历（替代 weekday()<5）
- meta_data_quality 每个数据集的最后成功时间/行数/覆盖率/异常数 —— 前端新鲜度的唯一来源
- ingest_job       采集任务运行记录（断点续传 + 任务监控页 E5）
- algo_meta        策略元信息（前端卡片）
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean, Date, DateTime, Index, Integer, Numeric, SmallInteger, String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class TradeCalendar(Base):
    """交易日历。is_open=False 的行也会存在（用于区分"休市"与"没数据"）。"""

    __tablename__ = "trade_calendar"

    cal_date: Mapped[date] = mapped_column(Date, primary_key=True)
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="akshare")

    __table_args__ = (Index("ix_trade_calendar_is_open", "is_open"),)


class MetaDataQuality(Base):
    """数据集健康度。**前端数据新鲜度徽标的唯一数据源。**"""

    __tablename__ = "meta_data_quality"

    dataset: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(64))

    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_trade_date: Mapped[date | None] = mapped_column(Date)

    rows: Mapped[int | None] = mapped_column(Integer, comment="最近一次成功写入行数")
    coverage: Mapped[float | None] = mapped_column(Numeric(6, 4), comment="覆盖率 0-1")
    anomalies: Mapped[int | None] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="unknown",
                                        comment="ok/stale/quality_failed/error/unknown")
    error: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class IngestJob(Base):
    """采集任务运行记录。用于断点续传与任务监控页。"""

    __tablename__ = "ingest_job"

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task: Mapped[str] = mapped_column(String(64), nullable=False, comment="任务名，如 backfill_daily")
    dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    trade_date: Mapped[date | None] = mapped_column(Date, index=True)
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    status: Mapped[str] = mapped_column(String(16), default="running",
                                        comment="running/success/failed/partial/skipped")
    total: Mapped[int | None] = mapped_column(Integer)
    done: Mapped[int | None] = mapped_column(Integer, default=0)
    failed: Mapped[int | None] = mapped_column(Integer, default=0)
    rows_written: Mapped[int | None] = mapped_column(Integer, default=0)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    # 断点：已完成/失败的代码清单，重跑时跳过
    checkpoint: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("ix_ingest_job_task_started", "task", "started_at"),
        Index("ix_ingest_job_status", "status"),
    )


class AlgoMeta(Base):
    """策略元信息台账（A3/A4 + B1–B5）；/algo/list 实际由 strategy/strategies.py 的 SPECS 提供。"""

    __tablename__ = "algo_meta"

    algo_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    algo_name: Mapped[str] = mapped_column(String(100), nullable=False)
    algo_type: Mapped[str] = mapped_column(String(20), nullable=False,
                                           comment="technical/fund/score/value")
    short_desc: Mapped[str | None] = mapped_column(String(200))
    full_desc: Mapped[str | None] = mapped_column(Text)
    params_schema: Mapped[list[dict] | None] = mapped_column(JSONB)
    sort_order: Mapped[int] = mapped_column(SmallInteger, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


__all__ = ["TradeCalendar", "MetaDataQuality", "IngestJob", "AlgoMeta"]
