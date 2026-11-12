"""市场状态与配置仪表盘（V7 T1）

Revision ID: 0011_market_regime
Revises: 0010_stg_run_signal_schema
Create Date: 2026-11-12

新增：
  - macro_rates：中债 1Y/10Y 国债收益率（ERP / 期限利差的输入）
  - fact_market_daily：市场级原始因子日快照
  - market_regime_daily：状态机 + 配置建议日快照
  - margin_trading.margin_buy_amount：沪深融资买入额合计（元）

迁移只增不删；全部幂等，fresh DB 与已有库均可执行。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0011_market_regime"
down_revision: str | None = "0010_stg_run_signal_schema"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_table("macro_rates"):
        op.create_table(
            "macro_rates",
            sa.Column("trade_date", sa.Date(), primary_key=True),
            sa.Column("cn_1y", sa.Numeric(8, 4), comment="1 年期国债收益率 %"),
            sa.Column("cn_10y", sa.Numeric(8, 4), comment="10 年期国债收益率 %"),
            sa.Column("term_spread", sa.Numeric(8, 4), comment="10Y-1Y 百分点"),
            sa.Column("src", sa.String(16), nullable=False, server_default="chinabond"),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True), nullable=False,
                server_default=sa.text("now()"),
            ),
        )

    if not _has_table("fact_market_daily"):
        op.create_table(
            "fact_market_daily",
            sa.Column("trade_date", sa.Date(), primary_key=True),
            sa.Column("index_code", sa.String(16), nullable=False, server_default="sh000300"),
            sa.Column("close", sa.Numeric(14, 3)),
            sa.Column("ret_1d", sa.Numeric(10, 4), comment="%"),
            sa.Column("ret_20", sa.Numeric(10, 4), comment="%"),
            sa.Column("ema20", sa.Numeric(14, 4)),
            sa.Column("ema60", sa.Numeric(14, 4)),
            sa.Column("ema120", sa.Numeric(14, 4)),
            sa.Column("ema_slope60", sa.Numeric(12, 8), comment="EMA60 20日斜率/close"),
            sa.Column("ma_alignment", sa.SmallInteger(), comment="+1/-1/0"),
            sa.Column("adx14", sa.Numeric(10, 4)),
            sa.Column("di_plus", sa.Numeric(10, 4)),
            sa.Column("di_minus", sa.Numeric(10, 4)),
            sa.Column("efficiency_ratio", sa.Numeric(10, 4)),
            sa.Column("choppiness", sa.Numeric(10, 4)),
            sa.Column("macd_hist", sa.Numeric(14, 6)),
            sa.Column("rsi14", sa.Numeric(10, 4)),
            sa.Column("price_vs_ma120", sa.Numeric(10, 6)),
            sa.Column("amount", sa.Numeric(24, 2), comment="全市场成交额(元)"),
            sa.Column("amount_ma20", sa.Numeric(24, 2)),
            sa.Column("amount_vs_ma20", sa.Numeric(8, 4)),
            sa.Column("market_turnover", sa.Numeric(8, 4), comment="Σ成交额/Σ流通市值 %"),
            sa.Column("turnover_ma5", sa.Numeric(8, 4)),
            sa.Column("volume_price_match", sa.Numeric(6, 4), comment="0–1"),
            sa.Column(
                "industry_heat_concentration", sa.Numeric(6, 4),
                comment="前5行业成交额占比",
            ),
            sa.Column("capital_efficiency", sa.Numeric(12, 6), comment="单位成交额推动涨幅"),
            sa.Column("up_count", sa.Integer()),
            sa.Column("down_count", sa.Integer()),
            sa.Column("flat_count", sa.Integer()),
            sa.Column("total_count", sa.Integer()),
            sa.Column("limit_up_cnt", sa.Integer()),
            sa.Column("limit_down_cnt", sa.Integer()),
            sa.Column("adv_dec_ratio", sa.Numeric(6, 4)),
            sa.Column("above_ma30_ratio", sa.Numeric(6, 4)),
            sa.Column("new_high_60_ratio", sa.Numeric(6, 4)),
            sa.Column("broken_net_ratio", sa.Numeric(6, 4)),
            sa.Column("dividend_yield", sa.Numeric(8, 4), comment="流通市值加权股息率 %"),
            sa.Column("earnings_yield", sa.Numeric(8, 4), comment="流通市值加权盈利收益率 %"),
            sa.Column("cn_1y", sa.Numeric(8, 4)),
            sa.Column("cn_10y", sa.Numeric(8, 4)),
            sa.Column("erp", sa.Numeric(8, 4), comment="盈利收益率-10Y"),
            sa.Column("bond_equity_gap", sa.Numeric(8, 4), comment="股息率-10Y"),
            sa.Column("term_spread", sa.Numeric(8, 4)),
            sa.Column(
                "defensive_rel_momentum", sa.Numeric(10, 4),
                comment="防御篮子20日超额 %",
            ),
            sa.Column("margin_buy_amount", sa.Numeric(24, 2), comment="沪深融资买入额合计(元)"),
            sa.Column("margin_buy_ratio", sa.Numeric(8, 6)),
            sa.Column("rv20", sa.Numeric(10, 4), comment="20日年化波动 %"),
            sa.Column("rv60", sa.Numeric(10, 4), comment="60日年化波动 %"),
            sa.Column("natr14", sa.Numeric(10, 4), comment="ATR14/close %"),
            sa.Column("boll_bandwidth", sa.Numeric(10, 4), comment="(上轨-下轨)/中轨 %"),
            sa.Column("max_drawdown_3m", sa.Numeric(10, 4), comment="近60日最大回撤 %"),
            sa.Column("data_quality", sa.String(16), nullable=False, server_default="full"),
            sa.Column("src", sa.String(16), nullable=False, server_default="engine"),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True), nullable=False,
                server_default=sa.text("now()"),
            ),
        )

    if not _has_table("market_regime_daily"):
        op.create_table(
            "market_regime_daily",
            sa.Column("trade_date", sa.Date(), primary_key=True),
            sa.Column("regime_code", sa.String(16), nullable=False),
            sa.Column("regime_label", sa.String(16), nullable=False),
            sa.Column("regime_since", sa.Date(), nullable=False),
            sa.Column("regime_days", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("note", sa.Text()),
            sa.Column("headline", sa.String(120)),
            sa.Column("confidence_score", sa.SmallInteger()),
            sa.Column("confidence_level", sa.String(8)),
            sa.Column("optimism_score", sa.SmallInteger()),
            sa.Column("optimism_level", sa.String(8)),
            sa.Column("group_t", sa.Numeric(6, 4)),
            sa.Column("group_m", sa.Numeric(6, 4)),
            sa.Column("group_b", sa.Numeric(6, 4)),
            sa.Column("group_v", sa.Numeric(6, 4), comment="V_q 0–1"),
            sa.Column("group_q", sa.Numeric(6, 4)),
            sa.Column("group_r", sa.Numeric(6, 4)),
            sa.Column("factors", JSONB(), comment="因子 raw/pct/x/group"),
            sa.Column("allocation", JSONB()),
            sa.Column("evidence", JSONB()),
            sa.Column("completeness", sa.Numeric(5, 4)),
            sa.Column("data_quality", sa.String(16), nullable=False, server_default="full"),
            sa.Column("stale_fields", JSONB()),
            sa.Column("src", sa.String(16), nullable=False, server_default="engine"),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True), nullable=False,
                server_default=sa.text("now()"),
            ),
        )

    if _has_table("margin_trading") and not _has_column(
        "margin_trading", "margin_buy_amount"
    ):
        op.add_column(
            "margin_trading",
            sa.Column(
                "margin_buy_amount", sa.Numeric(24, 2),
                comment="沪深融资买入额合计(元)",
            ),
        )


def downgrade() -> None:
    if _has_table("margin_trading") and _has_column(
        "margin_trading", "margin_buy_amount"
    ):
        op.drop_column("margin_trading", "margin_buy_amount")
    if _has_table("market_regime_daily"):
        op.drop_table("market_regime_daily")
    if _has_table("fact_market_daily"):
        op.drop_table("fact_market_daily")
    if _has_table("macro_rates"):
        op.drop_table("macro_rates")
