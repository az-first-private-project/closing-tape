"""状态枚举与迁移表（W0-4）。"""
from __future__ import annotations

import json
from pathlib import Path

from app.core.status import (
    QUALITY_TRANSITIONS, TASK_TRANSITIONS, QualityStatus, TaskStatus,
    can_transition_quality, can_transition_task,
)


def test_enums_serialize_as_plain_strings() -> None:
    assert TaskStatus.SUCCESS == "success"
    assert QualityStatus.QUALITY_FAILED == "quality_failed"
    assert json.dumps({"status": TaskStatus.PARTIAL}) == '{"status": "partial"}'


def test_task_transitions_only_leave_running() -> None:
    assert can_transition_task(None, TaskStatus.SUCCESS)
    for status in (TaskStatus.SUCCESS, TaskStatus.PARTIAL,
                   TaskStatus.FAILED, TaskStatus.SKIPPED):
        assert can_transition_task(TaskStatus.RUNNING, status)
        assert can_transition_task(status, status) is False

    assert not can_transition_task(TaskStatus.SUCCESS, TaskStatus.RUNNING)
    assert TASK_TRANSITIONS[TaskStatus.SUCCESS] == frozenset()


def test_quality_transitions_allow_recovery_but_not_unknown() -> None:
    assert can_transition_quality(None, QualityStatus.OK)
    assert can_transition_quality(QualityStatus.ERROR, QualityStatus.OK)
    assert can_transition_quality(QualityStatus.OK, QualityStatus.STALE)
    assert can_transition_quality(QualityStatus.OK, QualityStatus.OK)
    assert not can_transition_quality(QualityStatus.OK, QualityStatus.UNKNOWN)
    assert not can_transition_quality(QualityStatus.ERROR, QualityStatus.UNKNOWN)
    assert QUALITY_TRANSITIONS[QualityStatus.UNKNOWN] == frozenset({
        QualityStatus.OK, QualityStatus.STALE,
        QualityStatus.QUALITY_FAILED, QualityStatus.ERROR,
    })


def test_meta_data_quality_has_a_single_writer() -> None:
    """所有新鲜度写入必须走 write_freshness，其他模块不得直接 upsert 该表。"""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    writers = {
        path.relative_to(app_dir).as_posix()
        for path in app_dir.rglob("*.py")
        if "pg_insert(MetaDataQuality)" in path.read_text(encoding="utf-8")
    }
    assert writers == {"services/freshness.py"}
