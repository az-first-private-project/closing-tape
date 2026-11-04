"""板块成分直连分页测试（P0-18：成分残缺会误关 SCD2 区间，必须零容差）。

akshare 的 cons 接口每页 100 行且翻页间 sleep 0.5~1.5s，480 个板块一轮超过
采集预算；这里改为直连东财 clist 自行翻页，并校验每页与 total 自洽。
"""
from __future__ import annotations

import time

import curl_cffi.requests as curl_requests
import pytest

from app.core.errors import ProviderError
from app.ingest.akshare_provider import AkshareProvider


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


def _page(codes: list[str], total: int) -> dict:
    return {
        "data": {
            "total": total,
            "diff": [{"f12": code, "f14": f"股票{code}"} for code in codes],
        }
    }


class _FakeClist:
    """按页码回放响应序列；序列只剩一条时重复返回（模拟持续残页）。"""

    def __init__(self, script: dict[int, list]) -> None:
        self.script = {int(pn): list(items) for pn, items in script.items()}
        self.calls: list[int] = []

    def __call__(self, url, params=None, **kwargs):
        pn = int(params["pn"])
        self.calls.append(pn)
        items = self.script[pn]
        item = items.pop(0) if len(items) > 1 else items[0]
        if isinstance(item, Exception):
            raise item
        return _Response(item)


def _codes(count: int) -> list[str]:
    return [f"{index:06d}" for index in range(count)]


def test_two_pages_assembled(monkeypatch) -> None:
    codes = _codes(150)
    fake = _FakeClist({
        1: [_page(codes[:100], 150)],
        2: [_page(codes[100:], 150)],
    })
    monkeypatch.setattr(curl_requests, "get", fake)

    frame = AkshareProvider()._em_board_cons_df("BK0473")

    assert len(frame) == 150
    assert set(frame.columns) == {"代码", "名称"}
    assert frame["代码"].iloc[0] == "000000"
    assert fake.calls == [1, 2]


def test_truncated_page_is_retried(monkeypatch) -> None:
    """本页 30 行 < 100 且未到 total → 同页重试，最终拿全。"""
    codes = _codes(150)
    fake = _FakeClist({
        1: [_page(codes[:100], 150)],
        2: [_page(codes[100:130], 150), _page(codes[100:], 150)],
    })
    monkeypatch.setattr(curl_requests, "get", fake)

    frame = AkshareProvider()._em_board_cons_df("BK0473")

    assert len(frame) == 150
    assert fake.calls == [1, 2, 2]


def test_persistent_short_page_fails_whole_board(monkeypatch) -> None:
    """残页重试 4 次仍不完整 → ProviderError，整板按失败处理（不动已有区间）。"""
    codes = _codes(150)
    fake = _FakeClist({
        1: [_page(codes[:100], 150)],
        2: [_page(codes[100:120], 150)],
    })
    monkeypatch.setattr(curl_requests, "get", fake)

    with pytest.raises(ProviderError, match="残页"):
        AkshareProvider()._em_board_cons_df("BK0473")
    assert fake.calls.count(2) == 4


def test_duplicate_rows_below_total_fails(monkeypatch) -> None:
    """跨页重复导致去重后少于 total：宁可整板失败，也不交付残缺名单。"""
    codes = _codes(120)
    second = codes[:20]  # 与第 1 页完全重复
    fake = _FakeClist({
        1: [_page(codes[:100], 120)],
        2: [_page(second, 120)],
        3: [_page([], 120)],
    })
    monkeypatch.setattr(curl_requests, "get", fake)

    with pytest.raises(ProviderError):
        AkshareProvider()._em_board_cons_df("BK0473")


def test_empty_board_returns_empty_frame(monkeypatch) -> None:
    """total=0 且 diff=null 是合法空板块，不能无限翻页。"""
    fake = _FakeClist({1: [{"data": {"total": 0, "diff": None}}]})
    monkeypatch.setattr(curl_requests, "get", fake)

    frame = AkshareProvider()._em_board_cons_df("BK0473")

    assert frame.empty
    assert fake.calls == [1]


def test_board_list_maps_code_and_name(monkeypatch) -> None:
    """板块清单直连：f12→板块代码、f14→板块名称，供名称归一化映射使用。"""
    fake = _FakeClist({1: [_page(["BK0473", "BK0475"], 2)]})
    monkeypatch.setattr(curl_requests, "get", fake)

    frame = AkshareProvider()._em_board_list_df("industry")

    assert list(frame.columns) == ["板块名称", "板块代码"]
    assert frame["板块代码"].tolist() == ["BK0473", "BK0475"]
    assert frame["板块名称"].tolist() == ["股票BK0473", "股票BK0475"]
