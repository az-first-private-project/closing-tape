"""纸面组合跟踪表（V6 T11）

Revision ID: 0008_paper_portfolio
Revises: 0007_backtest_grid
Create Date: 2026-11-01

一策略一组合；paper_nav 每交易日一行，整体重算后幂等 upsert。
基线迁移用当前 ORM metadata 建表，fresh DB 可能已包含这些对象，升级必须幂等。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_paper_portfolio"
down_revision: str | None = "0007_backtest_grid"
branch_labels = None
depends_on = None


def _create_portfolio() -> None:
    op.create_table(
        "paper_portfolio",
        sa.Column("portfolio_id", sa.String(length=36), primary_key=True),
        sa.Column("algo_code", sa.String(length=16), nullable=False, unique=True),
        sa.Column("name", sa.String(length=64), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True, comment="建仓日 = 策略首个实盘 run 交易日"),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="策略参数 / 回测参数快照 / paper bt_id",
        ),
        sa.Column("rebalance_days", sa.Integer(), nullable=True),
        sa.Column("max_positions", sa.Integer(), nullable=True),
        sa.Column("initial_capital", sa.Numeric(20, 2), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
    )


def _create_nav() -> None:
    op.create_table(
        "paper_nav",
        sa.Column("portfolio_id", sa.String(length=36), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("nav", sa.Numeric(16, 6), nullable=True),
        sa.Column("benchmark", sa.Numeric(16, 6), nullable=True),
        sa.Column("drawdown", sa.Numeric(8, 3), nullable=True, comment="相对历史峰值，负数"),
        sa.Column(
            "holdings", postgresql.JSONB(astext_type=sa.Text()),
            nullable=True, comment="收盘持仓快照",
        ),
        sa.Column("cash_ratio", sa.Numeric(6, 4), nullable=True),
        sa.Column(
            "pending_orders", postgresql.JSONB(astext_type=sa.Text()),
            nullable=True, comment="下一交易日待执行",
        ),
        sa.Column(
            "vs_backtest", postgresql.JSONB(astext_type=sa.Text()),
            nullable=True, comment="与同区间人工回测的差值；仅最新行写入",
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("portfolio_id", "trade_date", name="pk_paper_nav"),
        sa.ForeignKeyConstraint(
            ["portfolio_id"], ["paper_portfolio.portfolio_id"], ondelete="CASCADE",
        ),
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("paper_portfolio"):
        _create_portfolio()
    if not inspector.has_table("paper_nav"):
        _create_nav()
    existing = {item["name"] for item in sa.inspect(bind).get_indexes("paper_nav")}
    if "ix_paper_nav_portfolio_date" not in existing:
        op.create_index(
            "ix_paper_nav_portfolio_date",
            "paper_nav",
            ["portfolio_id", sa.text("trade_date DESC")],
        )


def downgrade() -> None:
    op.drop_index("ix_paper_nav_portfolio_date", table_name="paper_nav")
    op.drop_table("paper_nav")
    op.drop_table("paper_portfolio")
