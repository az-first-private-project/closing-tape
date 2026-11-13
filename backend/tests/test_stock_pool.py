"""股票池板块白名单测试（V7 T2）

产品口径：只保留沪深主板 / 创业板 / 科创板；北交所（4/8/920）不采集、
不进入股票池，也不参与广度/温度聚合。
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

pytest.importorskip("sqlalchemy", reason="sqlalchemy 未安装")

from app.core.constants import (  # noqa: E402
    BOARD_BSE,
    BOARD_GEM,
    BOARD_MAIN,
    BOARD_STAR,
    POOL_BOARDS,
    board_of,
    is_pool_stock_code,
)
from app.engine.breadth import load_breadth  # noqa: E402


def test_pool_boards_are_main_gem_star() -> None:
    assert POOL_BOARDS == (BOARD_MAIN, BOARD_GEM, BOARD_STAR)
    assert BOARD_BSE not in POOL_BOARDS


@pytest.mark.parametrize("code,board,expected", [
    ("600519", BOARD_MAIN, True),
    ("601318", BOARD_MAIN, True),
    ("000001", BOARD_MAIN, True),
    ("002594", BOARD_MAIN, True),
    ("300750", BOARD_GEM, True),
    ("301029", BOARD_GEM, True),
    ("688981", BOARD_STAR, True),
    ("689009", BOARD_STAR, True),
    ("920007", BOARD_BSE, False),
    ("430047", BOARD_BSE, False),
    ("830799", BOARD_BSE, False),
    ("870204", BOARD_BSE, False),
    ("810011", BOARD_BSE, False),
    ("200002", BOARD_MAIN, False),   # B 股不是 A 股股票
    ("900901", BOARD_MAIN, False),   # B 股
])
def test_is_pool_stock_code(code: str, board: str, expected: bool) -> None:
    assert board_of(code) == board
    assert is_pool_stock_code(code) is expected


class _FakeResult:
    def __init__(self, rows: list[tuple]):
        self._rows = rows

    def all(self) -> list[tuple]:
        return list(self._rows)


class _FakeSession:
    def __init__(self) -> None:
        self.statements: list = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _FakeResult([])


def test_load_breadth_sql_filters_to_stock_pool() -> None:
    session = _FakeSession()
    asyncio.run(load_breadth(session, date(2026, 11, 6), exclude_suspended=True))
    sql = str(session.statements[0]).lower()
    assert "dim_stock" in sql
    assert "board" in sql
    assert "status" in sql
