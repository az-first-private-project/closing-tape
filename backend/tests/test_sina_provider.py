"""新浪概念成分兜底测试（P0-19：东财清单未覆盖板块的日更补充源）。

只做 gn_/chgn_ 概念节点的精确同名匹配；不完整翻页必须报错而不是返回残页，
否则 SCD2 差集会把缺失成分误判为"已移除"。
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import date

import pytest

from app.core.errors import ProviderError
from app.ingest import sina_provider
from app.ingest.base import FetchResult, ProviderChain
from app.ingest.sina_provider import SinaProvider, build_node_map


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch):
    sina_provider._NODE_CACHE.clear()
    sina_provider._CONS_BREAKER.update({"streak": 0, "until": 0.0})
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    yield
    sina_provider._NODE_CACHE.clear()
    sina_provider._CONS_BREAKER.update({"streak": 0, "until": 0.0})


class _Response:
    def __init__(self, payload):
        self._payload = payload

    @property
    def text(self) -> str:
        return json.dumps(self._payload, ensure_ascii=False)

    def raise_for_status(self) -> None:
        return None


class _FakeSina:
    def __init__(self, nodes, script: dict[int, list] | None = None) -> None:
        self.nodes = nodes
        self.script = {int(page): list(items) for page, items in (script or {}).items()}
        self.calls: list[int] = []
        self.node_calls = 0

    def __call__(self, url, params=None, headers=None, timeout=None):
        if url.endswith("Market_Center.getHQNodes"):
            self.node_calls += 1
            if isinstance(self.nodes, Exception):
                raise self.nodes
            return _Response(self.nodes)
        page = int(params["page"])
        self.calls.append(page)
        items = self.script[page]
        item = items.pop(0) if len(items) > 1 else items[0]
        if isinstance(item, Exception):
            raise item
        return _Response(item)


def _nodes(*groups) -> list:
    return ["行情中心", [["A股", [["概念板块", list(groups)]]]]]


def _rows(codes: list[str]) -> list[dict]:
    return [{"symbol": f"sh{c}", "code": c, "name": f"股票{c}"} for c in codes]


def _codes(count: int, start: int = 0) -> list[str]:
    return [f"{index:06d}" for index in range(start, start + count)]


def test_node_map_keeps_only_concept_nodes_and_prefers_gn() -> None:
    payload = _nodes(
        ["白酒", "", "chgn_700182"],
        ["白酒", "", "gn_baijiu"],
        ["保险", "", "hk_hshy502"],
        ["塑料制品", "", "new_slzp"],
        ["PM2.5", "", "chgn_730615"],
    )

    mapping = build_node_map(payload)

    assert mapping == {"白酒": "gn_baijiu", "PM2.5": "chgn_730615"}


def test_industry_type_skipped_without_network(monkeypatch) -> None:
    fake = _FakeSina(_nodes())
    monkeypatch.setattr(sina_provider.httpx, "get", fake)

    result = SinaProvider().fetch_sector_members("881101", "白酒", "industry")

    assert result.rows == 0
    assert result.detail["skip_reason"] == "sina_unsupported_type"
    assert fake.node_calls == 0 and fake.calls == []


def test_name_not_found_returns_skip(monkeypatch) -> None:
    fake = _FakeSina(_nodes(["PM2.5", "", "chgn_730615"]))
    monkeypatch.setattr(sina_provider.httpx, "get", fake)

    result = SinaProvider().fetch_sector_members("885001", "不存在", "concept")

    assert result.rows == 0
    assert result.detail["skip_reason"] == "sina_name_not_found"
    assert result.detail["data"].empty
    assert fake.calls == []


def test_two_pages_assembled(monkeypatch) -> None:
    fake = _FakeSina(
        _nodes(["光热发电", "", "chgn_701026"]),
        {1: [_rows(_codes(100))], 2: [_rows(_codes(50, 100))]},
    )
    monkeypatch.setattr(sina_provider.httpx, "get", fake)

    result = SinaProvider().fetch_sector_members("885002", "光热发电", "concept")

    assert result.rows == 150
    assert fake.calls == [1, 2]
    frame = result.detail["data"]
    assert set(frame.columns) == {"sector_code", "sector_name", "sector_type",
                                  "stock_code", "stock_name"}
    assert frame["stock_code"].iloc[0] == "000000"
    assert (frame["sector_code"] == "885002").all()


def test_endless_pages_raise_instead_of_truncating(monkeypatch) -> None:
    """每页 100 行且永远有下一页：到上限报错，不返回残缺名单。"""
    fake = _FakeSina(
        _nodes(["光热发电", "", "chgn_701026"]),
        {page: [_rows(_codes(100, page * 1000))]
         for page in range(1, sina_provider._CONS_MAX_PAGES + 1)},
    )
    monkeypatch.setattr(sina_provider.httpx, "get", fake)

    with pytest.raises(ProviderError, match="不完整"):
        SinaProvider().fetch_sector_members("885002", "光热发电", "concept")


def test_null_page_raises(monkeypatch) -> None:
    """200 但返回 null 是风控/接口异常，按失败处理，不能当成尾页。"""
    fake = _FakeSina(
        _nodes(["光热发电", "", "chgn_701026"]),
        {1: [_rows(_codes(100))], 2: [None]},
    )
    monkeypatch.setattr(sina_provider.httpx, "get", fake)

    with pytest.raises(ProviderError, match="返回异常"):
        SinaProvider().fetch_sector_members("885002", "光热发电", "concept")


def test_node_refresh_failure_uses_stale_cache_with_cooldown(monkeypatch) -> None:
    """清单刷新失败：沿用旧映射，并在冷却期内不再发起网络请求（防重试风暴）。"""
    sina_provider._NODE_CACHE["map"] = (date(2000, 1, 1), {"光热发电": "chgn_701026"})
    fake = _FakeSina(ConnectionError("DNS 抖动"))
    monkeypatch.setattr(sina_provider.httpx, "get", fake)

    provider = SinaProvider()
    assert provider._node_map() == {"光热发电": "chgn_701026"}
    assert provider._node_map() == {"光热发电": "chgn_701026"}
    assert fake.node_calls == 1


def test_consecutive_failures_open_breaker(monkeypatch) -> None:
    """源整体不可用时快速失败：连续 3 次失败后不再发请求，把预算留给东财。"""
    fake = _FakeSina(
        _nodes(["光热发电", "", "chgn_701026"]),
        {1: [ConnectionError("连接被重置")]},
    )
    monkeypatch.setattr(sina_provider.httpx, "get", fake)
    provider = SinaProvider()

    for _ in range(3):
        with pytest.raises(ConnectionError):
            provider.fetch_sector_members("885002", "光热发电", "concept")

    with pytest.raises(ProviderError, match="冷却中"):
        provider.fetch_sector_members("885002", "光热发电", "concept")
    assert fake.calls == [1, 1, 1]


def test_chain_uses_sina_after_primary_skip(monkeypatch) -> None:
    """东财返回 skip_reason 时，降级链继续尝试新浪并采用其成分。"""
    fake = _FakeSina(
        _nodes(["PM2.5", "", "chgn_730615"]),
        {1: [_rows(_codes(3))]},
    )
    monkeypatch.setattr(sina_provider.httpx, "get", fake)

    class _SkipProvider:
        name = "akshare"

        def available(self) -> bool:
            return True

        def fetch_sector_members(self, *args, **kwargs) -> FetchResult:
            return FetchResult(rows=0, total=0,
                               detail={"data": None, "skip_reason": "em_name_not_found"})

    chain = ProviderChain([_SkipProvider(), SinaProvider()])
    result = asyncio.run(chain.fetch_sector_members("885003", "PM2.5", "concept"))

    assert result.rows == 3
    assert result.detail["source"] == "sina"
    assert [item["status"] for item in result.detail["attempts"]] == ["empty", "ok"]
