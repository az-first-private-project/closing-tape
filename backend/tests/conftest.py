"""测试全局配置（P2-8）。

规则：
- ``GFNA_TEST_DB=1`` 时，在导入 ``app.db.session`` 之前把配置切换到独立
  ``_test`` 库；库名不以 ``_test`` 结尾直接报错终止，绝不回退生产库。
- 测试进程一律关闭调度器与启动建表，避免后台任务写入数据库。
- ``backend/.env`` 带 ``override=True``，会覆盖同名环境变量，因此这里直接
  改写 ``app.core.config`` 的模块级值，不能只设置 ``os.environ``。
"""
from __future__ import annotations

import os
import sys

import pytest
from sqlalchemy.engine import make_url

_TRUTHY = {"1", "true", "yes", "on"}
TEST_DB_ENABLED = os.getenv("GFNA_TEST_DB", "").strip().lower() in _TRUTHY

os.environ.setdefault("SCHEDULER_ENABLED", "0")
os.environ.setdefault("AUTO_CREATE_TABLES", "0")

from app.core import config  # noqa: E402

config.SCHEDULER_ENABLED = False
config.AUTO_CREATE_TABLES = False


def _test_database_url():
    raw = os.getenv("GFNA_TEST_DATABASE_URL", "").strip()
    if raw:
        url = make_url(raw)
    else:
        url = make_url(config.DATABASE_URL).set(
            database=os.getenv("GFNA_TEST_DATABASE_NAME", "gfna_test").strip()
        )
    name = url.database or ""
    if not name.endswith("_test"):
        raise RuntimeError(
            f"GFNA_TEST_DB=1 但测试库名不是 _test 结尾，拒绝连接：{name!r}"
        )
    return name, url


if TEST_DB_ENABLED:
    if "app.db.session" in sys.modules:
        raise RuntimeError(
            "app.db.session 已在切换测试库前导入，引擎会连生产库；请检查测试导入顺序"
        )
    _name, _url = _test_database_url()
    config.POSTGRES_DATABASE = _name
    config.POSTGRES_HOST = _url.host or config.POSTGRES_HOST
    config.POSTGRES_PORT = str(_url.port or config.POSTGRES_PORT)
    config.POSTGRES_USER = _url.username or config.POSTGRES_USER
    if _url.password is not None:
        config.POSTGRES_PASSWORD = _url.password
    config.DATABASE_URL = _url.set(
        drivername="postgresql+asyncpg"
    ).render_as_string(hide_password=False)
    config.DATABASE_URL_SYNC = _url.set(
        drivername="postgresql+psycopg2"
    ).render_as_string(hide_password=False)
    os.environ["POSTGRES_DATABASE"] = _name


@pytest.fixture(scope="session", autouse=True)
def _assert_test_db_isolation():
    """开启测试库时，任一连接指向非 _test 库立即失败。"""
    if not TEST_DB_ENABLED:
        return

    from app.db.session import engine, sync_engine

    targets = [
        ("config.DATABASE_URL", config.DATABASE_URL),
        ("config.DATABASE_URL_SYNC", config.DATABASE_URL_SYNC),
        ("async engine", engine.url.render_as_string(hide_password=False)),
        ("sync engine", sync_engine.url.render_as_string(hide_password=False)),
    ]
    for label, url in targets:
        name = make_url(url).database or ""
        assert name.endswith("_test"), f"{label} 指向非 _test 库：{name!r}"
