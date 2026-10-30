"""新浪个股资金流历史（A2 历史数据的真实来源）。

东财个股资金流排行只有"当前快照"，无法重建历史；同花顺接口实测 401。
新浪 ``MoneyFlow.ssl_qsfx_zjlrqs`` 单次返回最近 300 个交易日，字段：

- ``netamount``  主力净流入（元）；
- ``r0_net``     超大单净流入（元）；
- ``turnover``   换手率 × 100（例如 45.05 表示 0.4505%）；
- ``trade``      收盘价。

口径说明：新浪把 ``netamount`` 标为主力净流入，与东财"超大单+大单"不完全等价，
落库时 ``src`` 记为 ``sina``，A2 结果里可据此区分来源。
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import date
from typing import Any

import requests

from app.core.constants import exchange_of
from app.core.errors import ProviderError
from app.core.logging import get_logger

logger = get_logger(__name__)

SINA_MF_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "MoneyFlow.ssl_qsfx_zjlrqs"
)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://finance.sina.com.cn/",
}


def _num(value: Any) -> float | None:
    try:
        if value in (None, "", "null"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _symbol(code: str) -> str:
    code = str(code).zfill(6)
    return f"{exchange_of(code).lower()}{code}"


def fetch_moneyflow_sync(
    code: str, *, num: int = 300, retries: int = 3
) -> list[dict[str, Any]]:
    """返回按日期升序的资金流行。"""
    symbol = _symbol(code)
    params = {
        "page": "1",
        "num": str(min(max(int(num), 1), 300)),
        "sort": "opendate",
        "asc": "0",
        "daima": symbol,
    }
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(
                SINA_MF_URL, params=params, headers=_HEADERS, timeout=(5, 20)
            )
            response.raise_for_status()
            text = response.text.strip()
            if not text or text in {"null", "[]"}:
                return []
            payload = json.loads(text)
            if not isinstance(payload, list):
                return []
            rows: list[dict[str, Any]] = []
            for item in payload:
                try:
                    trade_date = date.fromisoformat(str(item.get("opendate"))[:10])
                except (TypeError, ValueError):
                    continue
                rows.append(
                    {
                        "trade_date": trade_date,
                        "close": _num(item.get("trade")),
                        "net_amount": _num(item.get("netamount")),
                        "super_net": _num(item.get("r0_net")),
                        "turnover_x100": _num(item.get("turnover")),
                    }
                )
            rows.sort(key=lambda row: row["trade_date"])
            return rows
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(0.8 * (attempt + 1))
    raise ProviderError(
        f"新浪资金流获取失败 {symbol}：{last_error}", detail={"code": code}
    )


async def fetch_moneyflow(
    code: str, *, num: int = 300, retries: int = 3
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(fetch_moneyflow_sync, code, num=num, retries=retries)


__all__ = ["fetch_moneyflow", "fetch_moneyflow_sync"]
