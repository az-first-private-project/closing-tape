"""P2-8：测试进程的所有数据库连接必须指向独立 ``_test`` 库。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core import config
from app.db.session import engine, sync_engine


def test_config_urls_point_to_test_database() -> None:
    for label, url in (
        ("DATABASE_URL", config.DATABASE_URL),
        ("DATABASE_URL_SYNC", config.DATABASE_URL_SYNC),
    ):
        name = make_url(url).database or ""
        assert name.endswith("_test"), f"{label} 指向非 _test 库：{name!r}"
    assert config.POSTGRES_DATABASE.endswith("_test")


def test_global_engines_point_to_test_database() -> None:
    for label, url in (("async", engine.url), ("sync", sync_engine.url)):
        name = url.database or ""
        assert name.endswith("_test"), f"{label} 引擎指向非 _test 库：{name!r}"


def test_api_process_reads_test_database() -> None:
    """TestClient 走完整启动流程后，实际连接的仍是测试库。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        with Session(sync_engine) as session:
            name = session.execute(text("select current_database()")).scalar_one()
    assert name.endswith("_test"), f"API 实际连接了非 _test 库：{name!r}"
