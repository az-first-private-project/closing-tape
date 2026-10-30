"""
维度表 ORM（方案 6.1 / 6.3）
============================
- dim_stock          标的维度：含退市、ST、板块、行业、所有制、股本
- dim_sector_member  板块成分 SCD2：带 effective_from/effective_to，避免回测用到未来成分
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean, Date, DateTime, Index, Numeric, SmallInteger, String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class DimStock(Base):
    """标的维度表。

    **退市标的必须保留**（delist_date 非空），否则 5 年回测会有幸存者偏差。
    """

    __tablename__ = "dim_stock"

    code: Mapped[str] = mapped_column(String(6), primary_key=True, comment="6 位代码")
    exchange: Mapped[str] = mapped_column(String(4), nullable=False, comment="SH/SZ/BJ")
    board: Mapped[str] = mapped_column(String(8), nullable=False, comment="主板/创业板/科创板/北交所")
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    industry_l1: Mapped[str | None] = mapped_column(String(64), comment="申万一级")
    industry_l2: Mapped[str | None] = mapped_column(String(64), comment="申万二级")
    ownership: Mapped[str] = mapped_column(String(16), default="未知", comment="央企/地方国企/民企/外资")

    list_date: Mapped[date | None] = mapped_column(Date)
    delist_date: Mapped[date | None] = mapped_column(Date, index=True)
    is_st: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[int] = mapped_column(
        SmallInteger, default=1, comment="1=正常 2=停牌 9=退市"
    )

    total_share: Mapped[float | None] = mapped_column(Numeric(20, 4), comment="总股本(股)")
    float_share: Mapped[float | None] = mapped_column(Numeric(20, 4), comment="流通股本(股)")

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        Index("ix_dim_stock_industry_l1", "industry_l1"),
        Index("ix_dim_stock_ownership", "ownership"),
        Index("ix_dim_stock_status", "status", "is_st"),
    )

    def __repr__(self) -> str:
        return f"<DimStock {self.code} {self.name}>"


class DimSectorMember(Base):
    """板块成分表（SCD2 缓慢变化维）。

    一行 = 某成分股在某板块的**一段有效区间**。
    - effective_to 为 NULL 表示"至今仍是成分"
    - 回测取成分必须带 `effective_from <= d AND (effective_to IS NULL OR effective_to > d)`
    """

    __tablename__ = "dim_sector_member"

    sector_code: Mapped[str] = mapped_column(String(24), primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(6), primary_key=True)
    effective_from: Mapped[date] = mapped_column(Date, primary_key=True)
    effective_to: Mapped[date | None] = mapped_column(Date, index=True)

    sector_name: Mapped[str] = mapped_column(String(64), nullable=False)
    sector_type: Mapped[str] = mapped_column(String(16), default="concept")
    is_partial: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false",
        comment="名单仅覆盖 THS 前后各 5 页（pages>10），不参与市值/家数聚合",
    )

    __table_args__ = (
        Index("ix_dim_sector_member_stock", "stock_code"),
        Index("ix_dim_sector_member_sector", "sector_code", "effective_from"),
    )

    def __repr__(self) -> str:
        return f"<DimSectorMember {self.sector_code}/{self.stock_code} {self.effective_from}>"


class DimStockFinancial(Base):
    """财报关键指标（估值因子的事实源，季度更新）。

    为什么单独建表：PE = 收盘价 / EPS_TTM，只有 EPS 随财报变（季度），价格每天变。
    把 EPS/BPS/分红率缓存进库后，**每日估值计算完全不需要联网**；
    否则每天要为 5000+ 只股票各打一次接口（约 45 分钟），日报流水线根本跑不完。

    `report_date` 是报告期（如 2024-03-31）；`announced_date` 是**市场可知日期**
    （用 报告期 + REPORT_LAG_DAYS 保守估计），估值展开到日线时用它做 point-in-time，
    避免用"未来的财报"算"过去的 PE"（未来函数）。
    """

    __tablename__ = "dim_stock_financial"
    __table_args__ = (
        Index("ix_dim_stock_financial_report", "code", "report_date"),
    )

    code: Mapped[str] = mapped_column(String(6), primary_key=True)
    report_date: Mapped[date] = mapped_column(Date, primary_key=True)

    announced_date: Mapped[date | None] = mapped_column(Date, comment="市场可知日期(报告期+滞后)")
    eps: Mapped[float | None] = mapped_column(Numeric(14, 6), comment="累计每股收益(元)")
    bps: Mapped[float | None] = mapped_column(Numeric(14, 6), comment="每股净资产(元)")
    roe: Mapped[float | None] = mapped_column(Numeric(10, 4), comment="净资产收益率 %")
    payout_ratio: Mapped[float | None] = mapped_column(Numeric(10, 4), comment="股息发放率 %")
    eps_ttm: Mapped[float | None] = mapped_column(Numeric(14, 6), comment="TTM 每股收益(元)")
    revenue: Mapped[float | None] = mapped_column(Numeric(20, 2), comment="营业总收入(元)")

    src: Mapped[str] = mapped_column(String(16), default="sina")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    def __repr__(self) -> str:
        return f"<DimStockFinancial {self.code} {self.report_date}>"


class DimStockDividend(Base):
    """现金分红明细（A4 高股息策略的事实源）。

    为什么需要它：新浪财报里的 `股息发放率(%)` 大量为空，且它是"分红/净利润"的比例，
    不是股息率。真正的股息率 = 近 12 个月每股现金分红 / 当前股价，
    必须按**除权日**（ex_date）落库，才能做到 point-in-time（不能用未来分红算过去股息率）。

    来源：巨潮 `ak.stock_dividend_cninfo(symbol)`，其中 `派息比例` 的单位是
    **每 10 股派 X 元**，入库时统一换算为 `dps`（每股，元）。
    """

    __tablename__ = "dim_stock_dividend"
    __table_args__ = (
        Index("ix_dim_stock_dividend_ex", "code", "ex_date"),
    )

    code: Mapped[str] = mapped_column(String(6), primary_key=True)
    ex_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="除权日=市场可知日")
    dps: Mapped[float | None] = mapped_column(Numeric(12, 6), comment="每股现金分红(元，含税)")
    announce_date: Mapped[date | None] = mapped_column(Date, comment="实施方案公告日期")
    report_period: Mapped[str | None] = mapped_column(String(16), comment="报告时间，如 2024年报")
    src: Mapped[str] = mapped_column(String(16), default="cninfo")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    def __repr__(self) -> str:
        return f"<DimStockDividend {self.code} {self.ex_date} dps={self.dps}>"


__all__ = ["DimStock", "DimSectorMember", "DimStockFinancial", "DimStockDividend"]
