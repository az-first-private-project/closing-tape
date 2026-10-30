"""
事实表 ORM（方案 6.2 / 6.4）
============================
- fact_stock_daily    个股日线（**按年 RANGE 分区**，5 年约 650 万行）
- fact_sector_daily   板块日线（替代旧 sector_daily）
- fact_feature_daily  预计算技术指标（选股秒级的关键）
- fact_stock_score_daily 个股因子评分（A3 与个股详情雷达图用）
- fact_moneyflow      个股资金流（情绪/资金因子输入）
- market_indices      指数日线
- margin_trading      两融 + 北向（北向可能为空，非阻断）
- sector_score        板块三面评分 + 龙头

单位约定（硬约束，方案 6.2）：
  volume = 股；amount = 元；市值/成交额统一到元；比率类用百分数（%）。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, Index, Integer, Numeric, PrimaryKeyConstraint,
    String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class FactStockDaily(Base):
    """个股日线（核心事实表，按 trade_date 年分区）。

    注意：分区表的主键**必须包含分区键**，所以 PK 是 (code, trade_date)，
    与方案 6.2 一致。分区由 `app/db/views.py:ensure_partitions()` 维护。
    """

    __tablename__ = "fact_stock_daily"
    __table_args__ = (
        PrimaryKeyConstraint("code", "trade_date", name="pk_fact_stock_daily"),
        Index("ix_fact_stock_daily_trade_date", "trade_date"),
        {"postgresql_partition_by": "RANGE (trade_date)"},
    )

    code: Mapped[str] = mapped_column(String(6))
    trade_date: Mapped[date] = mapped_column(Date)

    open: Mapped[float | None] = mapped_column(Numeric(12, 3))
    high: Mapped[float | None] = mapped_column(Numeric(12, 3))
    low: Mapped[float | None] = mapped_column(Numeric(12, 3))
    close: Mapped[float | None] = mapped_column(Numeric(12, 3), comment="未复权原始价")
    pre_close: Mapped[float | None] = mapped_column(Numeric(12, 3), comment="前收盘")

    pct_chg: Mapped[float | None] = mapped_column(Numeric(8, 3), comment="涨跌幅 %")
    amplitude: Mapped[float | None] = mapped_column(Numeric(8, 3), comment="振幅 %")
    volume: Mapped[int | None] = mapped_column(BigInteger, comment="成交量(股)")
    amount: Mapped[float | None] = mapped_column(Numeric(20, 2), comment="成交额(元)")
    turnover_rate: Mapped[float | None] = mapped_column(Numeric(10, 4), comment="换手率 %")

    # 回测正确性全靠它：缺 adj_factor 所有收益率都是错的
    adj_factor: Mapped[float | None] = mapped_column(
        Numeric(16, 6), default=1.0, comment="复权因子（后复权）"
    )

    market_cap: Mapped[float | None] = mapped_column(Numeric(20, 2), comment="总市值(元)")
    float_cap: Mapped[float | None] = mapped_column(Numeric(20, 2), comment="流通市值(元)")

    pe_ttm: Mapped[float | None] = mapped_column(Numeric(16, 4))
    pb: Mapped[float | None] = mapped_column(Numeric(16, 4))
    ps_ttm: Mapped[float | None] = mapped_column(Numeric(16, 4))
    dv_ttm: Mapped[float | None] = mapped_column(Numeric(10, 4), comment="股息率 %")

    # 回测「不可成交」判定依据
    is_limit_up: Mapped[bool] = mapped_column(Boolean, default=False)
    is_limit_down: Mapped[bool] = mapped_column(Boolean, default=False)
    is_suspended: Mapped[bool] = mapped_column(Boolean, default=False)

    src: Mapped[str] = mapped_column(String(16), default="akshare")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    def __repr__(self) -> str:
        return f"<FactStockDaily {self.code} {self.trade_date} close={self.close}>"


class FactSectorDaily(Base):
    """板块日线（替代旧 `sector_daily`，命名统一到 fact_*）。"""

    __tablename__ = "fact_sector_daily"
    __table_args__ = (
        PrimaryKeyConstraint("sector_code", "trade_date", name="pk_fact_sector_daily"),
        Index("ix_fact_sector_daily_trade_date", "trade_date"),
    )

    sector_code: Mapped[str] = mapped_column(String(24))
    trade_date: Mapped[date] = mapped_column(Date)
    sector_name: Mapped[str] = mapped_column(String(64), nullable=False)
    sector_type: Mapped[str] = mapped_column(String(16), default="concept")

    open: Mapped[float | None] = mapped_column(Numeric(12, 3))
    high: Mapped[float | None] = mapped_column(Numeric(12, 3))
    low: Mapped[float | None] = mapped_column(Numeric(12, 3))
    close: Mapped[float | None] = mapped_column(Numeric(12, 3))
    pre_close: Mapped[float | None] = mapped_column(Numeric(12, 3))

    pct_chg: Mapped[float | None] = mapped_column(Numeric(8, 3))
    amplitude: Mapped[float | None] = mapped_column(Numeric(8, 3))
    volume: Mapped[int | None] = mapped_column(BigInteger, comment="成交量(股)")
    amount: Mapped[float | None] = mapped_column(Numeric(20, 2), comment="成交额(元)")
    turnover_rate: Mapped[float | None] = mapped_column(Numeric(10, 4))

    up_count: Mapped[int | None] = mapped_column(Integer)
    down_count: Mapped[int | None] = mapped_column(Integer)
    leading_stock: Mapped[str | None] = mapped_column(String(64))
    leading_stock_pct: Mapped[float | None] = mapped_column(Numeric(8, 3))

    # V6 T4：按当日 SCD2 名单聚合的成分股指标（成分数<3 时市值/资金流置 NULL）
    market_cap: Mapped[float | None] = mapped_column(
        Numeric(24, 2), comment="板块成分股总市值合计(元)"
    )
    float_cap: Mapped[float | None] = mapped_column(
        Numeric(24, 2), comment="板块成分股流通市值合计(元)"
    )
    main_net_inflow: Mapped[float | None] = mapped_column(
        Numeric(24, 2), comment="成分股主力净流入合计(元)"
    )
    rs_20d: Mapped[float | None] = mapped_column(
        Numeric(8, 3), comment="20 交易日相对沪深300超额收益(百分点)"
    )
    member_count: Mapped[int | None] = mapped_column(
        Integer, comment="当日 SCD2 名单成分数"
    )

    src: Mapped[str] = mapped_column(String(16), default="akshare")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class FactFeatureDaily(Base):
    """预计算技术指标（方案 C1）。

    只放**指标**，不放评分：评分依赖横截面（z-score），在 fact_stock_score_daily。
    """

    __tablename__ = "fact_feature_daily"
    __table_args__ = (
        PrimaryKeyConstraint("code", "trade_date", name="pk_fact_feature_daily"),
        Index("ix_fact_feature_daily_trade_date", "trade_date"),
    )

    code: Mapped[str] = mapped_column(String(6))
    trade_date: Mapped[date] = mapped_column(Date)

    ma5: Mapped[float | None] = mapped_column(Numeric(12, 4))
    ma10: Mapped[float | None] = mapped_column(Numeric(12, 4))
    ma20: Mapped[float | None] = mapped_column(Numeric(12, 4))
    ma60: Mapped[float | None] = mapped_column(Numeric(12, 4))
    rsi14: Mapped[float | None] = mapped_column(Numeric(8, 4))
    macd_dif: Mapped[float | None] = mapped_column(Numeric(12, 6))
    macd_dea: Mapped[float | None] = mapped_column(Numeric(12, 6))
    macd_hist: Mapped[float | None] = mapped_column(Numeric(12, 6))
    macd_golden_cross: Mapped[bool] = mapped_column(Boolean, default=False, comment="当日金叉")
    boll_mid: Mapped[float | None] = mapped_column(Numeric(12, 4))
    boll_upper: Mapped[float | None] = mapped_column(Numeric(12, 4))
    boll_lower: Mapped[float | None] = mapped_column(Numeric(12, 4))
    atr14: Mapped[float | None] = mapped_column(Numeric(12, 4))

    momentum_5: Mapped[float | None] = mapped_column(Numeric(10, 4), comment="5日动量 %")
    momentum_20: Mapped[float | None] = mapped_column(Numeric(10, 4))
    momentum_60: Mapped[float | None] = mapped_column(Numeric(10, 4))
    volatility_20: Mapped[float | None] = mapped_column(Numeric(10, 4), comment="20日年化波动 %")
    volume_ratio: Mapped[float | None] = mapped_column(Numeric(10, 4), comment="量比(5/20)")
    amount_ma20: Mapped[float | None] = mapped_column(Numeric(20, 2), comment="20日均成交额(元)")
    ma_bullish: Mapped[bool] = mapped_column(Boolean, default=False, comment="MA5>MA10>MA20")

    # ---- 短线策略指标（B1–B5）----
    ema5: Mapped[float | None] = mapped_column(Numeric(12, 4))
    ema20: Mapped[float | None] = mapped_column(Numeric(12, 4))
    ema60: Mapped[float | None] = mapped_column(Numeric(12, 4))
    ma120: Mapped[float | None] = mapped_column(Numeric(12, 4))
    ema20_up: Mapped[bool] = mapped_column(Boolean, default=False, comment="EMA20 较前一日上行")
    ema_golden_cross: Mapped[bool] = mapped_column(Boolean, default=False, comment="EMA5 上穿 EMA20")
    ema_cross_3d: Mapped[bool] = mapped_column(Boolean, default=False, comment="近 3 日内 EMA 金叉")
    rsi6: Mapped[float | None] = mapped_column(Numeric(8, 4))
    roc10: Mapped[float | None] = mapped_column(Numeric(10, 4), comment="10日动量 %")
    kdj_k: Mapped[float | None] = mapped_column(Numeric(8, 4))
    kdj_d: Mapped[float | None] = mapped_column(Numeric(8, 4))
    kdj_j: Mapped[float | None] = mapped_column(Numeric(8, 4))
    kdj_j_min2: Mapped[float | None] = mapped_column(
        Numeric(8, 4), comment="J 值近两日最小值（当日/前一日）"
    )
    kdj_golden_cross: Mapped[bool] = mapped_column(Boolean, default=False, comment="K 上穿 D")
    boll_width: Mapped[float | None] = mapped_column(Numeric(10, 4), comment="布林带宽 %")
    boll_width_pct60: Mapped[float | None] = mapped_column(
        Numeric(8, 4), comment="带宽在近60日的百分位"
    )
    high20_close: Mapped[float | None] = mapped_column(
        Numeric(12, 4), comment="前20日最高收盘价"
    )
    range20_pct: Mapped[float | None] = mapped_column(
        Numeric(10, 4), comment="前20日收盘区间宽度 %"
    )
    amp60: Mapped[float | None] = mapped_column(Numeric(10, 4), comment="60日振幅 %")
    vol_ma5: Mapped[float | None] = mapped_column(Numeric(20, 2), comment="5日均量(股)")

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class FactStockScoreDaily(Base):
    """个股因子评分（C2/C3/C4）。0-10 分制，`*_z` 为行业内标准化后的值。"""

    __tablename__ = "fact_stock_score_daily"
    __table_args__ = (
        PrimaryKeyConstraint("code", "trade_date", name="pk_fact_stock_score_daily"),
        Index("ix_fact_stock_score_daily_trade_date_score", "trade_date", "composite_score"),
    )

    code: Mapped[str] = mapped_column(String(6))
    trade_date: Mapped[date] = mapped_column(Date)

    value_score: Mapped[float | None] = mapped_column(Numeric(6, 3), comment="价值")
    quality_score: Mapped[float | None] = mapped_column(Numeric(6, 3), comment="质量")
    momentum_score: Mapped[float | None] = mapped_column(Numeric(6, 3), comment="动量")
    volatility_score: Mapped[float | None] = mapped_column(Numeric(6, 3), comment="波动")
    liquidity_score: Mapped[float | None] = mapped_column(Numeric(6, 3), comment="流动性")
    sentiment_score: Mapped[float | None] = mapped_column(Numeric(6, 3), comment="情绪")
    composite_score: Mapped[float | None] = mapped_column(Numeric(6, 3), comment="综合")

    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="原始因子与分位")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class FactMoneyflow(Base):
    """个股资金流（情绪/资金因子输入）。金额单位：元；同时保留万元口径原始值便于对账。"""

    __tablename__ = "fact_moneyflow"
    __table_args__ = (
        PrimaryKeyConstraint("code", "trade_date", name="pk_fact_moneyflow"),
        Index("ix_fact_moneyflow_trade_date", "trade_date"),
    )

    code: Mapped[str] = mapped_column(String(6))
    trade_date: Mapped[date] = mapped_column(Date)

    main_net_t0: Mapped[float | None] = mapped_column(Numeric(20, 2), comment="当日主力净流入(元)")
    main_net_t1: Mapped[float | None] = mapped_column(Numeric(20, 2))
    main_net_t2: Mapped[float | None] = mapped_column(Numeric(20, 2))
    main_net_t3: Mapped[float | None] = mapped_column(Numeric(20, 2))
    main_net_t4: Mapped[float | None] = mapped_column(Numeric(20, 2))
    main_net_5d: Mapped[float | None] = mapped_column(Numeric(20, 2), comment="近5日合计(元)")

    north_hold_ratio: Mapped[float | None] = mapped_column(Numeric(10, 4), comment="北向持股比例 %")
    src: Mapped[str] = mapped_column(String(16), default="akshare")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class MarketIndex(Base):
    """指数日线（A 股四指数 + 沪深300 基准 + 可选美股）。"""

    __tablename__ = "market_indices"
    __table_args__ = (
        PrimaryKeyConstraint("index_code", "trade_date", name="pk_market_indices"),
        Index("ix_market_indices_trade_date", "trade_date"),
    )

    index_code: Mapped[str] = mapped_column(String(16))
    trade_date: Mapped[date] = mapped_column(Date)
    index_name: Mapped[str] = mapped_column(String(50), nullable=False)

    open: Mapped[float | None] = mapped_column(Numeric(14, 3))
    high: Mapped[float | None] = mapped_column(Numeric(14, 3))
    low: Mapped[float | None] = mapped_column(Numeric(14, 3))
    close: Mapped[float | None] = mapped_column(Numeric(14, 3))
    pre_close: Mapped[float | None] = mapped_column(Numeric(14, 3))
    pct_chg: Mapped[float | None] = mapped_column(Numeric(8, 3))
    volume: Mapped[int | None] = mapped_column(BigInteger)
    amount: Mapped[float | None] = mapped_column(Numeric(20, 2))

    src: Mapped[str] = mapped_column(String(16), default="akshare")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class MarginTrading(Base):
    """两融 + 北向（市场级）。北向自 2024-08 起可能长期为空，属于预期内。"""

    __tablename__ = "margin_trading"

    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    margin_balance: Mapped[float | None] = mapped_column(Numeric(16, 2), comment="融资余额(亿元)")
    short_balance: Mapped[float | None] = mapped_column(Numeric(16, 2), comment="融券余额(亿元)")
    margin_change: Mapped[float | None] = mapped_column(Numeric(16, 2), comment="日变化(亿元)")
    northward_net: Mapped[float | None] = mapped_column(Numeric(16, 2), comment="北向净流入(亿元)")

    src: Mapped[str] = mapped_column(String(16), default="akshare")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class SectorScore(Base):
    """板块三面评分 + 两类龙头（不再存 ai_summary，日报见 daily_report）。"""

    __tablename__ = "sector_score"
    __table_args__ = (
        PrimaryKeyConstraint("trade_date", "sector_code", name="pk_sector_score"),
        Index("ix_sector_score_trade_date_composite", "trade_date", "composite_score"),
    )

    trade_date: Mapped[date] = mapped_column(Date)
    sector_code: Mapped[str] = mapped_column(String(24))
    sector_name: Mapped[str | None] = mapped_column(String(64))

    technical_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    sentiment_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    news_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    composite_score: Mapped[float | None] = mapped_column(Numeric(5, 2))

    strength_leaders: Mapped[list[dict] | None] = mapped_column(JSONB)
    sentiment_leaders: Mapped[list[dict] | None] = mapped_column(JSONB)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="子项得分与数据源")

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class MarketTemperatureDaily(Base):
    """市场温度计每日快照（V6 T3）。

    `components` 保存六个子项的原值/归一分/权重/说明，便于前端解释温度分；
    `confidence` 为 ok / partial / insufficient，缺失子项一律存 NULL 而不是 0。
    """

    __tablename__ = "market_temperature_daily"

    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)

    up_count: Mapped[int | None] = mapped_column(Integer)
    down_count: Mapped[int | None] = mapped_column(Integer)
    flat_count: Mapped[int | None] = mapped_column(Integer)
    total_count: Mapped[int | None] = mapped_column(Integer, comment="有效交易家数（剔除停牌）")
    limit_up_count: Mapped[int | None] = mapped_column(Integer)
    limit_down_count: Mapped[int | None] = mapped_column(Integer)
    median_pct: Mapped[float | None] = mapped_column(Numeric(8, 3), comment="涨跌幅中位数 %")

    above_ma20_ratio: Mapped[float | None] = mapped_column(
        Numeric(6, 4), comment="站上 MA20 比例（0–1 小数，与 coverage 同口径）"
    )
    amount: Mapped[float | None] = mapped_column(Numeric(24, 2), comment="全市场成交额(元)")
    amount_vs_ma20: Mapped[float | None] = mapped_column(
        Numeric(8, 3), comment="量能比 = 当日成交额 / 前 20 交易日均值（倍）"
    )
    index_vol_annual: Mapped[float | None] = mapped_column(
        Numeric(8, 3), comment="沪深300 20日年化波动率 %"
    )

    temp_score: Mapped[float | None] = mapped_column(Numeric(5, 1), comment="0–100 温度分")
    # 任务书原文为 VARCHAR(8)，但枚举值 insufficient 有 12 个字符，按枚举放宽到 16
    confidence: Mapped[str] = mapped_column(
        String(16), default="insufficient", comment="ok / partial / insufficient"
    )
    components: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="子项原值/得分/权重/说明 + available_count"
    )

    src: Mapped[str] = mapped_column(String(16), default="engine")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class DailyReport(Base):
    """AI 复盘日报（独立表，终止 sector_score.ai_summary hack）。"""

    __tablename__ = "daily_report"

    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="Markdown 正文")
    structured: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="结构化输出：结论/依据/风险"
    )
    model: Mapped[str | None] = mapped_column(String(64))
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False, comment="LLM 不可用时的模板报告")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


__all__ = [
    "FactStockDaily", "FactSectorDaily", "FactFeatureDaily", "FactStockScoreDaily",
    "FactMoneyflow", "MarketIndex", "MarginTrading", "SectorScore",
    "MarketTemperatureDaily", "DailyReport",
]
