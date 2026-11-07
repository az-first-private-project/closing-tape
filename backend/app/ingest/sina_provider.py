"""新浪概念板块成分兜底源（仅 gn_/chgn_ 精确同名）。

东财板块清单不包含部分同花顺概念板块（口径差异），这些板块在日更里永远拿不到
成分。新浪的 gn_/chgn_ 概念节点与同花顺有大量完全同名的情况，只做**精确同名**
匹配，不做跨分类映射：新浪的行业节点（new_/sw*/hk_*）与同花顺行业分类口径不同，
混用会把不同口径的成分写进同一板块。
"""
from __future__ import annotations

import json
import time
from datetime import date
from typing import Any

import httpx
import pandas as pd

from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.ingest.base import BaseProvider, FetchResult

logger = get_logger(__name__)

SINA_API = "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
SINA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Referer": "http://vip.stock.finance.sina.com.cn/mkt/",
}
SECTOR_MEMBER_COLUMNS = ("sector_code", "sector_name", "sector_type", "stock_code", "stock_name")

_CONS_PAGE_SIZE = 100
_CONS_MAX_PAGES = 20
_CONS_PAUSE_SEC = 0.35
# 失败冷却：避免 480 个板块逐个重试把一次 DNS 抖动/源限流放大成重试风暴
_FAIL_COOLDOWN_SEC = 300.0
_CONS_FAIL_THRESHOLD = 3

# "map": (清单日期, 名称→node)；"failed_at": 最近一次刷新失败的 monotonic 时间
_NODE_CACHE: dict[str, Any] = {}
# 成分抓取连续失败计数与冷却截止时间（源整体不可用时快速失败，把预算留给东财）
_CONS_BREAKER: dict[str, float] = {"streak": 0, "until": 0.0}


def _walk_nodes(node: Any, out: list[tuple[str, str]]) -> None:
    """递归展开 getHQNodes 的嵌套结构，收集 [名称, 附加, node] 三元组。"""
    if not isinstance(node, list):
        return
    if len(node) == 3 and isinstance(node[0], str) and isinstance(node[2], str) and node[2]:
        out.append((node[0], node[2]))
        return
    for item in node:
        _walk_nodes(item, out)


def build_node_map(payload: Any) -> dict[str, str]:
    """只保留 gn_/chgn_ 概念节点；同名优先 gn_（与 import_sina_members 口径一致）。"""
    triples: list[tuple[str, str]] = []
    _walk_nodes(payload, triples)
    chosen: dict[str, str] = {}
    for name, node in triples:
        if not name or not (node.startswith("gn_") or node.startswith("chgn_")):
            continue
        if name not in chosen or (node.startswith("gn_") and not chosen[name].startswith("gn_")):
            chosen[name] = node
    return chosen


