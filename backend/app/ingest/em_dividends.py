"""东财批量分红送配（现金分红事件）。

前复权因子能反映"价格调整了多少"，但无法区分现金分红与送转，用它反推每股分红
会把 10 送 10 当成巨额现金分红，直接污染 ``dv_ttm`` 和股息率策略（A4）。这里用
东财 ``RPT_SHAREBONUS_DET`` 一次拉全市场分红实施记录，按代码聚合现实现金分红。

字段口径：``PRETAX_BONUS_RMB`` 为**每 10 股**税前现金分红（元），与腾讯
``fh_sh`` 一致；调用方按 ``/10`` 换算为每股。
"""
from __future__ import annotations

import asyncio
import time
from datetime import date
from typing import Any

import requests

from app.core.errors import ProviderError
from app.core.logging import get_logger

logger = get_logger(__name__)

API_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT_NAME = "RPT_SHAREBONUS_DET"
PAGE_SIZE = 5000
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://data.eastmoney.com/",
}


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def fetch_dividends_sync(
    start: date,
    end: date | None = None,
    *,
    retries: int = 3,
) -> dict[str, list[tuple[date, float]]]:
    """拉取 ``[start, end]`` 内除权除息的现金分红，返回 ``{code: [(除权日, 每10股)]}``。"""
    filter_expr = f"(EX_DIVIDEND_DATE>='{start.isoformat()}')"
    if end is not None:
        filter_expr += f"(EX_DIVIDEND_DATE<='{end.isoformat()}')"

    events: dict[str, dict[date, float]] = {}
    page = 1
    while True:
        params = {
            "reportName": REPORT_NAME,
            "columns": "ALL",
            "filter": filter_expr,
            "pageNumber": str(page),
            "pageSize": str(PAGE_SIZE),
            "sortColumns": "EX_DIVIDEND_DATE",
            "sortTypes": "1",
        }
        payload: dict[str, Any] | None = None
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                response = requests.get(
                    API_URL, params=params, headers=_HEADERS, timeout=(5, 30)
                )
                response.raise_for_status()
                payload = response.json() or {}
                break
            except Exception as exc:  # noqa: BLE001 —— 网络抖动重试
                last_error = exc
                time.sleep(0.8 * (attempt + 1))
        if payload is None:
            raise ProviderError(
                f"东财分红数据获取失败(第 {page} 页)：{last_error}",
                detail={"start": start.isoformat(), "end": end and end.isoformat()},
            )
        if not payload.get("success", True):
            raise ProviderError(
                f"东财分红数据返回失败：{payload.get('message')}",
                detail={"page": page},
            )

        result = payload.get("result") or {}
        data = result.get("data") or []
        for record in data:
            raw_code = str(record.get("SECURITY_CODE") or "").strip()
            ex_date = _parse_date(record.get("EX_DIVIDEND_DATE"))
            amount = record.get("PRETAX_BONUS_RMB")
            if not raw_code or ex_date is None or amount is None:
                continue
            try:
                per_10 = float(amount)
            except (TypeError, ValueError):
                continue
            if per_10 <= 0:
                continue
            if ex_date < start or (end is not None and ex_date > end):
                continue
            code = raw_code.zfill(6)
            bucket = events.setdefault(code, {})
            bucket[ex_date] = bucket.get(ex_date, 0.0) + per_10

        pages = int(result.get("pages") or 1)
        if page >= pages or not data:
            break
        page += 1

    logger.info(
        "分红事件：%s~%s，%d 只股票，%d 条",
        start, end or "今", len(events), sum(len(v) for v in events.values()),
    )
    return {code: sorted(bucket.items()) for code, bucket in events.items()}


async def fetch_dividends(
    start: date, end: date | None = None, *, retries: int = 3
) -> dict[str, list[tuple[date, float]]]:
    return await asyncio.to_thread(fetch_dividends_sync, start, end, retries=retries)


__all__ = ["fetch_dividends", "fetch_dividends_sync"]
