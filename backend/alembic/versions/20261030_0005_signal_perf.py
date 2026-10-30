"""信号前瞻收益表（V6 T5）

Revision ID: 0005_signal_perf
Revises: 0004_sector_member_partial
Create Date: 2026-10-30

一行 = 策略 + 信号日 + 股票，记录 T+1 开盘买入后的 5/10/20 日前瞻收益。
只统计非回测信号（stg_run.is_backtest IS FALSE），由
`app/engine/signal_perf.py` 幂等重算。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0005_signal_perf"
down_revision: str | None = "0004_sector_member_partial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stg_signal_perf",
        sa.Column("algo_code", sa.String(16), nullable=False),
        sa.Column("signal_date", sa.Date(), nullable=False),
        sa.Column("code", sa.String(6), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("score", sa.Numeric(12, 4), nullable=True),
        sa.Column("entry_date", sa.Date(), nullable=True),
        sa.Column("entry_price", sa.Numeric(16, 6), nullable=True),
        sa.Column("ret_5", sa.Numeric(10, 4), nullable=True),
        sa.Column("ret_10", sa.Numeric(10, 4), nullable=True),
        sa.Column("ret_20", sa.Numeric(10, 4), nullable=True),
        sa.Column("excess_5", sa.Numeric(10, 4), nullable=True),
        sa.Column("excess_10", sa.Numeric(10, 4), nullable=True),
        sa.Column("excess_20", sa.Numeric(10, 4), nullable=True),
        sa.Column(
            "mature_5", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "mature_10", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "mature_20", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "fillable", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("industry_l1", sa.String(64), nullable=True),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint(
            "algo_code", "signal_date", "code", name="pk_stg_signal_perf"
        ),
    )
    op.create_index(
        "ix_stg_signal_perf_algo_date",
        "stg_signal_perf",
        ["algo_code", "signal_date"],
    )
    op.create_index(
        "ix_stg_signal_perf_algo_mature",
        "stg_signal_perf",
        ["algo_code", "mature_20", "signal_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_stg_signal_perf_algo_mature", table_name="stg_signal_perf")
    op.drop_index("ix_stg_signal_perf_algo_date", table_name="stg_signal_perf")
    op.drop_table("stg_signal_perf")
