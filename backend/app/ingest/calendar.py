"""
真实交易日历（方案 A7，修复被点名的 P0 缺陷 ``weekday() < 5``）
=================================================================
为什么必须用真实日历：

    A 股每年有 10 天以上法定节假日休市（春节/国庆最长 9 天），只按"周一到周五"
    判断会在节假日跑空、把前一天的旧数据当成今天写入（脏数据），也会在调休上班的
    周六误判为休市。所有"今天是不是交易日""上一个/下一个交易日"必须只信这张表。

数据源：``akshare.tool_trade_date_hist_sina()``（主）→ baostock ``query_trade_dates``（兜底）。
表中同时存**休市日**（``is_open=False``），用于区分"休市"与"没数据"。

降级策略（按优先级）：

    1. 本地 ``trade_calendar`` 有完整区间 → 直接用（只读，不联网）；
    2. 本地缺失 → 调 Provider 同步一次（受 ``INGEST_NETWORK_ENABLED`` 控制）；
    3. 仍拿不到 → ``pandas.bdate_range`` 兜底（仅剔除周末），并把写入行的
       ``source`` 标记成 ``"fallback"``，前端/日志能看出"日历未同步，结果不可全信"。

**本模块不使用 ``weekday() < 5`` 做业务判断**（只出现在 fallback 的 bdate_range 里，
且必须带 source 标记）。
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Any, Iterable

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.db.models.meta import TradeCalendar
from app.ingest.base import FetchResult, upsert_rows
from app.ingest.quality import QualityReport, record_quality

logger = get_logger(__name__)


# ============================================================
# 小工具
# ============================================================
def _coerce_date(value: Any) -> date:
    """把 date/datetime/str/Timestamp 统一成 ``datetime.date``。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, pd.Timestamp):
        return value.date()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"无法解析日期: {value!r}")


def _normalize_range(start: date, end: date) -> tuple[date, date]:
    """规整区间（参数顺序反了自动交换）。"""
    start = _coerce_date(start)
    end = _coerce_date(end)
    return (start, end) if start <= end else (end, start)


def _iter_days(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _today_cn() -> date:
    """A 股"今天"（Asia/Shanghai）。"""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(config.TIMEZONE)).date()
    except Exception:  # pragma: no cover —— Windows 缺 tzdata 时兜底固定 +8
        return (datetime.now() + timedelta(hours=8)).date()


async def _load_calendar_map(
    db: AsyncSession, start: date, end: date
) -> dict[date, bool]:
    """读取区间内本地日历（date -> is_open）。"""
    stmt = (
        select(TradeCalendar.cal_date, TradeCalendar.is_open)
        .where(TradeCalendar.cal_date >= start, TradeCalendar.cal_date <= end)
        .order_by(TradeCalendar.cal_date)
    )
    rows = (await db.execute(stmt)).all()
    return {row[0]: bool(row[1]) for row in rows}


