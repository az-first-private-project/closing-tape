"""
数据库引导脚本（V5）
====================
用法（在 `backend/` 目录）：
    python scripts/bootstrap_db.py                  # 建库 + 建表 + 分区 + 视图 + 种子
    python scripts/bootstrap_db.py --recreate       # **危险**：删除全部业务表后重建
    python scripts/bootstrap_db.py --only-schema    # 只建表，不写种子

它做四件事：
  1. 若目标库不存在则创建（连到 postgres 库执行 CREATE DATABASE）
  2. `Base.metadata.create_all` 建表（含分区父表）
  3. 预建年度分区 2021..2027 + default，并建视图（create_all 做不到）
  4. 写入 algo_meta 种子 + 按需回填 dim_stock.ownership

设计说明：V5 用「create_all + 幂等 DDL + Alembic」双轨：
  - 全新环境：本脚本一键到位（方案「30 分钟内跑出第一份选股」依赖它）
  - 已有环境：用 `alembic upgrade head` 做增量迁移，避免删库
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

from sqlalchemy import select, text  # noqa: E402

from app.core.config import (  # noqa: E402
    HISTORY_START, POSTGRES_DATABASE, POSTGRES_HOST, POSTGRES_PASSWORD,
    POSTGRES_PORT, POSTGRES_USER, TIMEZONE,
)
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db import models  # noqa: F401,E402  —— 必须 import 才能注册所有表
from app.db.seed import ALGO_SEED  # noqa: E402
from app.db.views import create_views, drop_all_objects, ensure_partitions, partition_info  # noqa: E402

logger = get_logger("bootstrap")


def ensure_database() -> bool:
    """确保目标数据库存在。返回 True 表示是本次新建的。"""
    import psycopg2

    conn = psycopg2.connect(
        host=POSTGRES_HOST, port=int(POSTGRES_PORT), user=POSTGRES_USER,
        password=POSTGRES_PASSWORD, dbname="postgres", connect_timeout=10,
    )
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (POSTGRES_DATABASE,))
            if cur.fetchone():
                logger.info("数据库已存在: %s", POSTGRES_DATABASE)
                return False
            cur.execute(
                f'CREATE DATABASE "{POSTGRES_DATABASE}" WITH ENCODING \'UTF8\' TEMPLATE template0'
            )
            logger.info("已创建数据库: %s", POSTGRES_DATABASE)
            return True
    finally:
        conn.close()


def run_schema(recreate: bool = False) -> None:
    """建表 + 分区 + 视图。"""
    from app.db.session import sync_engine

    with sync_engine.begin() as conn:
        conn.execute(text(f"SET TIME ZONE '{TIMEZONE}'"))

        if recreate:
            logger.warning("--recreate：删除全部业务对象")
            drop_all_objects(conn)
            Base.metadata.drop_all(conn)

        Base.metadata.create_all(conn)
        logger.info("表结构已就绪：%d 张表", len(Base.metadata.tables))

        end_year = date.today().year + 1
        created = ensure_partitions(conn, HISTORY_START.year, end_year)
        logger.info("分区已就绪：%s ~ %s + default", HISTORY_START.year, end_year)

        create_views(conn)
        logger.info("视图已就绪：v_stock_latest / v_stock_features")

        info = partition_info(conn)
        logger.info("当前分区 %d 个", len(info))


def seed_algo_meta() -> int:
    """写入/更新策略元信息（幂等 upsert），并下线种子中已不存在的策略。"""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.db.models import AlgoMeta
    from app.db.session import sync_engine

    active_codes = {item["algo_code"] for item in ALGO_SEED}
    with sync_engine.begin() as conn:
        existing = {r[0] for r in conn.execute(select(AlgoMeta.algo_code))}
        for item in ALGO_SEED:
            stmt = pg_insert(AlgoMeta).values(**item, is_active=True)
            stmt = stmt.on_conflict_do_update(
                index_elements=[AlgoMeta.algo_code],
                set_={k: v for k, v in item.items() if k != "algo_code"} | {"is_active": True},
            )
            conn.execute(stmt)
        retired = conn.execute(
            AlgoMeta.__table__.update()
            .where(AlgoMeta.algo_code.notin_(active_codes), AlgoMeta.is_active.is_(True))
            .values(is_active=False)
            .returning(AlgoMeta.algo_code)
        ).scalars().all()
        logger.info(
            "策略元信息已写入：%d 条（原有 %d 条，下线 %d 条%s）",
            len(ALGO_SEED), len(existing), len(retired),
            f"：{', '.join(retired)}" if retired else "",
        )
    return len(ALGO_SEED)


def refresh_ownership() -> int:
    """按 core/soe.py 的规则重算 dim_stock.ownership（幂等，可反复跑）。"""
    from app.core.soe import classify_ownership
    from app.db.models import DimStock
    from app.db.session import sync_engine

    updated = 0
    with sync_engine.begin() as conn:
        rows = conn.execute(select(DimStock.code, DimStock.name)).all()
        for code, name in rows:
            ownership = classify_ownership(code, name)
            conn.execute(
                DimStock.__table__.update()
                .where(DimStock.code == code)
                .values(ownership=ownership)
            )
            updated += 1
    logger.info("所有制归类完成：%d 只", updated)
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description="GFNA V5 数据库引导")
    parser.add_argument("--recreate", action="store_true", help="危险：删除全部业务表后重建")
    parser.add_argument("--only-schema", action="store_true", help="只建表结构，不写种子")
    parser.add_argument("--no-db-create", action="store_true", help="不检查/创建数据库")
    args = parser.parse_args()

    setup_logging()

    if not args.no_db_create:
        try:
            ensure_database()
        except Exception as exc:                      # noqa: BLE001
            logger.error("创建数据库失败（是否已手动建库？）：%s", exc)
            return 1

    run_schema(recreate=args.recreate)

    if not args.only_schema:
        seed_algo_meta()
        try:
            refresh_ownership()
        except Exception as exc:                      # noqa: BLE001
            logger.warning("所有制回填跳过（dim_stock 为空时正常）：%s", exc)

    logger.info("✅ 数据库引导完成：%s@%s:%s/%s",
                POSTGRES_USER, POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DATABASE)
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(main())
