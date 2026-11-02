"""策略 run 幂等单测（不依赖数据库）。

覆盖：
  - 新 run 成功后只取代同参数旧 run，不同参数不受影响；
  - 当前 run 不会被自己取代；
  - 存量清理脚本按“最新在前”每组保留第一条。
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
SCRIPTS_DIR = BACKEND_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from app.strategy.runner import params_fingerprint, superseded_run_ids  # noqa: E402
from dedupe_strategy_runs import plan_supersede  # noqa: E402


def test_supersede_only_same_params() -> None:
    fingerprint = params_fingerprint("a3", {"min_score": 60})
    candidates = [
        ("old", {"min_score": 60}),
        ("custom", {"min_score": 50}),
    ]
    assert superseded_run_ids("new", "a3", fingerprint, candidates) == ["old"]


def test_supersede_ignores_current_run() -> None:
    fingerprint = params_fingerprint("b1", {})
    assert superseded_run_ids("new", "b1", fingerprint, [("new", {})]) == []


def test_plan_supersede_keeps_newest_per_group() -> None:
    day = date(2026, 10, 30)
    rows = [
        ("new-a3", "a3", day, {"min_score": 60}, datetime(2026, 10, 30, 12, 0)),
        ("old-a3", "a3", day, {"min_score": 60}, datetime(2026, 10, 30, 9, 0)),
        ("custom", "a3", day, {"min_score": 50}, datetime(2026, 10, 30, 8, 0)),
        ("new-b1", "b1", day, {}, datetime(2026, 10, 30, 12, 0)),
        ("old-b1", "b1", day, {}, datetime(2026, 10, 30, 9, 0)),
    ]

    keep, stale, by_algo = plan_supersede(rows)

    assert stale == ["old-a3", "old-b1"]
    assert keep[("a3", day, params_fingerprint("a3", {"min_score": 60}))] == "new-a3"
    assert keep[("a3", day, params_fingerprint("a3", {"min_score": 50}))] == "custom"
    assert by_algo == {"a3": 1, "b1": 1}