# ============================================================
# 同步
# ============================================================
async def sync_trade_calendar(
    db: AsyncSession, start: date, end: date
) -> FetchResult:
    """从数据源同步交易日历并 upsert 到 ``trade_calendar``。

    Args:
        db: ``AsyncSession``（事务由调用方管理；内部只 flush 级操作）。
        start / end: 需要覆盖的日期区间（含两端）。

    Returns:
        ``FetchResult``；``rows`` 为写入行数，``detail["open_days"]`` 为开市天数，
        ``detail["source"]`` 为实际数据源。

    Raises:
        ProviderError: 所有数据源都拿不到日历，或返回空日历。
            绝不把"源异常"写成"全部休市"，否则后续所有交易日判断都会错。
    """
    from app.ingest.service import get_provider_chain  # 延迟导入：避免 service ↔ calendar 循环

    started = time.perf_counter()
    start, end = _normalize_range(start, end)

    chain = get_provider_chain()
    fetched = await chain.fetch_trade_calendar(start, end)
    frame = fetched.detail.get("data")

    open_dates: set[date] = set()
    if frame is not None and len(frame) > 0:
        column = "trade_date" if "trade_date" in frame.columns else frame.columns[0]
        for value in frame[column]:
            try:
                open_dates.add(_coerce_date(value))
            except (TypeError, ValueError):
                continue

    if not open_dates:
        # 源返回空：可能是接口异常，也可能是区间内确实无交易日。
        # 无论哪种都不能写"全休市"，否则 is_trade_day 会永久返回 False。
        raise ProviderError(
            f"交易日历数据为空，拒绝写入（{start} ~ {end}）",
            detail={"provider": "calendar", "start": start.isoformat(),
                    "end": end.isoformat()},
        )

    source = fetched.source or "akshare"
    today = _today_cn()
    rows: list[dict[str, Any]] = []
    for day in _iter_days(start, end):
        if day > today and day not in open_dates:
            # 未来日期的"休市"尚未确认（源可能只披露到本年度），不要臆测，跳过不写
            continue
        rows.append({"cal_date": day, "is_open": day in open_dates, "source": source})

    if not rows:  # pragma: no cover —— 正常情况下不可能
        return FetchResult(rows=0, total=0, elapsed_ms=0,
                           detail={"source": source, "empty": True})

    written = await upsert_rows(
        db,
        TradeCalendar,
        rows,
        ["cal_date"],
        update_columns=["is_open", "source"],
    )

    # 质量：开市天数占工作日的比例异常低 → warning（例如只拿到一年的一部分）
    past_days = [d for d in _iter_days(start, min(end, today))]
    weekday_count = sum(1 for d in past_days if d.weekday() < 5)  # 仅统计口径，不用于业务判断
    open_past = sum(1 for d in open_dates if start <= d <= min(end, today))
    report = QualityReport(
        dataset="trade_calendar",
        rows=open_past,
        expected=max(weekday_count, 1),
        coverage=round(open_past / weekday_count, 4) if weekday_count else 0.0,
    )
    if weekday_count and open_past < weekday_count * 0.8:
        report.add(
            "calendar_open_days_suspicious",
            count=open_past,
            severity="warning",
            message=(
                f"区间内开市 {open_past} 天，明显少于工作日 {weekday_count} 天，"
                "请核对数据源是否完整"
            ),
        )
    if fetched.failed:
        report.add(
            "partial_calendar",
            count=fetched.failed,
            severity="warning",
            message=f"日历获取存在 {fetched.failed} 个失败",
        )
    await record_quality(db, "trade_calendar", report, None, "ok")

    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info(
        "交易日历同步完成 %s ~ %s：开市 %d 天 / 写入 %d 行（source=%s）",
        start, end, len(open_dates), written, source,
    )
    return FetchResult(
        rows=written,
        total=len(rows),
        failed=fetched.failed,
        elapsed_ms=elapsed,
        detail={
            "source": source,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "open_days": len(open_dates),
            "attempts": fetched.detail.get("attempts"),
        },
    )


