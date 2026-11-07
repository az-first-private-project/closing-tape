"""任务与数据质量状态枚举（W0-4）。

状态字符串的唯一来源：``run_task`` 返回体、``ingest_job.status``、
``meta_data_quality.status``。新增状态必须先在这里登记，迁移表声明终态，
避免同一个词在不同模块被写成不同拼写。
"""
from __future__ import annotations

from enum import Enum
from typing import Final


class TaskStatus(str, Enum):
    """单次任务运行状态（``run_task`` / ``ingest_job`` / ``stg_run``）。"""

    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class QualityStatus(str, Enum):
    """``meta_data_quality.status`` 的取值（前端新鲜度徽标）。"""

    OK = "ok"
    STALE = "stale"
    QUALITY_FAILED = "quality_failed"
    ERROR = "error"
    UNKNOWN = "unknown"


# 任务终态不允许回到 running：重跑必须新建一条 run，不能改写审计记录。
TASK_TRANSITIONS: Final[dict[TaskStatus, frozenset[TaskStatus]]] = {
    TaskStatus.RUNNING: frozenset({
        TaskStatus.SUCCESS, TaskStatus.PARTIAL, TaskStatus.FAILED, TaskStatus.SKIPPED,
    }),
    TaskStatus.SUCCESS: frozenset(),
    TaskStatus.PARTIAL: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.SKIPPED: frozenset(),
}

# 新鲜度是"当前值"覆盖写：允许同状态重复写；unknown 只作为初始值，不能被写回。
QUALITY_TRANSITIONS: Final[dict[QualityStatus, frozenset[QualityStatus]]] = {
    QualityStatus.UNKNOWN: frozenset({
        QualityStatus.OK, QualityStatus.STALE,
        QualityStatus.QUALITY_FAILED, QualityStatus.ERROR,
    }),
    QualityStatus.OK: frozenset({
        QualityStatus.OK, QualityStatus.STALE,
        QualityStatus.QUALITY_FAILED, QualityStatus.ERROR,
    }),
    QualityStatus.STALE: frozenset({
        QualityStatus.OK, QualityStatus.STALE,
        QualityStatus.QUALITY_FAILED, QualityStatus.ERROR,
    }),
    QualityStatus.QUALITY_FAILED: frozenset({
        QualityStatus.OK, QualityStatus.STALE,
        QualityStatus.QUALITY_FAILED, QualityStatus.ERROR,
    }),
    QualityStatus.ERROR: frozenset({
        QualityStatus.OK, QualityStatus.STALE,
        QualityStatus.QUALITY_FAILED, QualityStatus.ERROR,
    }),
}


def can_transition_task(previous: TaskStatus | str | None, current: TaskStatus | str) -> bool:
    """新记录（previous 为 None）可进入任意状态；否则按迁移表判断。"""
    current_status = TaskStatus(current)
    if previous is None:
        return True
    return current_status in TASK_TRANSITIONS[TaskStatus(previous)]


def can_transition_quality(
    previous: QualityStatus | str | None, current: QualityStatus | str
) -> bool:
    current_status = QualityStatus(current)
    if previous is None:
        return True
    return current_status in QUALITY_TRANSITIONS[QualityStatus(previous)]


__all__ = [
    "TaskStatus", "QualityStatus", "TASK_TRANSITIONS", "QUALITY_TRANSITIONS",
    "can_transition_task", "can_transition_quality",
]
