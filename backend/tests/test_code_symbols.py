"""代码段 → 交易所/源符号映射（920 北交所回归）。

背景：``exchange_of`` 先判断 "9 开头 = 沪市"，导致 920xxx（北交所）被映射成
``sh920000``；腾讯日线、新浪资金流、baostock 代码全部取不到数据（实测
``sh920000`` 返回空、``bj920000`` 有数据）。900xxx 是沪市 B 股，仍必须为 SH。
"""
from __future__ import annotations

import pytest

from app.core.constants import board_of, exchange_of, limit_pct_of
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
