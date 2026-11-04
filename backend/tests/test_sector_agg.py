"""板块聚合指标拼装测试（P0-20：THS 不提供涨跌家数，按成分重算）。"""
from __future__ import annotations

from app.engine.sector_agg import MIN_MEMBERS, build_sector_metrics


def _agg(**overrides) -> dict:
    base = {
        "member_count": 5,
        "market_cap": 1.0e10,
        "float_cap": 8.0e9,
        "main_net_inflow": 1.0e8,
        "pct_n": 5,
        "up_count": 3,
        "down_count": 2,
        "has_partial": False,
    }
    base.update(overrides)
    return base


def test_breadth_counts_computed_from_members() -> None:
    metrics = build_sector_metrics(["BK1"], {"BK1": _agg()}, {})

    assert metrics[0]["up_count"] == 3
    assert metrics[0]["down_count"] == 2


def test_partial_members_do_not_produce_breadth() -> None:
    """纯 partial 板块（没有任何完整成分）整体置空。"""
    metrics = build_sector_metrics(
        ["BK1"],
        {"BK1": _agg(has_partial=True, member_count=0, pct_n=0,
                     up_count=0, down_count=0)},
        {},
    )

    assert metrics[0]["member_count"] is None
    assert metrics[0]["up_count"] is None
    assert metrics[0]["down_count"] is None


def test_mixed_membership_uses_complete_list() -> None:
    """历史 partial 残留 + 完整名单：以完整名单为准，不能整块置空。"""
    metrics = build_sector_metrics(
        ["BK1"],
        {"BK1": _agg(has_partial=True, leader_code="600000",
                     leader_name="浦发银行", leader_pct=9.9)},
        {},
    )

    assert metrics[0]["member_count"] == 5
    assert metrics[0]["up_count"] == 3
    assert metrics[0]["leading_stock"] == "浦发银行"


def test_missing_daily_rows_do_not_fake_zero_breadth() -> None:
    """成分股当日无行情（全部停牌/数据缺失）时家数为 NULL，不能写成 0。"""
    metrics = build_sector_metrics(
        ["BK1"], {"BK1": _agg(pct_n=0, up_count=0, down_count=0)}, {}
    )

    assert metrics[0]["up_count"] is None
    assert metrics[0]["down_count"] is None


def test_small_board_keeps_breadth_but_not_market_cap() -> None:
    """成分数 < MIN_MEMBERS 时市值/资金流置 NULL，涨跌家数仍然有意义。"""
    metrics = build_sector_metrics(
        ["BK1"],
        {"BK1": _agg(member_count=2, pct_n=2, up_count=1, down_count=1)},
        {},
    )

    assert metrics[0]["member_count"] == 2
    assert metrics[0]["market_cap"] is None
    assert metrics[0]["main_net_inflow"] is None
    assert (metrics[0]["up_count"], metrics[0]["down_count"]) == (1, 1)
    assert MIN_MEMBERS == 3


def test_board_without_membership_stays_empty() -> None:
    metrics = build_sector_metrics(["BK9"], {}, {"BK9": 1.5})

    assert metrics[0]["member_count"] == 0
    assert metrics[0]["up_count"] is None
    assert metrics[0]["rs_20d"] == 1.5


def test_leader_taken_from_top_gaining_member() -> None:
    metrics = build_sector_metrics(
        ["BK1"],
        {"BK1": _agg(leader_code="600000", leader_name="浦发银行",
                     leader_pct=9.981)},
        {},
    )

    assert metrics[0]["leading_stock"] == "浦发银行"
    assert metrics[0]["leading_stock_pct"] == 9.981


def test_leader_falls_back_to_code_when_dim_stock_missing() -> None:
    """dim_stock 关联不到名称时 SQL 已退回代码，拼装层原样输出。"""
    metrics = build_sector_metrics(
        ["BK1"], {"BK1": _agg(leader_code="920680", leader_name="920680",
                              leader_pct=3.2)}, {}
    )

    assert metrics[0]["leading_stock"] == "920680"
    assert metrics[0]["leading_stock_pct"] == 3.2


def test_pure_partial_board_does_not_produce_leader() -> None:
    """残缺名单的涨幅第一不是板块真龙头，纯 partial 板块必须置空。"""
    metrics = build_sector_metrics(
        ["BK1"],
        {"BK1": _agg(has_partial=True, member_count=0, pct_n=0,
                     leader_code="600000", leader_name="浦发银行",
                     leader_pct=9.98)},
        {},
    )

    assert metrics[0]["leading_stock"] is None
    assert metrics[0]["leading_stock_pct"] is None


def test_board_without_pct_data_keeps_leader_null() -> None:
    metrics = build_sector_metrics(["BK1"], {"BK1": _agg()}, {})

    assert metrics[0]["leading_stock"] is None
    assert metrics[0]["leading_stock_pct"] is None
