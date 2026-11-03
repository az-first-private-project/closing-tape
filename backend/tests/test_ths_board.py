"""同花顺板块日线（THS 口径）测试（不联网、不连库）。

覆盖：
  ① ``load_board_list``：新鲜缓存直接命中、过期缓存 + 刷新失败沿用旧缓存、
     无缓存且刷新失败才抛异常；
  ② ``fetch_daily_rows``：只保留目标交易日、``pct_chg`` 用前一交易日收盘推导、
     单板块失败/空数据/挂死（墙钟超时）不阻塞其它板块；
  ③ ``service._fill_sector_rows_from_live``：按代码补齐缺失板块、不重复追加、
     实时源失败时保持原样。
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import date, timedelta

import pytest

pd = pytest.importorskip("pandas")

from app.ingest import ths_board  # noqa: E402

DAY = date(2026, 11, 2)


class _FakeLimiter:
    def __init__(self) -> None:
        self.calls = 0

    async def acquire(self) -> float:
        self.calls += 1
        return 0.0


def _boards() -> list[dict[str, str]]:
    return [
        {"sector_code": "THS_板块A", "sector_name": "板块A",
         "sector_type": "industry", "ths_code": "881001"},
        {"sector_code": "THS_板块B", "sector_name": "板块B",
         "sector_type": "concept", "ths_code": "886001"},
        {"sector_code": "THS_板块C", "sector_name": "板块C",
         "sector_type": "concept", "ths_code": "886002"},
    ]


def _history(prev_close: float, close: float) -> pd.DataFrame:
    """两行历史：前一交易日 + 目标交易日（用于推导 pre_close/pct_chg）。"""
    return pd.DataFrame(
        {
            "日期": [(DAY - timedelta(days=1)).isoformat(), DAY.isoformat()],
            "开盘价": [prev_close, prev_close],
            "最高价": [close, close * 1.01],
            "最低价": [prev_close * 0.99, prev_close],
            "收盘价": [prev_close, close],
            "成交量": [1_000_000, 2_000_000],
            "成交额": [1.0e8, 2.0e8],
        }
    )


# ============================================================
# ① load_board_list
# ============================================================
def _write_cache(path, payload, age_days: float = 0.0) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    stamp = time.time() - age_days * 86400
    import os

    os.utime(path, (stamp, stamp))


def test_load_board_list_uses_fresh_cache(monkeypatch, tmp_path) -> None:
    cache = tmp_path / "sector_board_list.json"
    _write_cache(cache, _boards(), age_days=1)
    monkeypatch.setattr(ths_board, "BOARD_LIST_CACHE", cache)

    def _boom():
        raise AssertionError("新鲜缓存不应联网刷新")

    monkeypatch.setattr(ths_board, "fetch_board_list", _boom)
    assert ths_board.load_board_list() == _boards()


def test_load_board_list_falls_back_to_stale_cache(monkeypatch, tmp_path) -> None:
    cache = tmp_path / "sector_board_list.json"
    _write_cache(cache, _boards(), age_days=30)
    monkeypatch.setattr(ths_board, "BOARD_LIST_CACHE", cache)

    def _fail():
        raise ConnectionError("10jqka 不可达")

    monkeypatch.setattr(ths_board, "fetch_board_list", _fail)
    assert ths_board.load_board_list() == _boards()


def test_load_board_list_raises_without_any_cache(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ths_board, "BOARD_LIST_CACHE", tmp_path / "missing.json")

    def _fail():
        raise ConnectionError("10jqka 不可达")

    monkeypatch.setattr(ths_board, "fetch_board_list", _fail)
    with pytest.raises(ConnectionError):
        ths_board.load_board_list()


def test_load_board_list_refresh_writes_cache(monkeypatch, tmp_path) -> None:
    cache = tmp_path / "sector_board_list.json"
    monkeypatch.setattr(ths_board, "BOARD_LIST_CACHE", cache)
    monkeypatch.setattr(ths_board, "fetch_board_list", _boards)

    assert ths_board.load_board_list() == _boards()
    assert json.loads(cache.read_text(encoding="utf-8")) == _boards()


# ============================================================
# ② fetch_daily_rows
# ============================================================
def test_fetch_daily_rows_filters_date_and_derives_pct(monkeypatch) -> None:
    limiter = _FakeLimiter()
    monkeypatch.setattr(ths_board, "HISTORY_LIMITER", limiter)
    monkeypatch.setattr(ths_board, "load_board_list", _boards)

    def fake_history(board, start, end):
        assert start == DAY - timedelta(days=10)
        assert end == DAY
        return _history(100.0, 110.0)

    monkeypatch.setattr(ths_board, "fetch_board_history", fake_history)
    rows, detail = asyncio.run(ths_board.fetch_daily_rows(DAY))

    assert [row["sector_code"] for row in rows] == [
        "THS_板块A", "THS_板块B", "THS_板块C",
    ]
    assert all(row["trade_date"] == DAY for row in rows)
    assert rows[0]["close"] == pytest.approx(110.0)
    assert rows[0]["pre_close"] == pytest.approx(100.0)
    assert rows[0]["pct_chg"] == pytest.approx(10.0)
    assert rows[0]["volume"] == 2_000_000
    assert rows[0]["src"] == "ths_hist"

    assert detail == {
        "source": "ths_hist", "boards": 3, "failed": 0, "empty": 0, "errors": {},
    }
    assert limiter.calls == 3


def test_fetch_daily_rows_isolates_failures_and_empty(monkeypatch) -> None:
    monkeypatch.setattr(ths_board, "HISTORY_LIMITER", _FakeLimiter())
    monkeypatch.setattr(ths_board, "load_board_list", _boards)

    def fake_history(board, start, end):
        if board["sector_code"] == "THS_板块C":
            raise ConnectionError("DNS failed")
        if board["sector_code"] == "THS_板块B":
            return _history(100.0, 110.0).head(1)  # 没有目标日期的行
        return _history(100.0, 110.0)

    monkeypatch.setattr(ths_board, "fetch_board_history", fake_history)
    rows, detail = asyncio.run(ths_board.fetch_daily_rows(DAY, attempts=1))

    assert [row["sector_code"] for row in rows] == ["THS_板块A"]
    assert detail["failed"] == 1
    assert detail["empty"] == 1
    assert "THS_板块C" in detail["errors"]
    assert "ConnectionError" in detail["errors"]["THS_板块C"]


def test_fetch_daily_rows_cuts_hung_board(monkeypatch) -> None:
    """单板块挂死必须被墙钟切断，不能拖住整条日更。"""
    monkeypatch.setattr(ths_board, "HISTORY_LIMITER", _FakeLimiter())
    monkeypatch.setattr(ths_board, "load_board_list", _boards)

    def fake_history(board, start, end):
        if board["sector_code"] == "THS_板块B":
            time.sleep(0.4)
            return _history(100.0, 110.0)
        return _history(100.0, 110.0)

    monkeypatch.setattr(ths_board, "fetch_board_history", fake_history)
    rows, detail = asyncio.run(
        ths_board.fetch_daily_rows(DAY, attempts=1, timeout_sec=0.05)
    )

    assert [row["sector_code"] for row in rows] == ["THS_板块A", "THS_板块C"]
    assert detail["failed"] == 1
    assert "TimeoutError" in detail["errors"]["THS_板块B"]


# ============================================================
# ③ 实时快照补齐
# ============================================================
def _live_row(code: str, name: str) -> dict:
    return {
        "sector_code": code, "trade_date": DAY, "sector_name": name,
        "sector_type": "concept", "open": None, "high": None, "low": None,
        "close": 1000.0, "pre_close": None, "pct_chg": 1.0, "amplitude": None,
        "volume": None, "amount": None, "turnover_rate": None,
        "up_count": None, "down_count": None, "leading_stock": None,
        "leading_stock_pct": None, "src": "ths",
    }


def test_fill_live_adds_missing_codes_without_duplicates(monkeypatch) -> None:
    from app.ingest import ths_sector
    from app.ingest.service import _fill_sector_rows_from_live

    async def fake_fetch():
        return pd.DataFrame(), pd.DataFrame()

    calls = {"n": 0}

    def fake_build(frame, code_map, day):
        calls["n"] += 1
        if calls["n"] == 1:  # 行业表
            return [_live_row("THS_板块A", "板块A"), _live_row("THS_板块B", "板块B")]
        return [_live_row("THS_板块B", "板块B"), _live_row("THS_板块C", "板块C")]

    monkeypatch.setattr(ths_sector, "fetch_sector_fundflow", fake_fetch)
    monkeypatch.setattr(ths_sector, "build_sector_daily_rows", fake_build)

    rows = [{"sector_code": "THS_板块A"}]
    added = asyncio.run(_fill_sector_rows_from_live(rows, DAY))

    # A 已存在；B 在两表重复只补一次；C 补入
    assert added == 2
    assert [row["sector_code"] for row in rows] == [
        "THS_板块A", "THS_板块B", "THS_板块C",
    ]


def test_fill_live_failure_keeps_rows(monkeypatch) -> None:
    from app.ingest import ths_sector
    from app.ingest.service import _fill_sector_rows_from_live

    async def fake_fetch():
        raise RuntimeError("10jqka 不可达")

    monkeypatch.setattr(ths_sector, "fetch_sector_fundflow", fake_fetch)

    rows = [{"sector_code": "THS_板块A"}]
    assert asyncio.run(_fill_sector_rows_from_live(rows, DAY)) == 0
    assert rows == [{"sector_code": "THS_板块A"}]
