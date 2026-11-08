"""run 信号快照 schema（P2-7）纯单测。

验证版本常量、`_run_meta` 输出与模型列约束；真实库默认值由
`tests/integration/test_run_signal_schema_sql.py` 覆盖。
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.models.strategy import StgRun  # noqa: E402
from app.strategy import strategies as strat  # noqa: E402
from app.strategy.runner import SIGNAL_SCHEMA_VERSION, _assemble, _run_meta  # noqa: E402


def _run(signal_schema: int | None = None) -> StgRun:
    run = StgRun(
        run_id="00000000-0000-0000-0000-000000000001",
        algo_code="a3",
        algo_name="多因子综合选股",
        trade_date=date(2026, 11, 6),
        params={"limit": 20},
        universe_count=10,
        signal_count=3,
        data_version="2026-11-06:abcdef",
        status="success",
        elapsed_ms=120,
        created_at=datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc),
    )
    if signal_schema is not None:
        run.signal_schema = signal_schema
    return run


def test_signal_schema_version_is_current() -> None:
    assert SIGNAL_SCHEMA_VERSION == 2


def test_run_meta_exposes_signal_schema() -> None:
    assert _run_meta(_run(1))["signal_schema"] == 1
    assert _run_meta(_run(2))["signal_schema"] == 2


def test_run_meta_falls_back_to_legacy_when_unset() -> None:
    """未落库对象取不到默认值时按旧版返回，避免前端误判为当前快照。"""
    assert _run_meta(_run())["signal_schema"] == 1


def test_assemble_exposes_signal_schema() -> None:
    """契约 5.2：run 元信息随响应返回信号快照版本。"""
    body = _assemble(_run(2), strat.get_spec("a3"), [], from_cache=False)
    assert body["run"]["signal_schema"] == 2


def test_model_column_is_not_null_with_current_default() -> None:
    column = StgRun.__table__.c.signal_schema
    assert column.nullable is False
    assert str(column.server_default.arg) == "2"
