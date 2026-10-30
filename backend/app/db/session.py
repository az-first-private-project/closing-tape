"""
数据库会话（V5）
================
- 异步引擎给 FastAPI/采集/回测用（asyncpg）
- 同步引擎只给 Alembic 与极少数脚本（psycopg2）
- `session_scope()` 供后台任务使用：自动 commit/rollback/close

注意：所有引擎都设置 `connect_args` 的 `server_settings` 把会话时区固定为 Asia/Shanghai，
避免容器默认 UTC 导致 `date`/`now()` 偏移（方案 10.2 时区坑）。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import (
    DATABASE_URL,
    DATABASE_URL_SYNC,
    DB_ECHO,
    DB_POOL_SIZE,
    TIMEZONE,
)

# ==================== 异步（主用）====================
engine = create_async_engine(
    DATABASE_URL,
    echo=DB_ECHO,
    pool_size=DB_POOL_SIZE,
    max_overflow=DB_POOL_SIZE,
    pool_pre_ping=True,
    pool_recycle=1800,
    connect_args={
        "server_settings": {
            "application_name": "gfna",
            "timezone": TIMEZONE,
            # 防"僵尸连接"：任务被强杀后连接可能停在 idle in transaction 并**继续持有
            # advisory lock**，导致后续采集任务一律 lock_busy 跳过（实测踩到，表现为
            # "任务没进展但也不报错"）。让 PG 主动回收这种会话。
            "idle_in_transaction_session_timeout": "900000",   # 15 分钟
        },
        "timeout": 15,
    },
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖注入：每请求一个会话。

    注意（V5）：策略运行/回测只 `flush` 不 `commit`（事务所有权归调用方），
    因此这里在**成功路径显式 commit** —— 否则 `/algo/*` 产生的 `stg_run` 会随
    请求结束被回滚，缓存与会话复现都会失效。
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """后台任务专用：进入即开事务，正常退出 commit，异常回滚。"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ==================== 同步（仅 Alembic / 运维脚本）====================
sync_engine = create_engine(
    DATABASE_URL_SYNC,
    echo=False,
    pool_pre_ping=True,
    connect_args={"options": f"-c timezone={TIMEZONE}", "connect_timeout": 15},
)


async def dispose_engines() -> None:
    await engine.dispose()
    sync_engine.dispose()


__all__ = ["engine", "AsyncSessionLocal", "get_db", "session_scope",
           "sync_engine", "dispose_engines"]
