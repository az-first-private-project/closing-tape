"""回测参数敏感性矩阵列（V6 T10）

Revision ID: 0007_backtest_grid
Revises: 0006_watchlist_state
Create Date: 2026-11-01

一次矩阵 = 一个 grid_id，每格 = 一条独立 backtest_run；
grid_cell 记录两轴坐标与区间快照，便于矩阵无表重建。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_backtest_grid"
down_revision: str | None = "0006_watchlist_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "backtest_run",
        sa.Column("grid_id", sa.String(length=36), nullable=True, comment="参数敏感性矩阵 ID（T10）"),
    )
    op.add_column(
        "backtest_run",
        sa.Column(
            "grid_cell",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="矩阵坐标 {rebalance_days,max_positions,start_date,end_date,reused}",
        ),
    )
    op.create_index("ix_backtest_run_grid", "backtest_run", ["grid_id"])


def downgrade() -> None:
    op.drop_index("ix_backtest_run_grid", table_name="backtest_run")
    op.drop_column("backtest_run", "grid_cell")
    op.drop_column("backtest_run", "grid_id")
