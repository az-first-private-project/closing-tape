"""全市场历史数据并发回补。

为什么单独一套通道：现有 ``backfill_stock_history`` 走 provider 链（akshare 东财
+ Baostock），在本机网络下东财断连、Baostock 偶发挂起，全市场回补几乎跑不完。
这里直接用可用的 HTTP 源：

- 日线（腾讯）：原始价 + 前复权价各一次请求，单只约 800 个交易日；
- 资金流（新浪）：单只最近 300 个交易日；
- 估值（东财 datacenter）：单只完整历史 PE/PB/PS/市值。

所有写入都是幂等 upsert，按 ``code`` 批量并发抓取、分批落库；中断后重跑会跳过
已完成代码（``var/backfill_*.json`` 断点）。
"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import (
    Date, Numeric, String, column, func, or_, select, update, values,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.core.constants import is_st_name, limit_prices
from app.core.logging import get_logger
from app.db.models import DimStock, FactMoneyflow, FactStockDaily, TradeCalendar
from app.db.session import session_scope
from app.ingest.base import FetchResult, upsert_rows
from app.ingest.em_dividends import fetch_dividends
from app.ingest.em_valuation import fetch_valuation
from app.ingest.sina_moneyflow import fetch_moneyflow
from app.ingest.tencent_history import dividends_from_qfq, fetch_price_pair

logger = get_logger(__name__)

CN_TZ = ZoneInfo("Asia/Shanghai")
PRICE_COUNT = 1023
PRICE_LOOKBACK_DAYS = 1100
MONEYFLOW_COUNT = 300
DEFAULT_CONCURRENCY = 6
PRICE_BATCH = 40
MONEYFLOW_BATCH = 60
VALUATION_BATCH = 40
_UPDATE_CHUNK_ROWS = 500
_STATE_DIR = Path(config.BACKEND_DIR) / "var"


# ============================================================
# 通用
# ============================================================
@asynccontextmanager
async def _db_scope(db: AsyncSession | None) -> AsyncIterator[AsyncSession]:
    if db is not None:
        yield db
        return
    async with session_scope() as session:
        yield session


async def _write(db: AsyncSession | None, fn) -> None:
    """执行一批写入。

    外部 session 下必须逐批 commit：断点文件是按批保存的，若只在任务结束时提交，
    中途被停止就会留下"断点说已完成、库里其实回滚了"的假进度。
    """
    async with _db_scope(db) as session:
        await fn(session)
        if db is not None:
            await session.commit()


def _chunks(items: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for offset in range(0, len(items), size):
        yield items[offset:offset + size]


def _state_path(kind: str) -> Path:
    return _STATE_DIR / f"backfill_{kind}.json"


def _load_state(kind: str, end: date) -> set[str]:
    path = _state_path(kind)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("end") == end.isoformat():
            return {str(code) for code in payload.get("completed", [])}
    except (OSError, ValueError):
        pass
    return set()


def _save_state(kind: str, end: date, completed: set[str]) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = _state_path(kind)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            {"end": end.isoformat(), "completed": sorted(completed)},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    tmp.replace(path)


async def last_completed_trade_date(db: AsyncSession | None = None) -> date:
    """最近一个已经收盘的交易日（盘中运行时不把当天半截数据当完整日）。"""
    now = datetime.now(CN_TZ)
    closed = (now.hour, now.minute) >= (15, 30)
    cutoff = now.date() if closed else now.date() - timedelta(days=1)
    async with _db_scope(db) as session:
        value = (
            await session.execute(
                select(func.max(TradeCalendar.cal_date)).where(
                    TradeCalendar.is_open.is_(True),
                    TradeCalendar.cal_date <= cutoff,
                )
            )
        ).scalar_one_or_none()
    return value or cutoff


async def _resolve_codes(
    codes: Sequence[str] | None, db: AsyncSession | None
) -> list[str]:
    if codes:
        cleaned = []
        for code in codes:
            digits = "".join(ch for ch in str(code) if ch.isdigit())
            if digits:
                cleaned.append(digits.zfill(6)[-6:])
        return sorted(set(cleaned))
    async with _db_scope(db) as session:
        rows = (
            await session.execute(
                select(DimStock.code)
                .where(DimStock.delist_date.is_(None), DimStock.status != 9)
                .order_by(DimStock.code)
            )
        ).scalars().all()
    return [str(code) for code in rows]


async def _load_meta(
    codes: Sequence[str], db: AsyncSession | None
) -> dict[str, dict[str, Any]]:
    async with _db_scope(db) as session:
        rows = (
            await session.execute(
                select(
                    DimStock.code, DimStock.name, DimStock.total_share,
                    DimStock.float_share,
                ).where(DimStock.code.in_(list(codes)))
            )
        ).all()
    return {
        str(row[0]): {
            "name": row[1],
            "total_share": float(row[2]) if row[2] is not None else None,
            "float_share": float(row[3]) if row[3] is not None else None,
        }
        for row in rows
    }


# ============================================================
# 日线（腾讯）
# ============================================================
def _dv_ttm(
    trade_date: date, close: float, events: Sequence[tuple[date, float]]
) -> float:
    total = 0.0
    lower = trade_date - timedelta(days=365)
    for ex_date, amount_per_10 in events:
        if lower < ex_date <= trade_date:
            total += amount_per_10 / 10.0
    return round(total / close * 100.0, 4) if close > 0 else 0.0


def _volume_multiplier(code: str, src: str) -> int:
    """返回原始成交量换算成「股」的倍数。

    腾讯主板/创业板返回「手」，但科创板（68 开头）返回的已经是「股」；
    新浪统一先除以 100 归一成「手」，因此只有腾讯科创板需要跳过 ×100。
    """
    if src == "tencent" and code.startswith("68"):
        return 1
    return 100


def _price_rows(
    code: str,
    meta: dict[str, Any],
    raw: Sequence[dict[str, Any]],
    qfq: Sequence[dict[str, Any]],
    start: date,
    end: date,
    events: Sequence[tuple[date, float]] | None = None,
    src: str = "tencent",
) -> list[dict[str, Any]]:
    qfq_map = {item["trade_date"]: item["close"] for item in qfq}
    # 新浪因子区不了现金分红与送转，优先用东财分红事件；拿不到时退回腾讯行内事件
    events = dividends_from_qfq(qfq) if events is None else events
    name = meta.get("name")
    is_st = is_st_name(name)
    total_share = meta.get("total_share")
    float_share = meta.get("float_share")

    rows: list[dict[str, Any]] = []
    prev_raw: dict[str, Any] | None = None
    prev_qfq_close: float | None = None
    for item in raw:
        trade_date = item["trade_date"]
        qfq_close = qfq_map.get(trade_date)
        if trade_date < start:
            prev_raw, prev_qfq_close = item, qfq_close
            continue
        if trade_date > end:
            break

        close = float(item["close"])
        high = item.get("high")
        low = item.get("low")
        open_ = item.get("open")
        raw_volume = item.get("volume")
        volume = (
            int(raw_volume * _volume_multiplier(code, src))
            if raw_volume is not None
            else None
        )

        # 用前复权价算真实涨跌幅，并反推交易所口径的调整后前收盘
        pct_chg = None
        pre_close = None
        if prev_qfq_close and qfq_close and prev_qfq_close > 0:
            pct_chg = round((qfq_close / prev_qfq_close - 1) * 100.0, 3)
            if qfq_close > 0:
                pre_close = round(close * prev_qfq_close / qfq_close, 3)
        elif prev_raw is not None:
            pre_close = float(prev_raw["close"])
            if pre_close > 0:
                pct_chg = round((close / pre_close - 1) * 100.0, 3)

        amplitude = None
        if pre_close and pre_close > 0 and high is not None and low is not None:
            amplitude = round((float(high) - float(low)) / pre_close * 100.0, 3)

        if volume is not None and volume > 0 and all(
            value is not None for value in (open_, high, low)
        ):
            avg_price = (float(open_) + float(high) + float(low) + close) / 4.0
            amount = round(avg_price * volume, 2)
        else:
            amount = None

        adj_factor = (
            round(float(qfq_close) / close, 6)
            if qfq_close and close > 0
            else 1.0
        )
        turnover_rate = (
            round(volume / float_share * 100.0, 4)
            if volume is not None and float_share and float_share > 0
            else None
        )
        market_cap = (
            round(close * total_share, 2)
            if total_share and total_share > 0
            else None
        )
        float_cap = (
            round(close * float_share, 2)
            if float_share and float_share > 0
            else None
        )
        is_limit_up = is_limit_down = False
        if pre_close and pre_close > 0:
            up, down = limit_prices(pre_close, code, is_st)
            if up is not None and down is not None:
                is_limit_up = close >= up - 0.005
                is_limit_down = close <= down + 0.005

        rows.append(
            {
                "code": code,
                "trade_date": trade_date,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "pre_close": pre_close,
                "pct_chg": pct_chg,
                "amplitude": amplitude,
                "volume": volume,
                "amount": amount,
                "turnover_rate": turnover_rate,
                "adj_factor": adj_factor,
                "market_cap": market_cap,
                "float_cap": float_cap,
                "dv_ttm": _dv_ttm(trade_date, close, events),
                "is_limit_up": is_limit_up,
                "is_limit_down": is_limit_down,
                "is_suspended": bool(volume is None or volume <= 0),
                "src": src,
            }
        )
        prev_raw, prev_qfq_close = item, qfq_close
    return rows


async def backfill_prices(
    start: date | None = None,
    end: date | None = None,
    codes: Sequence[str] | None = None,
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    db: AsyncSession | None = None,
    force: bool = False,
) -> FetchResult:
    started = time.perf_counter()
    end_date = end or await last_completed_trade_date(db)
    start_date = start or (end_date - timedelta(days=PRICE_LOOKBACK_DAYS))
    code_list = await _resolve_codes(codes, db)
    meta = await _load_meta(code_list, db)
    completed = set() if force else _load_state("prices", end_date)
    pending = [code for code in code_list if code not in completed]

    logger.info(
        "日线回补：区间 %s~%s，标的 %d 只（断点跳过 %d）",
        start_date, end_date, len(code_list), len(code_list) - len(pending),
    )
    semaphore = asyncio.Semaphore(max(int(concurrency), 1))
    written = 0
    failed: dict[str, str] = {}
    empty: list[str] = []
    update_columns = [
        "open", "high", "low", "close", "pre_close", "pct_chg", "amplitude",
        "volume", "amount", "turnover_rate", "adj_factor", "market_cap",
        "float_cap", "dv_ttm", "is_limit_up", "is_limit_down", "is_suspended", "src",
    ]

    async def _one(code: str) -> tuple[str, Any, Any, str, str | None]:
        try:
            async with semaphore:
                raw, qfq, source = await fetch_price_pair(code, count=PRICE_COUNT)
            return code, raw, qfq, source, None
        except Exception as exc:  # noqa: BLE001 —— 单只失败不阻断整体
            return code, None, None, "none", f"{type(exc).__name__}: {exc}"

    # 分红事件优先用东财批量报告（腾讯/新浪的复权数据都不能可靠区分现金分红与送转）
    try:
        dividend_events = await fetch_dividends(start_date - timedelta(days=365), end_date)
    except Exception as exc:  # noqa: BLE001 —— 分红利空只影响 dv_ttm，不阻断行情回补
        dividend_events = None
        logger.warning("分红事件获取失败，dv_ttm 回退为腾讯口径：%s", exc)

    sources: dict[str, int] = {}
    for batch in _chunks(pending, PRICE_BATCH):
        results = await asyncio.gather(*(_one(code) for code in batch))
        batch_rows: list[dict[str, Any]] = []
        for code, raw, qfq, source, error in results:
            if error is not None:
                failed[code] = error
                logger.warning("日线回补失败 code=%s：%s", code, error)
                continue
            sources[source] = sources.get(source, 0) + 1
            events = (
                dividend_events.get(code, ())
                if dividend_events is not None
                else None
            )
            rows = _price_rows(
                code, meta.get(code, {}), raw, qfq, start_date, end_date,
                events, src=source,
            )
            if not rows:
                empty.append(code)
            else:
                batch_rows.extend(rows)
            completed.add(code)
        if batch_rows:
            async def _write_rows(session: AsyncSession) -> None:
                nonlocal written
                written += await upsert_rows(
                    session, FactStockDaily, batch_rows, ["code", "trade_date"],
                    update_columns=update_columns, chunk_size=1000,
                )
            await _write(db, _write_rows)
        _save_state("prices", end_date, completed)
        logger.info(
            "日线进度 %d/%d（写入 %d 行，失败 %d，空 %d）",
            min(len(completed), len(code_list)), len(code_list),
            written, len(failed), len(empty),
        )

    elapsed = int((time.perf_counter() - started) * 1000)
    return FetchResult(
        rows=written,
        total=len(code_list),
        failed=len(failed),
        elapsed_ms=elapsed,
        detail={
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            "completed": len(completed),
            "empty": len(empty),
            "failed_codes": sorted(failed)[:100],
            "sources": sources,
        },
    )


# ============================================================
# 资金流（新浪）
# ============================================================
def _moneyflow_rows(
    code: str,
    items: Sequence[dict[str, Any]],
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        trade_date = item["trade_date"]
        if trade_date < start or trade_date > end:
            continue
        history = [
            items[index - offset]["net_amount"]
            for offset in range(0, 5)
            if index - offset >= 0
        ]
        available = [value for value in history if value is not None]
        values_ = [
            history[offset] if offset < len(history) else None for offset in range(5)
        ]
        rows.append(
            {
                "code": code,
                "trade_date": trade_date,
                "main_net_t0": values_[0],
                "main_net_t1": values_[1],
                "main_net_t2": values_[2],
                "main_net_t3": values_[3],
                "main_net_t4": values_[4],
                "main_net_5d": round(sum(available), 2) if available else None,
                "north_hold_ratio": None,
                "src": "sina",
            }
        )
    return rows


async def backfill_moneyflow(
    start: date | None = None,
    end: date | None = None,
    codes: Sequence[str] | None = None,
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    db: AsyncSession | None = None,
    force: bool = False,
) -> FetchResult:
    started = time.perf_counter()
    end_date = end or await last_completed_trade_date(db)
    start_date = start or (end_date - timedelta(days=420))
    code_list = await _resolve_codes(codes, db)
    completed = set() if force else _load_state("moneyflow", end_date)
    pending = [code for code in code_list if code not in completed]

    logger.info(
        "资金流回补：区间 %s~%s，标的 %d 只（断点跳过 %d）",
        start_date, end_date, len(code_list), len(code_list) - len(pending),
    )
    semaphore = asyncio.Semaphore(max(int(concurrency), 1))
    written = 0
    failed: dict[str, str] = {}
    empty: list[str] = []

    async def _one(code: str) -> tuple[str, Any, str | None]:
        try:
            async with semaphore:
                items = await fetch_moneyflow(code, num=MONEYFLOW_COUNT)
            return code, items, None
        except Exception as exc:  # noqa: BLE001
            return code, None, f"{type(exc).__name__}: {exc}"

    for batch in _chunks(pending, MONEYFLOW_BATCH):
        results = await asyncio.gather(*(_one(code) for code in batch))
        batch_rows: list[dict[str, Any]] = []
        for code, items, error in results:
            if error is not None:
                failed[code] = error
                logger.warning("资金流回补失败 code=%s：%s", code, error)
                continue
            rows = _moneyflow_rows(code, items or [], start_date, end_date)
            if not rows:
                empty.append(code)
            else:
                batch_rows.extend(rows)
            completed.add(code)
        if batch_rows:
            async def _write_rows(session: AsyncSession) -> None:
                nonlocal written
                written += await upsert_rows(
                    session, FactMoneyflow, batch_rows, ["code", "trade_date"],
                    chunk_size=1000,
                )
            await _write(db, _write_rows)
        _save_state("moneyflow", end_date, completed)
        logger.info(
            "资金流进度 %d/%d（写入 %d 行，失败 %d，空 %d）",
            min(len(completed), len(code_list)), len(code_list),
            written, len(failed), len(empty),
        )

    elapsed = int((time.perf_counter() - started) * 1000)
    return FetchResult(
        rows=written,
        total=len(code_list),
        failed=len(failed),
        elapsed_ms=elapsed,
        detail={
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            "completed": len(completed),
            "empty": len(empty),
            "failed_codes": sorted(failed)[:100],
            "source": "sina",
        },
    )


# ============================================================
# 估值（东财 datacenter）
# ============================================================
def _valuation_update_statement(rows: Sequence[dict[str, Any]]) -> Any:
    table = FactStockDaily.__table__
    payload = values(
        column("code", String(6)),
        column("trade_date", Date()),
        column("pe_ttm", Numeric(16, 4)),
        column("pb", Numeric(16, 4)),
        column("ps_ttm", Numeric(16, 4)),
        column("market_cap", Numeric(20, 2)),
        column("float_cap", Numeric(20, 2)),
        name="v",
    ).data(
        [
            (
                row["code"], row["trade_date"], row.get("pe_ttm"),
                row.get("pb"), row.get("ps_ttm"), row.get("market_cap"),
                row.get("float_cap"),
            )
            for row in rows
        ]
    )
    return (
        update(table)
        .where(
            table.c.code == payload.c.code,
            table.c.trade_date == payload.c.trade_date,
        )
        .values(
            pe_ttm=payload.c.pe_ttm,
            pb=payload.c.pb,
            ps_ttm=payload.c.ps_ttm,
            market_cap=payload.c.market_cap,
            float_cap=payload.c.float_cap,
            updated_at=table.c.updated_at,
        )
    )


async def _fill_share_capital(
    session: AsyncSession, code: str, latest: dict[str, Any]
) -> None:
    """用估值市值÷收盘价补 ``dim_stock`` 缺失的股本（只补空值，不覆盖已有精确值）。"""
    close = latest.get("close_price")
    if not close or close <= 0:
        return
    total = (
        round(float(latest["market_cap"]) / close, 4)
        if latest.get("market_cap") else None
    )
    float_ = (
        round(float(latest["float_cap"]) / close, 4)
        if latest.get("float_cap") else None
    )
    if total is None and float_ is None:
        return
    await session.execute(
        update(DimStock)
        .where(
            DimStock.code == code,
            or_(DimStock.total_share.is_(None), DimStock.float_share.is_(None)),
        )
        .values(
            total_share=func.coalesce(DimStock.total_share, total),
            float_share=func.coalesce(DimStock.float_share, float_),
        )
    )


async def backfill_valuation(
    start: date | None = None,
    end: date | None = None,
    codes: Sequence[str] | None = None,
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    db: AsyncSession | None = None,
    force: bool = False,
) -> FetchResult:
    started = time.perf_counter()
    end_date = end or await last_completed_trade_date(db)
    start_date = start or (end_date - timedelta(days=PRICE_LOOKBACK_DAYS))
    code_list = await _resolve_codes(codes, db)
    completed = set() if force else _load_state("valuation", end_date)
    pending = [code for code in code_list if code not in completed]

    logger.info(
        "估值回补：区间 %s~%s，标的 %d 只（断点跳过 %d）",
        start_date, end_date, len(code_list), len(code_list) - len(pending),
    )
    semaphore = asyncio.Semaphore(max(int(concurrency), 1))
    updated = 0
    failed: dict[str, str] = {}
    empty: list[str] = []

    async def _one(code: str) -> tuple[str, Any, str | None]:
        try:
            async with semaphore:
                items = await fetch_valuation(code)
            return code, items, None
        except Exception as exc:  # noqa: BLE001
            return code, None, f"{type(exc).__name__}: {exc}"

    for batch in _chunks(pending, VALUATION_BATCH):
        results = await asyncio.gather(*(_one(code) for code in batch))
        batch_rows: list[dict[str, Any]] = []
        share_fills: list[tuple[str, dict[str, Any]]] = []
        for code, items, error in results:
            if error is not None:
                failed[code] = error
                logger.warning("估值回补失败 code=%s：%s", code, error)
                continue
            if items:
                share_fills.append((code, items[-1]))
            rows = [
                {
                    "code": code,
                    "trade_date": item["trade_date"],
                    "pe_ttm": item.get("pe_ttm"),
                    "pb": item.get("pb"),
                    "ps_ttm": item.get("ps_ttm"),
                    "market_cap": item.get("market_cap"),
                    "float_cap": item.get("float_cap"),
                }
                for item in (items or [])
                if start_date <= item["trade_date"] <= end_date
                and any(
                    item.get(key) is not None
                    for key in ("pe_ttm", "pb", "ps_ttm", "market_cap", "float_cap")
                )
            ]
            if not rows:
                empty.append(code)
            else:
                batch_rows.extend(rows)
            completed.add(code)
        if batch_rows or share_fills:
            async def _write_rows(session: AsyncSession) -> None:
                nonlocal updated
                for offset in range(0, len(batch_rows), _UPDATE_CHUNK_ROWS):
                    chunk = batch_rows[offset:offset + _UPDATE_CHUNK_ROWS]
                    result = await session.execute(_valuation_update_statement(chunk))
                    affected = getattr(result, "rowcount", None)
                    updated += int(affected) if affected and affected > 0 else len(chunk)
                for code, latest in share_fills:
                    await _fill_share_capital(session, code, latest)
            await _write(db, _write_rows)
        _save_state("valuation", end_date, completed)
        logger.info(
            "估值进度 %d/%d（更新 %d 行，失败 %d，空 %d）",
            min(len(completed), len(code_list)), len(code_list),
            updated, len(failed), len(empty),
        )

    elapsed = int((time.perf_counter() - started) * 1000)
    return FetchResult(
        rows=updated,
        total=len(code_list),
        failed=len(failed),
        elapsed_ms=elapsed,
        detail={
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            "completed": len(completed),
            "empty": len(empty),
            "failed_codes": sorted(failed)[:100],
            "source": "eastmoney-datacenter",
        },
    )


__all__ = [
    "backfill_prices",
    "backfill_moneyflow",
    "backfill_valuation",
    "last_completed_trade_date",
]
