"""市场原始因子补充指数高低点（V7 T1）

Revision ID: 0012_market_factor_ohlc
Revises: 0011_market_regime
Create Date: 2026-11-12

筑底确认需要判断「近 10 日低点是否抬高」，`fact_market_daily` 需要保留
沪深300 当日 high/low。只加两列，幂等。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0012_market_factor_ohlc"
down_revision: str | None = "0011_market_regime"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_column("fact_market_daily", "high"):
        op.add_column("fact_market_daily", sa.Column("high", sa.Numeric(14, 3)))
    if not _has_column("fact_market_daily", "low"):
        op.add_column("fact_market_daily", sa.Column("low", sa.Numeric(14, 3)))


def downgrade() -> None:
    if _has_column("fact_market_daily", "low"):
        op.drop_column("fact_market_daily", "low")
    if _has_column("fact_market_daily", "high"):
        op.drop_column("fact_market_daily", "high")
