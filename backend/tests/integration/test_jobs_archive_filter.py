"""W0-5：归档任务不出现在任务监控默认列表，显式过滤仍可审计查询。"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.api.v1.meta import list_jobs
from app.db.models import IngestJob

pytestmark = pytest.mark.asyncio

TASK = "w05_archive_filter_test"


async def _seed(db) -> None:
    db.add_all(
        [
            IngestJob(
                job_id="dddd0001-0000-4000-8000-000000000001",
                task=TASK,
                dataset="stock_daily",
                status="success",
                total=1,
                done=1,
                started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
            IngestJob(
                job_id="dddd0002-0000-4000-8000-000000000002",
                task=TASK,
                dataset="stock_daily",
                status="archived",
                total=1,
                done=0,
                started_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
            ),
        ]
    )
    await db.flush()


async def test_archived_jobs_hidden_by_default_but_queryable(db_session) -> None:
    await _seed(db_session)

    default_view = await list_jobs(limit=50, task=TASK, status=None, db=db_session)
    assert [item["status"] for item in default_view["items"]] == ["success"]

    archived = await list_jobs(
        limit=50, task=TASK, status="archived", db=db_session
    )
    assert [item["status"] for item in archived["items"]] == ["archived"]
