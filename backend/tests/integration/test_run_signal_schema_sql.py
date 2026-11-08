"""`stg_run.signal_schema` 真实库列与默认值（P2-7）。

仅在 ``GFNA_TEST_DB=1`` 且 gfna_test 已迁移时运行；外层事务回滚。
"""
from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from app.db.models.strategy import StgRun

pytestmark = pytest.mark.asyncio


def _run(**kwargs) -> StgRun:
    return StgRun(
        run_id=str(uuid4()),
        algo_code="a3",
        trade_date=date(2026, 11, 6),
        params={},
        status="success",
        **kwargs,
    )


async def test_signal_schema_defaults_to_current(db_session):
    implicit = _run()
    legacy = _run(signal_schema=1)
    db_session.add_all([implicit, legacy])
    await db_session.flush()
    await db_session.refresh(implicit)
    await db_session.refresh(legacy)

    assert implicit.signal_schema == 2
    assert legacy.signal_schema == 1
