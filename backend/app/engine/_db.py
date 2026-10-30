"""
engine 数据访问公共工具（内部模块）
=====================================
分析层所有需要读写数据库的函数都要同时支持两种调用方式：

1. **自建事务**（`db=None`）：用 `app.db.session.session_scope()` 开一个短事务，
   每批写入后立即 commit，避免"全市场回补"这种长任务把事务拖到膨胀、锁表。
2. **复用外部 session**（`db` 传入）：由 `app.pipeline.py` 传入共享会话，
   此时**不 commit**（只 flush），让 pipeline 能把多个步骤放在同一个事务里，
   要么一起成功、要么一起回滚；同时避免一个任务反复开连接把连接池打满。

注意：本模块不负责数据新鲜度（`MetaDataQuality`）的写入——那是 pipeline 的统一职责，
引擎层重复写入会互相覆盖。
"""
from __future__ import annotations

import math
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, AsyncIterator
from zoneinfo import ZoneInfo

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import TIMEZONE
from app.db.session import session_scope


@asynccontextmanager
async def session_ctx(db: AsyncSession | None = None) -> AsyncIterator[AsyncSession]:
    """获取数据库会话：外部传入则复用，否则自建短事务会话。"""
    if db is not None:
        yield db
    else:
        async with session_scope() as session:
            yield session


async def commit_or_flush(session: AsyncSession, external: bool) -> None:
    """写入批次收尾。

    参数:
        session: 数据库会话
        external: 是否为外部传入的共享 session

    - 外部 session：只 `flush()`，把提交时机交给 pipeline（一个事务内可回滚）；
    - 自建 session：立即 `commit()`，控制事务长度与内存占用。
    """
    if external:
        await session.flush()
    else:
        await session.commit()


def trade_day_bounds(trade_date: date) -> tuple[datetime, datetime]:
    """交易日的 [00:00, 次日 00:00) 时间区间（Asia/Shanghai，含时区）。

    用于按 `publish_time` 查当日新闻：数据库列是 TIMESTAMPTZ，
    用带时区的边界比较才能命中索引，也避免容器 UTC 时区导致日期错位。
    """
    try:
        zone = ZoneInfo(TIMEZONE)
    except Exception:  # noqa: BLE001 - Windows 缺 tzdata 时兜底为东八区固定偏移
        zone = timezone(timedelta(hours=8))
    start = datetime.combine(trade_date, time.min, tzinfo=zone)
    return start, start + timedelta(days=1)


def to_jsonable(value: Any) -> Any:
    """把任意 SQLAlchemy/pandas 返回值转成可安全写入 JSONB 的 Python 值。

    JSONB 不接受 NaN/Infinity（PostgreSQL 的 json 解析会拒绝 `NaN` 字面量），
    Decimal/date/datetime/numpy 标量也不能直接 json.dumps，这里统一清洗：
      - NaN / ±Inf → None
      - Decimal → float
      - date/datetime → ISO 字符串
      - numpy 标量 → Python 原生类型
      - dict/list/tuple 递归处理
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return None
        return number
    return value


__all__ = ["session_ctx", "commit_or_flush", "to_jsonable", "trade_day_bounds"]
