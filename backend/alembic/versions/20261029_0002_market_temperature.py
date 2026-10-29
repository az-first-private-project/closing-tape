"""市场温度计日表（V6 T3）

Revision ID: 0002_market_temperature
Revises: 0001_v5_baseline
Create Date: 2026-10-29

说明
----
`market_temperature_daily` 每日一行，由 `app/engine/market_temperature.py`
幂等 upsert。缺失子项存 NULL（包括 counts），不允许用 0 冒充；
`confidence` 区分 ok / partial / insufficient。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002_market_temperature"
down_revision: str | None = "0001_v5_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_temperature_daily",
        sa.Column("trade_date", sa.Date(), primary_key=True),
        sa.Column("up_count", sa.Integer()),
        sa.Column("down_count", sa.Integer()),
        sa.Column("flat_count", sa.Integer()),
        sa.Column("total_count", sa.Integer(), comment="有效交易家数（剔除停牌）"),
        sa.Column("limit_up_count", sa.Integer()),
        sa.Column("limit_down_count", sa.Integer()),
        sa.Column("median_pct", sa.Numeric(8, 3), comment="涨跌幅中位数 %"),
        sa.Column(
            "above_ma20_ratio", sa.Numeric(6, 4),
            comment="站上 MA20 比例（0–1 小数）",
        ),
        sa.Column("amount", sa.Numeric(24, 2), comment="全市场成交额(元)"),
        sa.Column("amount_vs_ma20", sa.Numeric(8, 3), comment="量能比（倍）"),
        sa.Column("index_vol_annual", sa.Numeric(8, 3), comment="沪深300 20日年化波动率 %"),
        sa.Column("temp_score", sa.Numeric(5, 1), comment="0–100 温度分"),
        sa.Column(
            # 任务书原文 VARCHAR(8) 装不下 insufficient（12 字符），按枚举放宽到 16
            "confidence", sa.String(16), nullable=False,
            server_default="insufficient",
        ),
        sa.Column("components", JSONB(), comment="子项原值/得分/权重/说明"),
        sa.Column("src", sa.String(16), nullable=False, server_default="engine"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("market_temperature_daily")
