"""
策略运行与信号表（方案 6.4 D3）
===============================
- stg_run    每次运行一行：run_id、参数快照、universe、耗时、数据版本
- stg_signal 信号明细：run_id + code，含排名与指标明细

**没有 run 记录的策略结果不可复现** —— 因此任何选股结果都必须先落 run。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, PrimaryKeyConstraint,
    String, Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class StgRun(Base):
    """策略运行元信息。"""

    __tablename__ = "stg_run"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    algo_code: Mapped[str] = mapped_column(String(16), nullable=False)
    algo_name: Mapped[str | None] = mapped_column(String(100))
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)

    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, comment="参数快照")
    universe_count: Mapped[int] = mapped_column(Integer, default=0, comment="候选池数量")
    signal_count: Mapped[int] = mapped_column(Integer, default=0)

    data_version: Mapped[str | None] = mapped_column(String(32), comment="数据版本(最新交易日+行数指纹)")
    is_backtest: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="success",
                                        comment="running/success/failed")
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("ix_stg_run_algo_date", "algo_code", "trade_date"),
        Index("ix_stg_run_created", "created_at"),
    )


class StgSignal(Base):
    """策略信号明细。"""

    __tablename__ = "stg_signal"
    __table_args__ = (
        PrimaryKeyConstraint("run_id", "code", name="pk_stg_signal"),
        Index("ix_stg_signal_run_rank", "run_id", "rank"),
    )

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("stg_run.run_id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(6), nullable=False)
    name: Mapped[str | None] = mapped_column(String(64))
    rank: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[float | None] = mapped_column(Numeric(12, 4))
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="信号依据：各指标值")


class StgSignalPerf(Base):
    """信号前瞻收益（V6 T5，**仅供事后统计**）。

    一行 = 某个策略在 T 日的一条信号，T+1 开盘买入后 5/10/20 日的前瞻收益。
    只允许写入 `stg_run.is_backtest IS FALSE` 的信号；`mature_h` 表示第 H 个
    交易日窗口是否已经走完，`fillable` 表示 T+1 是否能真实买入（一字板/停牌为
    false，统计时必须整体排除，否则天梯会高估）。
    """

    __tablename__ = "stg_signal_perf"
    __table_args__ = (
        PrimaryKeyConstraint("algo_code", "signal_date", "code", name="pk_stg_signal_perf"),
        Index("ix_stg_signal_perf_algo_date", "algo_code", "signal_date"),
        Index(
            "ix_stg_signal_perf_algo_mature",
            "algo_code", "mature_20", "signal_date",
        ),
    )

    algo_code: Mapped[str] = mapped_column(String(16), comment="策略代码")
    signal_date: Mapped[date] = mapped_column(Date, comment="信号日 T（run.trade_date）")
    code: Mapped[str] = mapped_column(String(6))

    rank: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[float | None] = mapped_column(Numeric(12, 4))

    entry_date: Mapped[date | None] = mapped_column(Date, comment="T+1 交易日")
    entry_price: Mapped[float | None] = mapped_column(
        Numeric(16, 6), comment="T+1 开盘价 × adj_factor"
    )

    ret_5: Mapped[float | None] = mapped_column(Numeric(10, 4), comment="5 日前瞻收益 %")
    ret_10: Mapped[float | None] = mapped_column(Numeric(10, 4), comment="10 日前瞻收益 %")
    ret_20: Mapped[float | None] = mapped_column(Numeric(10, 4), comment="20 日前瞻收益 %")
    excess_5: Mapped[float | None] = mapped_column(Numeric(10, 4), comment="相对沪深300 超额 %")
    excess_10: Mapped[float | None] = mapped_column(Numeric(10, 4), comment="相对沪深300 超额 %")
    excess_20: Mapped[float | None] = mapped_column(Numeric(10, 4), comment="相对沪深300 超额 %")

    mature_5: Mapped[bool] = mapped_column(Boolean, default=False)
    mature_10: Mapped[bool] = mapped_column(Boolean, default=False)
    mature_20: Mapped[bool] = mapped_column(Boolean, default=False)
    fillable: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="T+1 可成交（非一字涨停/非停牌且有开盘价）"
    )
    industry_l1: Mapped[str | None] = mapped_column(String(64))
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BacktestRun(Base):
    """回测结果（方案 D4/D5）。净值曲线与逐笔成交以 JSONB 存储，便于导出 CSV。"""

    __tablename__ = "backtest_run"

    bt_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    algo_code: Mapped[str] = mapped_column(String(16), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)

    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="策略参数 + 回测参数快照")
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="绩效指标")
    equity_curve: Mapped[list[dict] | None] = mapped_column(JSONB, comment="[{date, nav, benchmark}]")
    trades: Mapped[list[dict] | None] = mapped_column(JSONB, comment="逐笔成交")
    status: Mapped[str] = mapped_column(String(16), default="success")
    error: Mapped[str | None] = mapped_column(Text)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("ix_backtest_run_algo", "algo_code", "created_at"),
    )


__all__ = ["StgRun", "StgSignal", "StgSignalPerf", "BacktestRun"]