# ============================================================
# 读取（业务层唯一入口）
# ============================================================
async def get_trade_dates(db: AsyncSession, start: date, end: date) -> list[date]:
    """返回区间内全部**开市日**（升序），只读本地库。

    降级链：
        1. 本地完整 → 直接返回；
        2. 本地缺失 → 同步一次再读；
        3. 仍缺失 → 对缺失日期用 ``pandas.bdate_range`` 兜底并写 ``source="fallback"``
           （会在日志里 warning，提示日历未同步）。
    """
    start, end = _normalize_range(start, end)
    total_days = (end - start).days + 1
    calendar = await _load_calendar_map(db, start, end)

    if len(calendar) >= total_days:
        return sorted(day for day, is_open in calendar.items() if is_open)

    # 2. 本地不完整：尝试同步（联网开关关闭时直接走兜底）
    if config.INGEST_NETWORK_ENABLED:
        try:
            await sync_trade_calendar(db, start, end)
        except Exception as exc:  # noqa: BLE001 —— 日历源挂了也必须能继续（用兜底）
            logger.warning("交易日历同步失败，将使用工作日兜底（%s）：%s", str(exc), exc)
        calendar = await _load_calendar_map(db, start, end)
        if len(calendar) >= total_days:
            return sorted(day for day, is_open in calendar.items() if is_open)

    # 3. 最终兜底：缺失的日期用 bdate_range（只剔除周末）补，并标记 source=fallback
    missing = [day for day in _iter_days(start, end) if day not in calendar]
    if not missing:
        return sorted(day for day, is_open in calendar.items() if is_open)

    try:
        business_days = [
            ts.date() for ts in pd.bdate_range(start=start, end=end) if ts.date() in set(missing)
        ]
    except Exception:  # pragma: no cover
        business_days = [day for day in missing if day.weekday() < 5]

    if business_days:
        fallback_rows = [
            {"cal_date": day, "is_open": True, "source": "fallback"}
            for day in business_days
        ]
        try:
            await upsert_rows(
                db,
                TradeCalendar,
                fallback_rows,
                ["cal_date"],
                update_columns=["is_open", "source"],
            )
            for day in business_days:
                calendar[day] = True
        except Exception:  # noqa: BLE001 —— 兜底写库失败不影响返回值
            logger.exception("写入 fallback 交易日历失败")
        logger.warning(
            "交易日历不完整，缺失 %d 天已用 bdate_range 兜底（source=fallback）：%s ~ %s",
            len(business_days), start, end,
        )

    return sorted(day for day, is_open in calendar.items() if is_open)


async def is_trade_day(db: AsyncSession, day: date) -> bool:
    """某一天是否 A 股交易日（只信本地日历，缺行时补齐周边 ±14 天）。"""
    day = _coerce_date(day)
    stmt = select(TradeCalendar.is_open).where(TradeCalendar.cal_date == day)
    value = (await db.execute(stmt)).scalar_one_or_none()
    if value is not None:
        return bool(value)

    # 本地没有该日：补齐周边窗口（内部会触发同步 / 兜底）
    dates = await get_trade_dates(db, day - timedelta(days=14), day + timedelta(days=14))
    return day in set(dates)


async def latest_trade_date(db: AsyncSession, on_or_before: date) -> date | None:
    """``on_or_before`` 当天或之前**最近的一个交易日**。"""
    target = _coerce_date(on_or_before)
    stmt = select(func.max(TradeCalendar.cal_date)).where(
        TradeCalendar.is_open.is_(True),
        TradeCalendar.cal_date <= target,
    )
    value = (await db.execute(stmt)).scalar_one_or_none()
    if value is not None:
        return value

    dates = await get_trade_dates(db, target - timedelta(days=45), target)
    return max(dates) if dates else None


async def prev_trade_date(db: AsyncSession, day: date) -> date | None:
    """上一个交易日（严格早于 ``day``）。"""
    return await latest_trade_date(db, _coerce_date(day) - timedelta(days=1))


async def next_trade_date(db: AsyncSession, day: date) -> date | None:
    """下一个交易日（严格晚于 ``day``）。"""
    target = _coerce_date(day)
    stmt = select(func.min(TradeCalendar.cal_date)).where(
        TradeCalendar.is_open.is_(True),
        TradeCalendar.cal_date > target,
    )
    value = (await db.execute(stmt)).scalar_one_or_none()
    if value is not None:
        return value

    dates = await get_trade_dates(db, target + timedelta(days=1), target + timedelta(days=45))
    return min(dates) if dates else None


async def ensure_calendar_range(db: AsyncSession, start: date, end: date) -> list[date]:
    """确保区间日历可用并返回开市日列表（历史回补/质量校验前调用）。"""
    return await get_trade_dates(db, start, end)


__all__ = [
    "sync_trade_calendar",
    "get_trade_dates",
    "ensure_calendar_range",
    "is_trade_day",
    "latest_trade_date",
    "prev_trade_date",
    "next_trade_date",
]
