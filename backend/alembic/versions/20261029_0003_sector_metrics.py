"""板块聚合指标增列（V6 T4）

Revision ID: 0003_sector_metrics
Revises: 0002_market_temperature
Create Date: 2026-10-29

说明
----
给非分区的 `fact_sector_daily` 增加 5 个派生列，由
`app/engine/sector_agg.py` 按当日 SCD2 名单重算：
- market_cap / float_cap：成分股市值合计（元）
- main_net_inflow：成分股 main_net_t0 合计（元）
- rs_20d：板块 20 日收益 − 沪深300 同窗口收益（百分点）
- member_count：当日名单成分数

全部允许 NULL；成分数 <3 或数据缺失时保持 NULL，不用 0 代替。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003_sector_metrics"
down_revision: str | None = "0002_market_temperature"
branch_labels = None
depends_on = None

_COLUMNS = (
    sa.Column("market_cap", sa.Numeric(24, 2), comment="板块成分股总市值合计(元)"),
    sa.Column("float_cap", sa.Numeric(24, 2), comment="板块成分股流通市值合计(元)"),
    sa.Column("main_net_inflow", sa.Numeric(24, 2), comment="成分股主力净流入合计(元)"),
    sa.Column("rs_20d", sa.Numeric(8, 3), comment="20 交易日相对沪深300超额收益(百分点)"),
    sa.Column("member_count", sa.Integer(), comment="当日 SCD2 名单成分数"),
)


def upgrade() -> None:
    existing = {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_columns("fact_sector_daily")
    }
    for column in _COLUMNS:
        if column.name not in existing:
            op.add_column("fact_sector_daily", column)


def downgrade() -> None:
    for name in ("member_count", "rs_20d", "main_net_inflow", "float_cap", "market_cap"):
        op.drop_column("fact_sector_daily", name)
