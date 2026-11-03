"""同花顺板块历史与成分抓取（东财不可用时的板块数据源）。

数据来源：
  - 板块列表 / 日线历史：``akshare.stock_board_{industry,concept}_*_ths``
    （底层 d.10jqka.com.cn，历史可回溯到 2020 年，含 OHLCV + 成交额）
  - 成分股：``q.10jqka.com.cn`` 板块详情页分页 HTML（需 hexin-v Cookie）

口径：
  - 板块编码统一 ``THS_<板块名>``，与 ``ths_sector.collect_sector_fallback`` 一致；
  - 成交量单位「股」，成交额单位「元」，与 ``fact_sector_daily`` 注释一致；
  - 历史接口只返回已收盘交易日，当日盘中不会出现半根 K 线。
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup

from app.core import config
from app.core.logging import get_logger
from app.ingest.base import HISTORY_LIMITER

logger = get_logger(__name__)

THS_SRC = "ths_hist"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
# 板块详情页排序字段：行业/概念各自不同，缺省也不影响第 1 页解析
_DETAIL_FIELD = {"industry": "199112", "concept": "264648"}
_DETAIL_PATH = {"industry": "/thshy/detail", "concept": "/gn/detail"}

_cookie_cache: str | None = None


def sector_code(name: str) -> str:
    """板块代码：与实时兜底保持同一约定。"""
    return f"THS_{str(name).strip()}"[:24]


# =====================================================================
# 板块列表
# =====================================================================

def fetch_board_list() -> list[dict[str, str]]:
    """同花顺行业 + 概念板块清单。失败抛异常由调用方决定是否中止。"""
    import akshare as ak

    frames = (
        ("industry", ak.stock_board_industry_name_ths()),
        ("concept", ak.stock_board_concept_name_ths()),
    )
    boards: list[dict[str, str]] = []
    seen: set[str] = set()
    for board_type, frame in frames:
        if frame is None or frame.empty:
            continue
        for row in frame.to_dict("records"):
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            code = sector_code(name)
            if code in seen:  # 名称撞车时保留先出现的（行业优先）
                logger.warning("板块名称重复，跳过：%s", name)
                continue
            seen.add(code)
            boards.append({
                "sector_code": code,
                "sector_name": name,
                "sector_type": board_type,
                "ths_code": str(row.get("code") or "").strip(),
            })
    return boards


# 板块清单缓存（日更复用，避免每天两次列表请求；清单变动极少）
BOARD_LIST_CACHE = Path(config.BACKEND_DIR) / "var" / "sector_board_list.json"
_BOARD_LIST_TTL_DAYS = 7


def load_board_list(*, refresh: bool = False) -> list[dict[str, str]]:
    """同花顺板块清单：优先读本地缓存（7 天），过期或缺失时联网刷新。

    刷新失败但存在旧缓存时沿用旧清单并记 warning —— 列表接口抖动不应让整条
    板块日更链路失败。没有任何缓存且联网失败才抛出异常。
    """
    cached: list[dict[str, str]] | None = None
    age_days: float | None = None
    if BOARD_LIST_CACHE.exists():
        try:
            data = json.loads(BOARD_LIST_CACHE.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                cached = data
                age_days = (time.time() - BOARD_LIST_CACHE.stat().st_mtime) / 86400
        except (OSError, ValueError):
            cached = None
    if cached is not None and not refresh and age_days is not None \
            and age_days < _BOARD_LIST_TTL_DAYS:
        return cached

    try:
        boards = fetch_board_list()
    except Exception as exc:  # noqa: BLE001
        if cached is not None:
            logger.warning(
                "板块清单刷新失败，沿用 %.1f 天前缓存：%s", age_days or -1, exc
            )
            return cached
        raise

    try:
        BOARD_LIST_CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = BOARD_LIST_CACHE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(boards, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, BOARD_LIST_CACHE)
    except OSError as exc:
        logger.warning("板块清单缓存写入失败（不影响采集）：%s", exc)
    return boards


async def fetch_daily_rows(
    trade_date: date,
    *,
    concurrency: int = 6,
    attempts: int = 2,
    timeout_sec: float = config.SECTOR_BOARD_TIMEOUT_SEC,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """拉取指定交易日全部板块日线（同花顺历史接口，含 OHLCV/成交额）。

    - 窗口从 ``trade_date - 10 天`` 开始：``pct_chg`` 需要前一交易日收盘推导；
    - 每个板块请求前过 ``HISTORY_LIMITER``（≤2 req/s），并发只用于掩盖单次延迟；
    - 单板块失败/无数据不阻塞整体，失败明细进 detail 供质量报告使用；
    - 单板块墙钟上限 ``timeout_sec``：akshare 内部无 timeout，挂死必须被切断，
      否则整条日更链路连同 advisory lock 会一直卡住。

    Returns:
        ``(rows, detail)``；rows 已按 sector_code 去重排序，detail 含
        ``source/boards/failed/empty/errors``。
    """
    boards = await asyncio.to_thread(load_board_list)
    start = trade_date - timedelta(days=10)
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _one(board: dict[str, str]) -> tuple[str, list[dict[str, Any]], str | None]:
        last_exc: Exception | None = None
        async with semaphore:
            for attempt in range(max(1, attempts)):
                await HISTORY_LIMITER.acquire()
                try:
                    frame = await asyncio.wait_for(
                        asyncio.to_thread(
                            fetch_board_history, board, start, trade_date
                        ),
                        timeout=timeout_sec,
                    )
                    rows = [
                        row for row in history_rows(frame, board)
                        if row["trade_date"] == trade_date
                    ]
                    return board["sector_code"], rows, None
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    await asyncio.sleep(0.5 * (attempt + 1))
        assert last_exc is not None
        return board["sector_code"], [], f"{type(last_exc).__name__}: {last_exc}"[:200]

    results = await asyncio.gather(*(_one(board) for board in boards))
    errors: dict[str, str] = {}
    empty = 0
    rows: list[dict[str, Any]] = []
    for code, part, error in results:
        if error is not None:
            errors[code] = error
        elif not part:
            empty += 1
        else:
            rows.extend(part)

    unique = {row["sector_code"]: row for row in rows}
    detail = {
        "source": THS_SRC,
        "boards": len(boards),
        "failed": len(errors),
        "empty": empty,
        "errors": dict(sorted(errors.items())[:10]),
    }
    logger.info(
        "同花顺板块日线 %s：%d/%d 板块有数据（失败 %d，空 %d）",
        trade_date, len(unique), len(boards), len(errors), empty,
    )
    return sorted(unique.values(), key=lambda item: item["sector_code"]), detail


# =====================================================================
# 板块日线历史
# =====================================================================

def fetch_board_history(
    board: dict[str, str], start: date, end: date
) -> pd.DataFrame:
    """单个板块的日线历史（升序，含 OHLCV/成交额）。"""
    import akshare as ak

    func_name = (
        "stock_board_industry_index_ths"
        if board["sector_type"] == "industry"
        else "stock_board_concept_index_ths"
    )
    func = getattr(ak, func_name)
    return func(
        symbol=board["sector_name"],
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
    )


def history_rows(
    frame: pd.DataFrame, board: dict[str, str]
) -> list[dict[str, Any]]:
    """把 akshare 历史表转成 fact_sector_daily 行（pre_close 由前一日收盘推导）。"""
    if frame is None or frame.empty:
        return []

    ordered = frame.copy()
    ordered["日期"] = pd.to_datetime(ordered["日期"], errors="coerce")
    ordered = ordered.dropna(subset=["日期"]).sort_values("日期")

    rows: list[dict[str, Any]] = []
    prev_close: float | None = None
    for record in ordered.to_dict("records"):
        close = _num(record.get("收盘价"))
        if close is None:
            continue
        open_ = _num(record.get("开盘价"))
        high = _num(record.get("最高价"))
        low = _num(record.get("最低价"))
        pct_chg = None
        amplitude = None
        if prev_close not in (None, 0):
            pct_chg = round((close - prev_close) / prev_close * 100, 4)
            if high is not None and low is not None:
                amplitude = round((high - low) / prev_close * 100, 4)
        rows.append({
            "sector_code": board["sector_code"],
            "trade_date": record["日期"].date(),
            "sector_name": board["sector_name"][:64],
            "sector_type": board["sector_type"],
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "pre_close": prev_close,
            "pct_chg": pct_chg,
            "amplitude": amplitude,
            "volume": _int(record.get("成交量")),
            "amount": _num(record.get("成交额")),
            "src": THS_SRC,
        })
        prev_close = close
    return rows


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:  # NaN
        return None
    return out


def _int(value: Any) -> int | None:
    out = _num(value)
    return int(out) if out is not None else None


# =====================================================================
# 板块成分
# =====================================================================

def _ths_cookie() -> str | None:
    """生成 q.10jqka.com.cn 的 hexin-v Cookie（复用 akshare 内置的 ths.js）。"""
    global _cookie_cache
    if _cookie_cache is not None:
        return _cookie_cache
    try:
        from akshare.stock_feature.stock_board_industry_ths import _get_file_content_ths
        from py_mini_racer import MiniRacer

        js = MiniRacer()
        js.eval(_get_file_content_ths("ths.js"))
        _cookie_cache = f"v={js.call('v')}"
    except Exception as exc:  # noqa: BLE001 —— 无 Cookie 时仍可尝试普通请求
        logger.warning("同花顺 Cookie 生成失败（降级为无 Cookie）：%s", exc)
        _cookie_cache = ""
    return _cookie_cache


def _member_page(
    board: dict[str, str], page: int, *, order: str = "desc", retries: int = 3
) -> tuple[str | None, Any]:
    """取一页成分 HTML，返回 (page_info, read_html 结果)。失败重试后抛出。"""
    path = _DETAIL_PATH[board["sector_type"]]
    field = _DETAIL_FIELD[board["sector_type"]]
    url = (
        f"http://q.10jqka.com.cn{path}/field/{field}/order/{order}/page/{page}"
        f"/ajax/1/code/{board['ths_code']}"
    )
    headers = {
        "User-Agent": _UA,
        "Referer": f"http://q.10jqka.com.cn{path}/code/{board['ths_code']}/",
    }
    cookie = _ths_cookie()
    if cookie:
        headers["Cookie"] = cookie

    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(url, headers=headers, timeout=20)
            response.raise_for_status()
            response.encoding = "gbk"
            soup = BeautifulSoup(response.text, features="lxml")
            node = soup.find(name="span", attrs={"class": "page_info"})
            tables = pd.read_html(io.StringIO(response.text))
            if not tables:
                raise ValueError("页面无表格（可能被限流）")
            return node.text.strip() if node else None, tables[0]
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"{board['sector_name']} 成分第 {page} 页失败：{last_exc}")


def _page_total(page_info: str | None) -> int:
    if page_info and "/" in page_info:
        try:
            return max(1, int(page_info.split("/")[1]))
        except ValueError:
            return 1
    return 1


def member_page_count(board: dict[str, str]) -> tuple[int, int]:
    """(总页数, 每页条数)，只需一次请求即可判断能否在 5 页内取全。"""
    page_info, table = _member_page(board, 1)
    return _page_total(page_info), (len(table) if table is not None else 0)


def fetch_board_members(
    board: dict[str, str], *, max_pages: int = 5
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """抓取一个板块的成分股。

    q.10jqka.com.cn 只开放前 5 页（行业 20 条/页、概念 10 条/页），
    这里用「降序前 N 页 + 升序前 N 页」覆盖两端：
      - 总页数 ≤ 5：一方向取全，完整；
      - 5 < 总页数 ≤ 10：双向取全，完整；
      - 总页数 > 10：双向仅覆盖 2×N 页，标记 partial 由调用方决定是否换源。

    Returns:
        (成员列表, {"source","partial","total_pages","page_size"})
    """
    page_info, first_table = _member_page(board, 1)
    total_pages = _page_total(page_info)
    page_size = len(first_table) if first_table is not None else 0

    members = _extract_members(first_table)
    if total_pages <= max_pages:
        for page in range(2, total_pages + 1):
            time.sleep(0.15)
            _, table = _member_page(board, page)
            members.update(_extract_members(table))
        complete = True
    else:
        for page in range(1, max_pages + 1):
            time.sleep(0.15)
            _, table = _member_page(board, page, order="asc")
            members.update(_extract_members(table))
        for page in range(2, max_pages + 1):
            time.sleep(0.15)
            _, table = _member_page(board, page)
            members.update(_extract_members(table))
        complete = total_pages <= max_pages * 2

    return (
        [
            {"stock_code": code, "stock_name": name}
            for code, name in sorted(members.items())
        ],
        {
            "source": "ths",
            "partial": not complete,
            "total_pages": total_pages,
            "page_size": page_size,
        },
    )


def _extract_members(table: pd.DataFrame) -> dict[str, str]:
    if table is None or table.empty:
        return {}
    columns = {str(col): col for col in table.columns}
    code_col = columns.get("代码")
    name_col = columns.get("名称")
    if code_col is None or name_col is None:
        return {}
    out: dict[str, str] = {}
    for row in table[[code_col, name_col]].to_dict("records"):
        code = str(row[code_col]).strip()
        if re.fullmatch(r"\d{1,6}", code):
            out[code.zfill(6)] = str(row[name_col]).strip()
    return out


__all__ = [
    "THS_SRC",
    "sector_code",
    "fetch_board_list",
    "load_board_list",
    "fetch_daily_rows",
    "fetch_board_history",
    "history_rows",
    "fetch_board_members",
]
