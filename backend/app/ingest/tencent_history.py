"""日线历史源（全市场并发回补用）。

主源腾讯 ``fqkline`` 单次可返回约 800 个交易日，原始价与前复权价各一次请求即可
算出 ``adj_factor``，并顺带带回分红除权事件。但腾讯对高频请求有 IP 级 WAF：
连续抓到几百只后会整段返回 501 拦截页。因此这里内置新浪兜底：

- 新浪日线 ``CN_MarketDataService.getKLineData``：单次最多 1023 根，成交量单位为股；
- 新浪前复权因子 ``qfq.js``：``qfq_close = raw_close / factor``，因子从除权日起生效；
- 腾讯连续失败达到阈值后进入冷却期，期间直接走新浪，避免每只股票都先撞一次墙。

分红事件不再从腾讯 qfq 行里提取（新浪因子无法区分现金分红与送转），由
``em_dividends`` 批量提供。本模块只负责抓取与解析，不做限频/写库。
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from bisect import bisect_right
from datetime import date
from typing import Any

import requests

from app.core.constants import exchange_of
from app.core.errors import ProviderError
from app.core.logging import get_logger

logger = get_logger(__name__)

TENCENT_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
SINA_KLINE_URL = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
SINA_QFQ_URL = "https://finance.sina.com.cn/realstock/company/{symbol}/qfq.js"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://gu.qq.com/",
}
_MAX_ROWS = 800
_SINA_MAX_ROWS = 1023

# 腾讯 WAF 熔断：连续失败 3 只即判定被限流，冷却 15 分钟内不再尝试
_TENCENT_FAIL_LIMIT = 3
_TENCENT_COOLDOWN_SEC = 900.0
_tencent_state = {"failures": 0, "blocked_until": 0.0}
_tencent_lock = threading.Lock()


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


def _tencent_available() -> bool:
    with _tencent_lock:
        return time.monotonic() >= _tencent_state["blocked_until"]


def _mark_tencent_ok() -> None:
    with _tencent_lock:
        _tencent_state["failures"] = 0


def _mark_tencent_failure(reason: str) -> None:
    with _tencent_lock:
        _tencent_state["failures"] += 1
        if _tencent_state["failures"] >= _TENCENT_FAIL_LIMIT:
            _tencent_state["blocked_until"] = time.monotonic() + _TENCENT_COOLDOWN_SEC
            logger.warning("腾讯日线连续失败，熔断 %d 秒：%s", int(_TENCENT_COOLDOWN_SEC), reason)


def _parse_kline(rows: list[Any]) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        try:
            trade_date = date.fromisoformat(str(row[0])[:10])
        except ValueError:
            continue
        close = _num(row[2])
        if close is None or close <= 0:
            continue
        parsed.append(
            {
                "trade_date": trade_date,
                "open": _num(row[1]),
                "close": close,
                "high": _num(row[3]),
                "low": _num(row[4]),
                "volume": _num(row[5]),
                "dividend": row[6] if len(row) > 6 and isinstance(row[6], dict) else None,
            }
        )
    parsed.sort(key=lambda item: item["trade_date"])
    return parsed


def fetch_kline_sync(
    code: str, *, adjust: str = "raw", count: int = _MAX_ROWS, retries: int = 3
) -> list[dict[str, Any]]:
    """腾讯同步抓取单只股票日线。``adjust='qfq'`` 时返回前复权价（含分红事件）。"""
    symbol = _symbol(code)
    fq = "qfq" if adjust == "qfq" else ""
    params = {"param": f"{symbol},day,,,{min(int(count), _MAX_ROWS)},{fq}"}
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(TENCENT_URL, params=params, headers=_HEADERS, timeout=(5, 20))
            if response.status_code in (403, 501):
                # WAF 拦截页：重试只会延长封禁，立即上抛交给熔断/兜底
                raise ProviderError(f"腾讯接口被拦截 HTTP {response.status_code}")
            response.raise_for_status()
            payload = response.json() or {}
            data = (payload.get("data") or {}).get(symbol) or {}
            rows = data.get("qfqday") or data.get("day") or []
            return _parse_kline(rows)
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 网络抖动重试
            last_error = exc
            time.sleep(0.6 * (attempt + 1))
    raise ProviderError(
        f"腾讯日线获取失败 {symbol}({adjust})：{last_error}",
        detail={"code": code, "adjust": adjust},
    )


async def fetch_kline(
    code: str, *, adjust: str = "raw", count: int = _MAX_ROWS, retries: int = 3
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(
        fetch_kline_sync, code, adjust=adjust, count=count, retries=retries
    )


def fetch_sina_kline_sync(
    code: str, *, count: int = _SINA_MAX_ROWS, retries: int = 3
) -> list[dict[str, Any]]:
    """新浪原始日线；``volume`` 已换算成"手"，与腾讯解析结果保持同一形状。"""
    symbol = _symbol(code)
    params = {
        "symbol": symbol,
        "scale": "240",
        "ma": "no",
        "datalen": str(min(int(count), _SINA_MAX_ROWS)),
    }
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(
                SINA_KLINE_URL, params=params,
                headers={**_HEADERS, "Referer": "https://finance.sina.com.cn/"},
                timeout=(5, 20),
            )
            response.raise_for_status()
            text = response.text.strip()
            if not text.startswith("["):
                raise ValueError(f"非预期响应：{text[:80]}")
            payload = json.loads(text)
            parsed: list[dict[str, Any]] = []
            for row in payload or []:
                if not isinstance(row, dict):
                    continue
                try:
                    trade_date = date.fromisoformat(str(row.get("day"))[:10])
                except ValueError:
                    continue
                close = _num(row.get("close"))
                if close is None or close <= 0:
                    continue
                volume_shares = _num(row.get("volume"))
                parsed.append(
                    {
                        "trade_date": trade_date,
                        "open": _num(row.get("open")),
                        "close": close,
                        "high": _num(row.get("high")),
                        "low": _num(row.get("low")),
                        "volume": volume_shares / 100.0 if volume_shares is not None else None,
                        "dividend": None,
                    }
                )
            parsed.sort(key=lambda item: item["trade_date"])
            return parsed
        except Exception as exc:  # noqa: BLE001 —— DNS 抖动很常见，必须重试
            last_error = exc
            time.sleep(0.8 * (attempt + 1))
    raise ProviderError(
        f"新浪日线获取失败 {symbol}：{last_error}",
        detail={"code": code},
    )


def fetch_sina_qfq_factors_sync(
    code: str, *, retries: int = 3
) -> list[tuple[date, float]]:
    """新浪前复权因子（按日期升序）；返回空列表表示无复权事件（因子恒为 1）。"""
    symbol = _symbol(code)
    url = SINA_QFQ_URL.format(symbol=symbol)
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(
                url, headers={**_HEADERS, "Referer": "https://finance.sina.com.cn/"},
                timeout=(5, 20),
            )
            response.raise_for_status()
            text = response.text
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                raise ValueError(f"非预期响应：{text[:80]}")
            payload = json.loads(text[start:end + 1]) or {}
            parsed: list[tuple[date, float]] = []
            for item in payload.get("data") or []:
                if not isinstance(item, dict):
                    continue
                try:
                    factor_date = date.fromisoformat(str(item.get("d"))[:10])
                except ValueError:
                    continue
                factor = _num(item.get("f"))
                if factor is not None and factor > 0:
                    parsed.append((factor_date, factor))
            parsed.sort(key=lambda item: item[0])
            return parsed
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(0.8 * (attempt + 1))
    raise ProviderError(
        f"新浪复权因子获取失败 {symbol}：{last_error}",
        detail={"code": code},
    )


def apply_sina_qfq(
    raw_rows: list[dict[str, Any]], factors: list[tuple[date, float]]
) -> list[dict[str, Any]]:
    """按新浪因子生成前复权行。

    因子条目从其除权日起生效（``qfq_close = raw_close / factor``）；早于最早因子
    的日期沿用最早因子（该因子已包含之后全部事件的累计调整）。
    """
    if not factors:
        return [{**row, "dividend": None} for row in raw_rows]
    factor_dates = [item[0] for item in factors]
    factor_values = [item[1] for item in factors]
    adjusted: list[dict[str, Any]] = []
    for row in raw_rows:
        index = bisect_right(factor_dates, row["trade_date"]) - 1
        factor = factor_values[index] if index >= 0 else factor_values[0]
        adjusted.append({**row, "close": row["close"] / factor, "dividend": None})
    return adjusted


def fetch_price_pair_sync(
    code: str, *, count: int = _SINA_MAX_ROWS, retries: int = 3
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """抓取 ``(原始日线, 前复权日线, 数据源)``；腾讯熔断时自动改走新浪。"""
    if _tencent_available():
        try:
            raw = fetch_kline_sync(code, adjust="raw", count=count, retries=retries)
            qfq = fetch_kline_sync(code, adjust="qfq", count=count, retries=retries)
            _mark_tencent_ok()
            return raw, qfq, "tencent"
        except Exception as exc:  # noqa: BLE001 —— 单只失败/熔断都转新浪
            _mark_tencent_failure(f"{code}: {exc}")
            logger.info("腾讯日线不可用，改走新浪 code=%s：%s", code, exc)

    raw = fetch_sina_kline_sync(code, count=count, retries=retries)
    factors = fetch_sina_qfq_factors_sync(code, retries=retries)
    return raw, apply_sina_qfq(raw, factors), "sina"


async def fetch_price_pair(
    code: str, *, count: int = _SINA_MAX_ROWS, retries: int = 3
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    return await asyncio.to_thread(
        fetch_price_pair_sync, code, count=count, retries=retries
    )


def dividends_from_qfq(rows: list[dict[str, Any]]) -> list[tuple[date, float]]:
    """从腾讯 qfq 行内的除权事件提取 ``(除权日, 每10股现金分红)``。"""
    events: dict[date, float] = {}
    for item in rows:
        info = item.get("dividend") or {}
        raw_date = info.get("cqr")
        amount = _num(info.get("fh_sh"))
        if not raw_date or amount is None:
            continue
        try:
            ex_date = date.fromisoformat(str(raw_date)[:10])
        except ValueError:
            continue
        events[ex_date] = events.get(ex_date, 0.0) + amount
    return sorted(events.items())


__all__ = [
    "fetch_kline",
    "fetch_kline_sync",
    "fetch_price_pair",
    "fetch_price_pair_sync",
    "fetch_sina_kline_sync",
    "fetch_sina_qfq_factors_sync",
    "apply_sina_qfq",
    "dividends_from_qfq",
]
