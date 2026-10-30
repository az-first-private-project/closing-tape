"""
分区与视图 DDL（方案 B1/B3）
============================
`Base.metadata.create_all()` 能建出分区父表，但**不会**建子分区、视图、以及跨年自动建分区的逻辑。
这些必须由本模块以原生 SQL 完成：

  - `ensure_partitions(conn, years)`      预建年度分区 2021..2027 + default
  - `ensure_partition_for(conn, d)`       写入前确保该年分区存在（跨年那天不会写失败）
  - `create_views(conn)`                  建 v_stock_latest / v_stock_features
  - `drop_all_objects(conn)`              干净重建用（仅开发/迁移脚本调用）

约定：分区命名 `fact_stock_daily_y2026`，覆盖 [2026-01-01, 2027-01-01)。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.config import HISTORY_START

PARTITIONED_TABLE = "fact_stock_daily"
DEFAULT_PARTITION_SUFFIX = "default"

VIEW_LATEST = "v_stock_latest"
VIEW_FEATURES = "v_stock_features"


def _partition_name(year: int) -> str:
    return f"{PARTITIONED_TABLE}_y{year}"


def ensure_partitions(
    conn: Connection,
    start_year: int | None = None,
    end_year: int | None = None,
) -> list[str]:
    """预建年度分区 + default 分区。幂等。返回本次创建的分区名。"""
    start_year = start_year or HISTORY_START.year
    end_year = end_year or (date.today().year + 1)   # 预建下一年，方案 10.2
    created: list[str] = []

    for year in range(start_year, end_year + 1):
        name = _partition_name(year)
        conn.execute(text(
            f'CREATE TABLE IF NOT EXISTS "{name}" '
            f'PARTITION OF "{PARTITIONED_TABLE}" '
            f"FOR VALUES FROM ('{year}-01-01') TO ('{year + 1}-01-01')"
        ))
        created.append(name)

    default_name = f"{PARTITIONED_TABLE}_{DEFAULT_PARTITION_SUFFIX}"
    conn.execute(text(
        f'CREATE TABLE IF NOT EXISTS "{default_name}" '
        f'PARTITION OF "{PARTITIONED_TABLE}" DEFAULT'
    ))
    created.append(default_name)
    return created


def ensure_partition_for(conn: Connection, trade_date: date) -> None:
    """确保某日期所在年份的分区存在。

    default 分区能兜底，但落在 default 里的行后续无法自动迁移到正确分区，
    所以采集写入前先建好当年分区（跨年那天的关键保护）。
    """
    year = trade_date.year
    conn.execute(text(
        f'CREATE TABLE IF NOT EXISTS "{_partition_name(year)}" '
        f'PARTITION OF "{PARTITIONED_TABLE}" '
        f"FOR VALUES FROM ('{year}-01-01') TO ('{year + 1}-01-01')"
    ))


def create_views(conn: Connection) -> None:
    """建视图层（方案 B3）。"""
    conn.execute(text(f"""
        CREATE OR REPLACE VIEW {VIEW_LATEST} AS
        SELECT s.code, s.name, s.exchange, s.board, s.industry_l1, s.industry_l2,
               s.ownership, s.is_st, s.status, s.list_date,
               d.trade_date, d.open, d.high, d.low, d.close, d.pre_close,
               d.pct_chg, d.volume, d.amount, d.turnover_rate,
               d.market_cap, d.float_cap, d.pe_ttm, d.pb, d.ps_ttm, d.dv_ttm,
               d.is_limit_up, d.is_limit_down, d.is_suspended
        FROM fact_stock_daily d
        JOIN dim_stock s ON s.code = d.code
        WHERE d.trade_date = (SELECT max(trade_date) FROM fact_stock_daily)
    """))

    conn.execute(text(f"""
        CREATE OR REPLACE VIEW {VIEW_FEATURES} AS
        SELECT d.code, d.trade_date, d.close, d.pct_chg, d.amount, d.turnover_rate,
               d.pe_ttm, d.pb, d.ps_ttm, d.dv_ttm, d.market_cap, d.float_cap,
               f.ma5, f.ma10, f.ma20, f.ma60, f.rsi14,
               f.macd_dif, f.macd_dea, f.macd_hist,
               f.macd_golden_cross, f.ma_bullish,
               f.boll_mid, f.boll_upper, f.boll_lower, f.atr14,
               f.momentum_5, f.momentum_20, f.momentum_60, f.volatility_20,
               f.volume_ratio, f.amount_ma20,
               sc.value_score, sc.quality_score, sc.momentum_score,
               sc.volatility_score, sc.liquidity_score, sc.sentiment_score,
               sc.composite_score
        FROM fact_stock_daily d
        LEFT JOIN fact_feature_daily f
               ON f.code = d.code AND f.trade_date = d.trade_date
        LEFT JOIN fact_stock_score_daily sc
               ON sc.code = d.code AND sc.trade_date = d.trade_date
    """))


def drop_all_objects(conn: Connection) -> None:
    """删除全部业务对象（干净重建专用）。视图/分区会随父表一并删除。"""
    conn.execute(text(f"DROP VIEW IF EXISTS {VIEW_FEATURES} CASCADE"))
    conn.execute(text(f"DROP VIEW IF EXISTS {VIEW_LATEST} CASCADE"))
    # 子分区随父表 DROP 自动删除，但 default 分区先删更干净
    conn.execute(text(
        f'DROP TABLE IF EXISTS "{PARTITIONED_TABLE}_{DEFAULT_PARTITION_SUFFIX}" CASCADE'
    ))
    # 其余表由 metadata.drop_all 处理，这里只兜底清理可能残留的旧表
    for legacy in ("sector_daily", "algo_results"):
        conn.execute(text(f"DROP TABLE IF EXISTS {legacy} CASCADE"))


def partition_info(conn: Connection) -> list[dict]:
    """列出当前分区及行数（运维/任务监控页使用）。"""
    result = conn.execute(text("""
        SELECT c.relname AS partition,
               pg_get_expr(c.relpartbound, c.oid) AS bound
        FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        JOIN pg_class p ON p.oid = i.inhparent
        WHERE p.relname = :parent
        ORDER BY c.relname
    """), {"parent": PARTITIONED_TABLE})
    return [{"partition": row[0], "bound": row[1]} for row in result]


__all__ = [
    "ensure_partitions", "ensure_partition_for", "create_views",
    "drop_all_objects", "partition_info", "PARTITIONED_TABLE",
]
