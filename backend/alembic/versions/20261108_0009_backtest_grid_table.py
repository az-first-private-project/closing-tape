"""矩阵主表与格关联（P2-2）

Revision ID: 0009_backtest_grid_table
Revises: 0008_paper_portfolio
Create Date: 2026-11-08

旧模型把 ``grid_id`` / ``grid_cell`` 挂在 ``backtest_run`` 上，复用会把回测行
改挂到新矩阵，导致旧矩阵链接丢格甚至 404。这里新增 ``backtest_grid`` 主表与
``backtest_grid_cell`` 关联表，同一回测可被多个矩阵复用；存量列回填后删除。
"""
from __future__ import annotations

import logging
from collections import Counter

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

revision: str = "0009_backtest_grid_table"
down_revision: str | None = "0008_paper_portfolio"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def _create_grid_table() -> None:
    op.create_table(
        "backtest_grid",
        sa.Column("grid_id", sa.String(length=36), primary_key=True),
        sa.Column("algo_code", sa.String(length=16), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column(
            "axes", postgresql.JSONB(astext_type=sa.Text()), nullable=True,
            comment='{"rebalance_days": [...], "max_positions": [...]}',
        ),
        sa.Column(
            "strategy_params", postgresql.JSONB(astext_type=sa.Text()), nullable=True,
            comment="策略参数快照（未含每格 limit 调整）",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
    )


def _create_cell_table() -> None:
    op.create_table(
        "backtest_grid_cell",
        sa.Column("grid_id", sa.String(length=36), nullable=False),
        sa.Column("bt_id", sa.String(length=36), nullable=False),
        sa.Column("rebalance_days", sa.Integer(), nullable=False),
        sa.Column("max_positions", sa.Integer(), nullable=False),
        sa.Column(
            "reused", sa.Boolean(), nullable=False, server_default=sa.text("false"),
            comment="该格是否复用已有回测",
        ),
        sa.PrimaryKeyConstraint("grid_id", "bt_id", name="pk_backtest_grid_cell"),
        sa.ForeignKeyConstraint(
            ["grid_id"], ["backtest_grid.grid_id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["bt_id"], ["backtest_run.bt_id"], ondelete="CASCADE",
        ),
    )


def _ensure_indexes() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    grid_indexes = {item["name"] for item in inspector.get_indexes("backtest_grid")}
    if "ix_backtest_grid_created" not in grid_indexes:
        op.create_index("ix_backtest_grid_created", "backtest_grid", ["created_at"])
    cell_indexes = {item["name"] for item in inspector.get_indexes("backtest_grid_cell")}
    if "ix_backtest_grid_cell_bt" not in cell_indexes:
        op.create_index("ix_backtest_grid_cell_bt", "backtest_grid_cell", ["bt_id"])


def _backfill_from_run_columns() -> None:
    """把存量 ``backtest_run.grid_id/grid_cell`` 折叠成主表与关联。

    历史上被复用改挂过的回测行只剩最后一次所属矩阵，无法还原更早的归属；
    迁移能保证的是“当前可访问的矩阵链接迁移后仍可访问”。
    """
    bind = op.get_bind()
    columns = {item["name"] for item in sa.inspect(bind).get_columns("backtest_run")}
    if "grid_id" not in columns or "grid_cell" not in columns:
        return

    rows = bind.execute(sa.text(
        """
        SELECT bt_id, grid_id, algo_code, start_date, end_date, created_at,
               grid_cell, params
        FROM backtest_run
        WHERE grid_id IS NOT NULL
        ORDER BY created_at
        """
    )).mappings().all()
    if not rows:
        return

    grids: dict[str, dict] = {}
    cells: list[dict] = []
    skipped = 0
    for row in rows:
        meta = row["grid_cell"] or {}
        params = row["params"] or {}
        rb = meta.get("rebalance_days", params.get("rebalance_days"))
        mp = meta.get("max_positions", params.get("max_positions"))
        if rb is None or mp is None:
            skipped += 1
            continue
        rb, mp = int(rb), int(mp)
        grid = grids.setdefault(row["grid_id"], {
            "algo_code": row["algo_code"],
            "start_date": row["start_date"],
            "end_date": row["end_date"],
            "created_at": row["created_at"],
            "rebalance_days": set(),
            "max_positions": set(),
            "algos": Counter(),
        })
        grid["start_date"] = min(grid["start_date"], row["start_date"])
        grid["end_date"] = max(grid["end_date"], row["end_date"])
        grid["created_at"] = min(grid["created_at"], row["created_at"])
        grid["rebalance_days"].add(rb)
        grid["max_positions"].add(mp)
        grid["algos"][row["algo_code"]] += 1
        cells.append({
            "grid_id": row["grid_id"],
            "bt_id": row["bt_id"],
            "rebalance_days": rb,
            "max_positions": mp,
            "reused": bool(meta.get("reused")),
        })

    if skipped:
        logger.warning("矩阵回填跳过 %d 条缺少轴坐标的回测行", skipped)
    if not cells:
        return

    grid_table = sa.table(
        "backtest_grid",
        sa.column("grid_id", sa.String(36)),
        sa.column("algo_code", sa.String(16)),
        sa.column("start_date", sa.Date()),
        sa.column("end_date", sa.Date()),
        sa.column("axes", postgresql.JSONB()),
        sa.column("strategy_params", postgresql.JSONB()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    cell_table = sa.table(
        "backtest_grid_cell",
        sa.column("grid_id", sa.String(36)),
        sa.column("bt_id", sa.String(36)),
        sa.column("rebalance_days", sa.Integer()),
        sa.column("max_positions", sa.Integer()),
        sa.column("reused", sa.Boolean()),
    )

    grid_rows = [
        {
            "grid_id": grid_id,
            "algo_code": max(grid["algos"], key=grid["algos"].get),
            "start_date": grid["start_date"],
            "end_date": grid["end_date"],
            "axes": {
                "rebalance_days": sorted(grid["rebalance_days"]),
                "max_positions": sorted(grid["max_positions"]),
            },
            "strategy_params": None,
            "created_at": grid["created_at"],
        }
        for grid_id, grid in grids.items()
        if grid["algos"]
    ]
    if grid_rows:
        bind.execute(
            pg_insert(grid_table).values(grid_rows).on_conflict_do_nothing(
                index_elements=["grid_id"]
            )
        )
    bind.execute(
        pg_insert(cell_table).values(cells).on_conflict_do_nothing(
            index_elements=["grid_id", "bt_id"]
        )
    )


def _drop_legacy_columns() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {item["name"] for item in inspector.get_columns("backtest_run")}
    indexes = {item["name"] for item in inspector.get_indexes("backtest_run")}
    if "ix_backtest_run_grid" in indexes:
        op.drop_index("ix_backtest_run_grid", table_name="backtest_run")
    if "grid_cell" in columns:
        op.drop_column("backtest_run", "grid_cell")
    if "grid_id" in columns:
        op.drop_column("backtest_run", "grid_id")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("backtest_grid"):
        _create_grid_table()
    if not inspector.has_table("backtest_grid_cell"):
        _create_cell_table()
    _ensure_indexes()
    _backfill_from_run_columns()
    _drop_legacy_columns()


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {item["name"] for item in inspector.get_columns("backtest_run")}
    if "grid_id" not in columns:
        op.add_column("backtest_run", sa.Column("grid_id", sa.String(length=36), nullable=True))
    if "grid_cell" not in columns:
        op.add_column(
            "backtest_run",
            sa.Column("grid_cell", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("backtest_run")}
    if "ix_backtest_run_grid" not in indexes:
        op.create_index("ix_backtest_run_grid", "backtest_run", ["grid_id"])

    if inspector.has_table("backtest_grid") and inspector.has_table("backtest_grid_cell"):
        bind.execute(sa.text(
            """
            UPDATE backtest_run AS r
            SET grid_id = latest.grid_id,
                grid_cell = jsonb_build_object(
                    'rebalance_days', latest.rebalance_days,
                    'max_positions', latest.max_positions,
                    'start_date', to_char(latest.start_date, 'YYYY-MM-DD'),
                    'end_date', to_char(latest.end_date, 'YYYY-MM-DD'),
                    'reused', latest.reused
                )
            FROM (
                SELECT DISTINCT ON (c.bt_id)
                       c.bt_id, c.grid_id, c.rebalance_days, c.max_positions, c.reused,
                       g.start_date, g.end_date
                FROM backtest_grid_cell AS c
                JOIN backtest_grid AS g ON g.grid_id = c.grid_id
                ORDER BY c.bt_id, g.created_at DESC
            ) AS latest
            WHERE r.bt_id = latest.bt_id
            """
        ))
        op.drop_table("backtest_grid_cell")
    if inspector.has_table("backtest_grid"):
        op.drop_table("backtest_grid")
