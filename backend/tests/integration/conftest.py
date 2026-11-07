"""真实 PostgreSQL 集成测试夹具（W0-2）。

隔离规则：
- 只有 ``GFNA_TEST_DB=1`` 才启用；默认单测不触碰数据库。
- 库名必须以 ``_test`` 结尾；未显式给 ``GFNA_TEST_DATABASE_URL`` 时，
  固定使用 ``GFNA_TEST_DATABASE_NAME``（默认 gfna_test），绝不沿用生产库名。
- 每个用例绑定一个外层事务，用例内的 commit 只释放 savepoint；
  夹具结束回滚外层事务，测试数据不会落库。
- 测试库需先迁移：``GFNA_TEST_DB=1 alembic upgrade head``。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import inspect
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config

TEST_DB_ENABLED = os.getenv("GFNA_TEST_DB", "").strip().lower() in {
    "1", "true", "yes", "on",
}


def _test_database_url() -> str:
    raw = os.getenv("GFNA_TEST_DATABASE_URL", "").strip()
    if raw:
        url = make_url(raw)
    else:
        url = make_url(config.DATABASE_URL).set(
            database=os.getenv("GFNA_TEST_DATABASE_NAME", "gfna_test").strip()
        )
    if not (url.database or "").endswith("_test"):
        raise RuntimeError(
            "集成测试库名必须以 _test 结尾，拒绝连接生产库："
            f"{url.database!r}"
        )
    return url.render_as_string(hide_password=False)


TEST_DATABASE_URL = _test_database_url() if TEST_DB_ENABLED else ""


def pytest_collection_modifyitems(config, items) -> None:
    if TEST_DB_ENABLED:
        return
    base = Path(__file__).resolve().parent
    marker = pytest.mark.skip(
        reason="需要 GFNA_TEST_DB=1 与已迁移的独立 _test 数据库"
    )
    for item in items:
        path = Path(str(getattr(item, "path", item.fspath))).resolve()
        if path.parent == base:
            item.add_marker(marker)


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        has_schema = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).has_table(
                "dim_sector_member"
            )
        )
        if not has_schema:
            await transaction.rollback()
            await engine.dispose()
            pytest.fail(
                "gfna_test 尚未迁移，请先运行："
                "GFNA_TEST_DB=1 alembic upgrade head"
            )
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()
