"""板块成分 partial 标记（V6 T4 数据质量）

Revision ID: 0004_sector_member_partial
Revises: 0003_sector_metrics
Create Date: 2026-10-29

说明
----
同花顺 pages>10 的板块只能抓到前/后各 5 页（100/200 只），这类名单只能用于
龙头，不能当作完整成分参与市值/资金流/家数聚合。新增 `is_partial` 布尔列，
由 `sync_members` 写入；`sector_agg` 只聚合 `is_partial = false` 的成分。

迁移内一次性把当前已知的 5 个 partial 板块标记为 true（与
`var/sector_backfill_progress.json` 的 members.completed[*].partial 一致）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004_sector_member_partial"
down_revision: str | None = "0003_sector_metrics"
branch_labels = None
depends_on = None

_PARTIAL_SECTORS = (
    "THS_阿里巴巴概念",
    "THS_参股券商",
    "THS_参股银行",
    "THS_车联网(车路协同)",
    "THS_存储芯片",
)


def upgrade() -> None:
    existing = {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_columns("dim_sector_member")
    }
    if "is_partial" in existing:
        return
    op.add_column(
        "dim_sector_member",
        sa.Column(
            "is_partial", sa.Boolean(), nullable=False, server_default=sa.text("false"),
            comment="名单仅覆盖 THS 前后各 5 页（pages>10），不参与市值/家数聚合",
        ),
    )
    op.get_bind().execute(
        sa.text(
            "UPDATE dim_sector_member SET is_partial = true "
            "WHERE sector_code = ANY(:codes)"
        ),
        {"codes": list(_PARTIAL_SECTORS)},
    )


def downgrade() -> None:
    op.drop_column("dim_sector_member", "is_partial")
