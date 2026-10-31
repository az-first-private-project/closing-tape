"""自选状态表（V6 T8）

Revision ID: 0006_watchlist_state
Revises: 0005_signal_perf
Create Date: 2026-10-31

单租户单行：id=1，codes/seen_hits 为 JSONB 数组。
localStorage 仅作前端缓存，清站点数据或隐私模式后从本表恢复。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_watchlist_state"
down_revision: str | None = "0005_signal_perf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_watchlist_state",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column(
            "codes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "seen_hits",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("id = 1", name="ck_app_watchlist_state_singleton"),
        sa.PrimaryKeyConstraint("id", name="pk_app_watchlist_state"),
    )


def downgrade() -> None:
    op.drop_table("app_watchlist_state")
