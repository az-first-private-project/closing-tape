"""策略 run 信号快照 schema 版本（P2-7）

Revision ID: 0010_stg_run_signal_schema
Revises: 0009_backtest_grid_table
Create Date: 2026-11-08

旧版 run 的 ``stg_signal.metrics`` 没有结构化依据（``triggered`` /
``score_breakdown``），前端无法区分“旧版本快照”与“当前版本但无依据”。
新增 ``stg_run.signal_schema``：1=旧版，2=当前。存量只按证据回填：
逐信号 JSONB 中出现结构化字段的 run 标为 2，其余保持 1，不改写任何信号数据。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0010_stg_run_signal_schema"
down_revision: str | None = "0009_backtest_grid_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {item["name"] for item in inspector.get_columns("stg_run")}
    if "signal_schema" not in columns:
        op.add_column(
            "stg_run",
            sa.Column(
                "signal_schema",
                sa.SmallInteger(),
                nullable=False,
                server_default=sa.text("1"),
                comment="信号快照 schema：1=旧版无结构化依据，2=当前（triggered/score_breakdown）",
            ),
        )

    # 仅凭已落库的结构化字段回填，不重算、不推断
    bind.execute(sa.text(
        """
        UPDATE stg_run AS r
        SET signal_schema = 2
        WHERE r.signal_schema < 2
          AND EXISTS (
              SELECT 1
              FROM stg_signal AS s
              WHERE s.run_id = r.run_id
                AND (s.metrics ? 'triggered' OR s.metrics ? 'score_breakdown')
          )
        """
    ))

    op.alter_column(
        "stg_run", "signal_schema",
        server_default=sa.text("2"), existing_type=sa.SmallInteger(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.drop_column("stg_run", "signal_schema")
