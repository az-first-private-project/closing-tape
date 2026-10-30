"""多策略共识榜单测（V6 T6）。

只测纯聚合，不依赖数据库/网络：
  - 空输入、单策略不足 min_count、0 信号策略仍计入评估数；
  - 同一策略多次 run / 重复行只算一次，取名次最优；
  - NULL（涨跌、综合分、metrics、名称、名次）不补 0、不抛错；
  - 命中数上限、排序与 limit、metrics 摘要裁剪。
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

pytest.importorskip("sqlalchemy", reason="sqlalchemy 未安装，跳过共识榜测试")

from app.engine.consensus import (  # noqa: E402
    ConsensusSignalRow,
    build_consensus,
    metrics_preview,
)


def row(
    algo: str,
    code: str | None,
    *,
    rank: int | None = 1,
    score: object = 10.0,
    metrics: dict | None = None,
    name: str | None = None,
    stock_name: str | None = None,
    industry: str | None = None,
    pct: object = None,
    composite: object = None,
    in_sector: bool = True,
) -> ConsensusSignalRow:
    return ConsensusSignalRow(
        algo_code=algo,
        algo_name=f"{algo}-name",
        code=code,
        signal_name=name,
        rank=rank,
        score=score,
        metrics=metrics,
        stock_name=stock_name,
        industry_l1=industry,
        board="主板",
        ownership="央企",
        pct_chg=pct,
        composite_score=composite,
        in_sector=in_sector,
    )


# ============================================================
# 退化输入
# ============================================================

def test_empty_input_returns_empty_board():
    result = build_consensus([])
    assert result == {"items": [], "total_algos_evaluated": 0}


def test_single_strategy_below_min_count_keeps_evaluated_count():
    rows = [row("b1", "600000"), row("b1", "600001")]
    result = build_consensus(rows, min_count=2)
    assert result["items"] == []
    assert result["total_algos_evaluated"] == 1


def test_zero_signal_strategy_counts_as_evaluated():
    rows = [
        row("b4", None),  # 当日 run 成功但 0 信号
        row("b1", "600000", rank=1),
        row("b2", "600000", rank=2),
    ]
    result = build_consensus(rows, min_count=2)
    assert result["total_algos_evaluated"] == 3
    assert result["items"][0]["hit_count"] == 2


def test_min_count_never_exceeds_evaluated_algos():
    rows = [row(f"b{i}", "600000") for i in range(1, 8)]
    result = build_consensus(rows, min_count=8)
    assert result["items"] == []
    assert result["total_algos_evaluated"] == 7

    result = build_consensus(rows, min_count=7)
    assert result["items"][0]["hit_count"] == 7


# ============================================================
# 聚合口径
# ============================================================

def test_hit_count_counts_distinct_algos_and_no_cross_avg():
    rows = [
        row("b1", "600000", rank=3, score=11.0),
        row("b2", "600000", rank=1, score=18.0),
        row("b3", "600000", rank=2, score=4.5),
        row("b1", "600001", rank=1),
        row("b2", "600001", rank=2),
        row("b1", "600002", rank=1),
    ]
    result = build_consensus(rows, min_count=2)
    assert [item["code"] for item in result["items"]] == ["600000", "600001"]
    top = result["items"][0]
    assert top["hit_count"] == 3
    assert top["best_rank"] == 1
    assert [hit["algo_code"] for hit in top["algos"]] == ["b2", "b3", "b1"]
    assert "avg_score" not in top
    assert "avg_score" not in top["algos"][0]


def test_duplicate_algo_rows_count_once_and_keep_best_rank():
    rows = [
        row("b1", "600000", rank=5, score=3.0, metrics={"z": 1}),
        row("b1", "600000", rank=2, score=9.0, metrics={"a": 2}),
        row("b2", "600000", rank=4),
    ]
    result = build_consensus(rows, min_count=1)
    item = result["items"][0]
    assert item["hit_count"] == 2
    assert item["best_rank"] == 2
    b1 = next(hit for hit in item["algos"] if hit["algo_code"] == "b1")
    assert b1["rank"] == 2
    assert b1["score"] == 9.0
    assert b1["metrics"] == {"a": 2}


def test_sector_filter_excludes_items_but_not_evaluated_count():
    rows = [
        row("b1", "600000", in_sector=False),
        row("b2", "600000", in_sector=False),
        row("b2", "600001", in_sector=True),
        row("b3", "600001", in_sector=True),
    ]
    result = build_consensus(rows, min_count=2)
    assert [item["code"] for item in result["items"]] == ["600001"]
    assert result["total_algos_evaluated"] == 3


# ============================================================
# NULL 与数值
# ============================================================

def test_null_fields_are_preserved_not_zeroed():
    rows = [
        row(
            "b1", "600000", rank=None, score=None, metrics=None,
            name=None, stock_name=None, industry=None,
            pct=None, composite=None,
        ),
        row("b2", "600000", rank=None, score=None, metrics={}),
    ]
    item = build_consensus(rows, min_count=2)["items"][0]
    assert item["name"] == "600000"
    assert item["industry_l1"] is None
    assert item["pct_chg"] is None
    assert item["composite_score"] is None
    assert item["best_rank"] is None
    assert item["algos"][0]["rank"] is None
    assert item["algos"][0]["score"] is None
    assert item["algos"][0]["metrics"] == {}


def test_name_falls_back_to_signal_name_then_code():
    rows = [
        row("b1", "600000", name="信号名"),
        row("b2", "600000"),
    ]
    item = build_consensus(rows, min_count=2)["items"][0]
    assert item["name"] == "信号名"

    rows = [
        row("b1", "600000", stock_name="维度名", name="信号名"),
        row("b2", "600000"),
    ]
    item = build_consensus(rows, min_count=2)["items"][0]
    assert item["name"] == "维度名"


def test_zero_pct_and_decimal_scores_are_kept():
    rows = [
        row("b1", "600000", score=Decimal("8.125"), pct=Decimal("0.000"), composite=Decimal("6.500")),
        row("b2", "600000", score=Decimal("7.000")),
    ]
    item = build_consensus(rows, min_count=2)["items"][0]
    assert item["pct_chg"] == 0.0
    assert item["composite_score"] == 6.5
    assert item["algos"][0]["score"] == 8.125


def test_metrics_preview_limits_and_sorts_keys():
    assert metrics_preview({"z": 1, "a": 2, "m": 3, "b": 4}) == {"a": 2, "b": 4, "m": 3}
    assert metrics_preview({"z": 1, "a": 2}) == {"a": 2, "z": 1}
    assert metrics_preview(None) == {}
    assert metrics_preview("not-a-dict") == {}
    assert metrics_preview({"a": 1}, limit=0) == {}


# ============================================================
# 排序与截断
# ============================================================

def test_sort_by_hits_then_best_rank_then_composite_then_code():
    rows = [
        row("b1", "600004", rank=5, composite=9.0),
        row("b2", "600004", rank=5, composite=9.0),
        row("b3", "600004", rank=5, composite=9.0),
        row("b1", "600002", rank=1, composite=8.0),
        row("b2", "600002", rank=1, composite=8.0),
        row("b1", "600003", rank=1, composite=None),
        row("b2", "600003", rank=1, composite=None),
        row("b1", "600001", rank=1, composite=9.0),
        row("b2", "600001", rank=1, composite=9.0),
    ]
    result = build_consensus(rows, min_count=2)
    assert [item["code"] for item in result["items"]] == [
        "600004",  # 3 家命中
        "600001",  # 2 家、最优名次 1、综合分 9
        "600002",  # 2 家、最优名次 1、综合分 8
        "600003",  # 2 家、最优名次 1、综合分缺失
    ]


def test_limit_truncates_items_but_not_total():
    rows = []
    for index in range(5):
        code = f"60000{index}"
        rows.append(row("b1", code))
        rows.append(row("b2", code))
    result = build_consensus(rows, min_count=2, limit=2)
    assert len(result["items"]) == 2
    assert result["total_algos_evaluated"] == 2


def test_negative_limit_returns_no_items():
    rows = [row("b1", "600000"), row("b2", "600000")]
    assert build_consensus(rows, min_count=2, limit=-1)["items"] == []
