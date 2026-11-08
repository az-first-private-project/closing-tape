"""代码段 → 交易所/源符号映射（920 北交所回归）。

背景：``exchange_of`` 先判断 "9 开头 = 沪市"，导致 920xxx（北交所）被映射成
``sh920000``；腾讯日线、新浪资金流、baostock 代码全部取不到数据（实测
``sh920000`` 返回空、``bj920000`` 有数据）。900xxx 是沪市 B 股，仍必须为 SH。
"""
from __future__ import annotations

from datetime import date

import pytest

from app.core.constants import board_of, exchange_of, is_stock_code, limit_pct_of
from app.ingest import sina_moneyflow, tencent_history, valuation


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("920000", "BJ"),
        ("920001", "BJ"),
        ("830799", "BJ"),
        ("430047", "BJ"),
        ("900901", "SH"),
        ("600519", "SH"),
        ("000001", "SZ"),
        ("300750", "SZ"),
        ("200001", "SZ"),
    ],
)
def test_exchange_of(code: str, expected: str) -> None:
    assert exchange_of(code) == expected


def test_bse_board_and_limit() -> None:
    assert board_of("920000") == "北交所"
    assert limit_pct_of("920000") == pytest.approx(0.30)


def test_sina_symbol_920_uses_bj() -> None:
    assert valuation._sina_symbol("920000") == "bj920000"
    assert valuation._sina_symbol("900901") == "sh900901"
    assert valuation._sina_symbol("830799") == "bj830799"
    assert valuation._sina_symbol("600519") == "sh600519"
    assert valuation._sina_symbol("000001") == "sz000001"


def test_provider_symbols_920_use_bj() -> None:
    assert tencent_history._symbol("920000") == "bj920000"
    assert sina_moneyflow._symbol("920000") == "bj920000"
    assert tencent_history._symbol("900901") == "sh900901"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("600519", True), ("601318", True), ("603259", True), ("605499", True),
        ("688981", True), ("689009", True),
        ("000001", True), ("001979", True), ("002594", True), ("003816", True),
        ("300750", True), ("301001", True), ("302132", True),
        ("430047", True), ("830799", True), ("872925", True), ("920000", True),
        ("810011", False), ("810013", False), ("810014", False),
        ("200002", False), ("900901", False),
    ],
)
def test_is_stock_code_excludes_bonds_and_b_shares(
    code: str, expected: bool
) -> None:
    """810xxx 是北交所定向可转债、200/900 是 B 股，都不能进 A 股股票池。"""
    assert is_stock_code(code) is expected


def test_snapshot_moneyflow_rows_skip_non_stock() -> None:
    pd = pytest.importorskip("pandas")
    from app.ingest.service import _moneyflow_rows

    frame = pd.DataFrame(
        [
            {"code": "600519", "main_net_t0": 1.0, "main_net_5d": 2.0},
            {"code": "810011", "main_net_t0": 3.0, "main_net_5d": 4.0},
            {"code": "900901", "main_net_t0": 5.0, "main_net_5d": 6.0},
            {"code": "000001", "main_net_t0": 7.0, "main_net_5d": 8.0},
            {"code": "810011", "main_net_t0": 9.0, "main_net_5d": 10.0},
        ]
    )
    rows = _moneyflow_rows(frame, date(2026, 11, 4))
    assert [row["code"] for row in rows] == ["600519", "000001"]


def test_bulk_moneyflow_rows_skip_non_stock() -> None:
    from app.ingest.bulk_backfill import _moneyflow_rows

    trade_date = date(2026, 11, 4)
    items = [{"trade_date": trade_date, "net_amount": 1.0}]
    assert _moneyflow_rows("810011", items, trade_date, trade_date) == []
    assert _moneyflow_rows("600519", items, trade_date, trade_date)[0]["code"] == "600519"