class SinaProvider(BaseProvider):
    """新浪概念板块成分兜底源（仅 ``concept`` 板块）。

    日更链里排在东财之后：东财清单找不到的名字，用新浪精确同名节点再试一次；
    仍匹配不到时返回 ``skip_reason``，由编排层按"未匹配"处理（不计失败、不熔断）。
    """

    name = "sina"

    def available(self) -> bool:
        return True

    # ---------- 内部 ----------
    def _get_json(self, path: str, params: dict[str, Any]) -> Any:
        # trust_env=False：本机 NO_PROXY 含 "[::1]" 时 httpx 构造 Client
        # 会抛 InvalidURL，导致兜底源整体不可用；新浪接口应直连。
        response = httpx.get(
            SINA_API + path,
            params=params,
            headers=SINA_HEADERS,
            timeout=12,
            trust_env=False,
        )
        response.raise_for_status()
        return json.loads(response.text)

    def _node_map(self) -> dict[str, str]:
        cached: tuple[date, dict[str, str]] | None = _NODE_CACHE.get("map")
        if cached and cached[0] == date.today():
            return cached[1]
        failed_at = _NODE_CACHE.get("failed_at")
        if failed_at is not None and time.monotonic() - failed_at < _FAIL_COOLDOWN_SEC:
            if cached:
                return cached[1]
            raise ProviderError("新浪板块节点清单刷新失败（冷却中）")
        try:
            mapping = build_node_map(self._get_json("Market_Center.getHQNodes", {}))
            if not mapping:
                raise ProviderError("新浪板块节点清单为空")
        except Exception:
            _NODE_CACHE["failed_at"] = time.monotonic()
            if cached:
                logger.warning(
                    "新浪板块节点清单刷新失败，沿用 %s 的缓存（%d 个名称）",
                    cached[0], len(cached[1]),
                )
                return cached[1]
            raise
        _NODE_CACHE["map"] = (date.today(), mapping)
        _NODE_CACHE.pop("failed_at", None)
        return mapping

    def _fetch_members(self, node: str) -> dict[str, str]:
        """逐页抓取成分；翻页到上限仍未见尾页视为不完整，抛错而不是返回残页。"""
        members: dict[str, str] = {}
        for page in range(1, _CONS_MAX_PAGES + 1):
            rows = self._get_json("Market_Center.getHQNodeData", {
                "page": page, "num": _CONS_PAGE_SIZE, "sort": "symbol", "asc": 1,
                "node": node, "symbol": "", "_s_r_a": "page",
            })
            if not isinstance(rows, list):
                # 200 但返回 null/对象是风控或接口异常，按失败处理（重跑可补）
                raise ProviderError(
                    f"新浪板块 {node} 第 {page} 页返回异常：{type(rows).__name__}"
                )
            if not rows:
                return members
            for row in rows:
                code = str(row.get("code") or "").strip()
                if len(code) == 6 and code.isdigit():
                    members[code] = str(row.get("name") or "").strip()
            if len(rows) < _CONS_PAGE_SIZE:
                return members
            time.sleep(_CONS_PAUSE_SEC)
        raise ProviderError(
            f"新浪板块 {node} 翻页超过 {_CONS_MAX_PAGES} 页仍未见尾页（疑似不完整）"
        )

    # ---------- A4 板块成分 ----------
    def fetch_sector_members(
        self, sector_code: str, sector_name: str, sector_type: str
    ) -> FetchResult:
        started = time.perf_counter()
        detail: dict[str, Any] = {
            "data": pd.DataFrame(columns=list(SECTOR_MEMBER_COLUMNS)),
            "source": self.name,
            "sector_code": sector_code,
            "sector_name": sector_name,
            "sector_type": sector_type,
        }
        if sector_type != "concept":
            detail["skip_reason"] = "sina_unsupported_type"
            return FetchResult(
                rows=0, total=0,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                detail=detail,
            )

        node = self._node_map().get(str(sector_name))
        if not node:
            detail["skip_reason"] = "sina_name_not_found"
            return FetchResult(
                rows=0, total=0,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                detail=detail,
            )

        if time.monotonic() < _CONS_BREAKER["until"]:
            raise ProviderError(
                f"新浪成分连续 {int(_CONS_BREAKER['streak'])} 次失败，冷却中"
            )
        try:
            members = self._fetch_members(node)
        except Exception:
            _CONS_BREAKER["streak"] += 1
            if _CONS_BREAKER["streak"] >= _CONS_FAIL_THRESHOLD:
                _CONS_BREAKER["until"] = time.monotonic() + _FAIL_COOLDOWN_SEC
                logger.error(
                    "新浪成分连续 %d 次抓取失败，冷却 %.0fs（兜底暂停，东财结果不受影响）",
                    _CONS_BREAKER["streak"], _FAIL_COOLDOWN_SEC,
                )
            raise
        _CONS_BREAKER["streak"] = 0
        if not members:
            raise ProviderError(f"新浪板块 {node}（{sector_name}）返回空成分")
        records = [
            {
                "sector_code": sector_code,
                "sector_name": sector_name,
                "sector_type": sector_type,
                "stock_code": code,
                "stock_name": name or None,
            }
            for code, name in sorted(members.items())
        ]
        frame = pd.DataFrame(records, columns=list(SECTOR_MEMBER_COLUMNS))
        detail["data"] = frame
        detail["node"] = node
        return FetchResult(
            rows=len(records),
            total=len(records),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            detail=detail,
        )


__all__ = ["SinaProvider", "build_node_map", "SECTOR_MEMBER_COLUMNS"]
