"""资金流分页完整性测试（P0-14：残页缓存 / 截断页 / 200 空响应）。

真实事故：东财 clist 某页返回 200 但 diff 被截短，旧实现把它当成功页缓存；
随后 provider 层重试命中第 1 页缓存时 ``total`` 丢失，循环在第 1 页直接结束，
全市场资金流只剩 ~100 行，且当日无法自愈。
"""
from __future__ import annotations

import time

import curl_cffi.requests as curl_requests
import pytest

from app.core.errors import ProviderError
from app.ingest import akshare_provider as provider_module
from app.ingest.akshare_provider import AkshareProvider


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch):
    provider_module._EM_MF_PAGE_CACHE.clear()
    provider_module._EM_MF_TOTAL_CACHE.clear()
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    yield
    provider_module._EM_MF_PAGE_CACHE.clear()
    provider_module._EM_MF_TOTAL_CACHE.clear()


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
            "diff": [{"f12": code, "f14": f"股票{code}", "f62": 1.0} for code in codes],
        }
    }


class _FakeClist:
    """按页码回放响应序列；序列只剩一条时重复返回（用于模拟持续失败/残页）。"""

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


def test_full_pagination_and_cache_reuse(monkeypatch) -> None:
    """完整抓取 3 页；同日第二次调用命中缓存仍返回全部行，不能只剩第 1 页。"""
    codes = _codes(250)
    fake = _FakeClist({
        1: [_page(codes[:100], 250)],
        2: [_page(codes[100:200], 250)],
        3: [_page(codes[200:], 250)],
    })
    monkeypatch.setattr(curl_requests, "get", fake)
    provider = AkshareProvider()

    frame, stats = provider._em_moneyflow_df("今日")
    assert len(frame) == 250
    assert stats == {"total": 250, "fetched": 250, "pages": 3,
                     "pages_hit": 0, "pages_fetched": 3}
    assert fake.calls == [1, 2, 3]

    frame_again, stats_again = provider._em_moneyflow_df("今日")
    assert len(frame_again) == 250
    assert stats_again["pages_hit"] == 3
    assert stats_again["pages_fetched"] == 0
    assert fake.calls == [1, 2, 3]


def test_truncated_page_is_retried_and_not_cached(monkeypatch) -> None:
    """200 但 diff 被截短（30 行 < 100 且未到 total）→ 同页重试，缓存完整页。"""
    codes = _codes(250)
    fake = _FakeClist({
        1: [_page(codes[:100], 250)],
        2: [_page(codes[100:130], 250), _page(codes[100:200], 250)],
        3: [_page(codes[200:], 250)],
    })
    monkeypatch.setattr(curl_requests, "get", fake)
    provider = AkshareProvider()

    frame, _stats = provider._em_moneyflow_df("今日")
    assert len(frame) == 250
    assert fake.calls == [1, 2, 2, 3]
    date_key = time.strftime("%Y%m%d")
    assert len(provider_module._EM_MF_PAGE_CACHE[("今日", date_key, 2)]) == 100


def test_persistent_truncated_page_raises_and_is_not_cached(monkeypatch) -> None:
    """残页重试 6 次仍不完整 → ProviderError（让降级链换源），残页不入缓存。"""
    codes = _codes(250)
    fake = _FakeClist({
        1: [_page(codes[:100], 250)],
        2: [_page(codes[100:150], 250)],
    })
    monkeypatch.setattr(curl_requests, "get", fake)
    provider = AkshareProvider()

    with pytest.raises(ProviderError, match="残页"):
        provider._em_moneyflow_df("今日")
    assert fake.calls.count(2) == 6
    date_key = time.strftime("%Y%m%d")
    assert ("今日", date_key, 2) not in provider_module._EM_MF_PAGE_CACHE
    assert provider_module._EM_MF_TOTAL_CACHE[("今日", date_key)] == 250


def test_null_data_response_retried_then_succeeds(monkeypatch) -> None:
    """HTTP 200 但 data=null 是风控响应，必须重试而不是当成"榜单为空"结束。"""
    codes = _codes(120)
    fake = _FakeClist({
        1: [{"data": None}, _page(codes[:100], 120)],
        2: [_page(codes[100:], 120)],
    })
    monkeypatch.setattr(curl_requests, "get", fake)
    provider = AkshareProvider()

    frame, stats = provider._em_moneyflow_df("今日")
    assert len(frame) == 120
    assert stats["fetched"] == 120
    assert fake.calls == [1, 1, 2]


def test_cross_page_duplicates_deduped(monkeypatch) -> None:
    """翻页期间榜单轻微变动可能让同一代码出现在两页，最终按代码去重。"""
    first = [f"{index:06d}" for index in range(100)]
    second = first[99:] + [f"{index:06d}" for index in range(100, 149)]  # 含 1 个重复
    fake = _FakeClist({
        1: [_page(first, 149)],
        2: [_page(second, 149)],
    })
    monkeypatch.setattr(curl_requests, "get", fake)
    provider = AkshareProvider()

    frame, stats = provider._em_moneyflow_df("今日")
    assert len(frame) == 149 == stats["fetched"]
    assert frame["代码"].is_unique
    assert fake.calls == [1, 2]


def test_empty_market_total_zero_is_allowed(monkeypatch) -> None:
    """total=0 是合法空榜（非截断），返回空表交给上层质量校验，不无限翻页。"""
    fake = _FakeClist({1: [_page([], 0)]})
    monkeypatch.setattr(curl_requests, "get", fake)
    provider = AkshareProvider()

    frame, stats = provider._em_moneyflow_df("今日")
    assert frame.empty
    assert stats["total"] == 0
    assert fake.calls == [1]


def test_em_moneyflow_fs_covers_bse() -> None:
    """东财资金流榜单必须包含北交所（m:0+t:81+s:2048）。

    缺这一段时，日更资金流永远少 ~360 只北交所标的，
    95% 覆盖率门槛必然误伤整条日更链路（2026-11-04 事故）。
    """
    segments = provider_module._EM_MF_FS.split(",")
    assert "m:0+t:81+s:2048+f:!2" in segments
    # B 股不在股票池内，写进资金流只会污染数据
    assert "m:0+t:7+f:!2" not in segments
    assert "m:1+t:3+f:!2" not in segments
