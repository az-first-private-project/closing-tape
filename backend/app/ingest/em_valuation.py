"""东财估值分析历史（PE_TTM / PB / PS_TTM / 市值）。

``push2`` 行情接口在本机网络下不可用，但 ``datacenter-web`` 的估值分析报表稳定，
单只股票一次请求即可拿到 2018 年以来的逐日估值，正好补齐 ``fact_stock_daily``
里 AKShare/Baostock 日线没有的估值列。
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

EM_VALUATION_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://data.eastmoney.com/",
}
_COLUMNS = (
    "SECURITY_CODE,TRADE_DATE,PE_TTM,PB_MRQ,PS_TTM,"
    "TOTAL_MARKET_CAP,NOTLIMITED_MARKETCAP_A,CLOSE_PRICE"
)


def _num(value: Any) -> float | None:
    try:
        if value in (None, "", "null"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def fetch_valuation_sync(
    code: str, *, retries: int = 3
) -> list[dict[str, Any]]:
    """返回按日期升序的估值行。"""
    code = str(code).zfill(6)
    params = {
        "reportName": "RPT_VALUEANALYSIS_DET",
        "columns": _COLUMNS,
        "sortColumns": "TRADE_DATE",
        "sortTypes": "1",
        "pageSize": "5000",
        "pageNumber": "1",
        "filter": f'(SECURITY_CODE="{code}")',
        "source": "WEB",
        "client": "WEB",
    }
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(
                EM_VALUATION_URL, params=params, headers=_HEADERS, timeout=(5, 20)
            )
            response.raise_for_status()
            payload = response.json() or {}
            if not payload.get("success"):
                message = payload.get("message") or "unknown"
                if "无数据" in str(message) or "报表配置" in str(message):
                    return []
                raise ProviderError(f"东财估值返回失败：{message}")
            rows = (payload.get("result") or {}).get("data") or []
            parsed: list[dict[str, Any]] = []
            for item in rows:
                trade_date = _parse_date(item.get("TRADE_DATE"))
                if trade_date is None:
                    continue
                parsed.append(
                    {
                        "trade_date": trade_date,
                        "pe_ttm": _num(item.get("PE_TTM")),
                        "pb": _num(item.get("PB_MRQ")),
                        "ps_ttm": _num(item.get("PS_TTM")),
                        "market_cap": _num(item.get("TOTAL_MARKET_CAP")),
                        "float_cap": _num(item.get("NOTLIMITED_MARKETCAP_A")),
                        "close_price": _num(item.get("CLOSE_PRICE")),
                    }
                )
            parsed.sort(key=lambda row: row["trade_date"])
            return parsed
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(0.8 * (attempt + 1))
    raise ProviderError(
        f"东财估值获取失败 {code}：{last_error}", detail={"code": code}
    )


async def fetch_valuation(
    code: str, *, retries: int = 3
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(fetch_valuation_sync, code, retries=retries)


__all__ = ["fetch_valuation", "fetch_valuation_sync"]
