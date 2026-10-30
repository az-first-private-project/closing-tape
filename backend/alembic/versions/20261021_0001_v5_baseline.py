"""V5 基线：建全部表 + 年度分区 + 视图

Revision ID: 0001_v5_baseline
Revises:
Create Date: 2026-10-21

说明
----
本基线由 ORM metadata 直接生成（`Base.metadata.create_all`），而不是逐列手写 op.create_table。
理由：
  1. V5 是对旧库的**干净重建**（V3/V4 的表与新模型差异过大，逐列迁移没有收益）；
  2. 基线之后的所有变更都用 `alembic revision --autogenerate` 正常增量迁移；
  3. 分区与视图是 create_all 表达不了的对象，必须用原生 SQL 补充，这里一并处理。

执行顺序很重要：
  1. create_all（含分区父表 fact_stock_daily）
  2. ensure_partitions（2021..今年+1 + default）
  3. create_views（v_stock_latest / v_stock_features）
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from alembic import op

# 允许从 alembic/versions 深目录 import 到 app 包
BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import HISTORY_START  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db import models  # noqa: F401,E402  —— 必须 import 才能注册所有表
from app.db.views import create_views, drop_all_objects, ensure_partitions  # noqa: E402

revision: str = "0001_v5_baseline"
down_revision: str | None = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    Base.metadata.create_all(bind=bind)
    ensure_partitions(bind, HISTORY_START.year, date.today().year + 1)
    create_views(bind)


def downgrade() -> None:
    bind = op.get_bind()
    drop_all_objects(bind)          # 视图 + default 分区 + 遗留表
    Base.metadata.drop_all(bind=bind)
