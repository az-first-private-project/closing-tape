"""
数据新鲜度服务（方案 A10 / E2）
==============================
把"某个数据集现在可信吗"的判断集中在一处，供 `/meta/freshness` 与各业务接口共用。
业务接口返回时附带 freshness 子集，前端就能在每张卡片上标注来源与时效。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import TIMEZONE
from app.core.status import QualityStatus
from app.db.models import MetaDataQuality

# dataset -> (展示名, 过期小时数, 是否关键数据集)
DATASET_META: dict[str, tuple[str, int, bool]] = {
    "stock_universe": ("股票池", 48, True),
    "stock_daily": ("个股日线", 30, True),
    "stock_features": ("技术指标", 30, True),
    "stock_scores": ("个股因子评分", 30, True),
    "sector_daily": ("板块日线", 30, False),
    "sector_members": ("板块成分", 24 * 30, False),
    "moneyflow": ("个股资金流", 30, False),
    "valuation": ("历史估值(PE/PB/股息率)", 24 * 30, False),
    "index_daily": ("指数日线", 30, False),
    "margin": ("融资融券", 30, False),
    "macro_rates": ("国债收益率", 48, False),
    "market_factors": ("市场因子", 30, False),
    "market_temperature": ("市场温度", 30, False),
    "market_regime": ("市场状态", 30, False),
    "trade_calendar": ("交易日历", 24 * 30, True),
    "news": ("财经新闻", 6, False),
    "algo_run": ("选股任务", 30, False),
    "signal_perf": ("信号前瞻统计", 30, False),
    "daily_report": ("AI 日报", 30, False),
}

_TZ = ZoneInfo(TIMEZONE)

_WRITE_MODES = frozenset({"success", "attempt", "failure"})


async def write_freshness(
    db: AsyncSession,
    dataset: str,
    *,
    status: QualityStatus | str,
    mode: str = "attempt",
    now: datetime | None = None,
    trade_date: date | None = None,
    rows: int | None = None,
    coverage: float | None = None,
    anomalies: int | None = None,
    display_name: str | None = None,
    error: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """``meta_data_quality`` 的唯一写入口（W0-4）。

    mode 决定"这次采集能改写哪些列"，三种语义与原来三个写入点一一对应：

    - ``success``: 记录成功时间与本次行数/覆盖率/详情；
    - ``attempt``: 质量不合格的一次尝试，更新体检结果但**保留 last_success_at**；
    - ``failure``: 采集直接抛异常，只更新尝试时间/状态/错误，保留行数与成功信息。
    """
    if mode not in _WRITE_MODES:
        raise ValueError(f"未知的新鲜度写入模式: {mode}")
    current = QualityStatus(status)
    attempted_at = now or datetime.now()
    error_text = error[:2000] if error else None

    values: dict[str, Any] = {
        "dataset": dataset,
        "last_attempt_at": attempted_at,
        "status": current.value,
        "error": error_text,
    }
    update: dict[str, Any] = {
        "last_attempt_at": attempted_at,
        "status": current.value,
        "error": error_text,
    }
    if display_name is not None:
        values["display_name"] = display_name
        update["display_name"] = display_name

    if mode == "success":
        values.update(
            last_success_at=attempted_at,
            last_trade_date=trade_date,
            rows=rows,
            coverage=coverage,
            anomalies=anomalies,
            detail=detail,
        )
        update = {key: value for key, value in values.items() if key != "dataset"}
    elif mode == "attempt":
        # 失败不清空历史成功信息；last_trade_date 只在新插入时写入。
        values["last_trade_date"] = trade_date
        values.update(rows=rows, coverage=coverage, anomalies=anomalies, detail=detail)
        update.update(rows=rows, coverage=coverage, anomalies=anomalies, detail=detail)

    stmt = pg_insert(MetaDataQuality).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[MetaDataQuality.dataset], set_=update
    )
    await db.execute(stmt)


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=_TZ)
    return value


def classify(dataset: str, row: MetaDataQuality | None) -> str:
    """ok / stale / quality_failed / error / unknown"""
    if row is None or row.last_success_at is None:
        return QualityStatus.UNKNOWN.value
    if row.status in {QualityStatus.ERROR, QualityStatus.QUALITY_FAILED}:
        return row.status
    last = _as_aware(row.last_success_at)
    max_age_h = DATASET_META.get(dataset, ("", 48, False))[1]
    if last is not None and datetime.now(tz=last.tzinfo) - last > timedelta(hours=max_age_h):
        return QualityStatus.STALE.value
    return row.status or QualityStatus.OK.value


def to_item(dataset: str, row: MetaDataQuality | None) -> dict:
    display = DATASET_META.get(dataset, (dataset, 48, False))[0]
    return {
        "dataset": dataset,
        "display_name": (row.display_name if row and row.display_name else display),
        "status": classify(dataset, row),
        "is_critical": DATASET_META.get(dataset, ("", 48, False))[2],
        "last_success_at": row.last_success_at.isoformat() if row and row.last_success_at else None,
        "last_attempt_at": row.last_attempt_at.isoformat() if row and row.last_attempt_at else None,
        "last_trade_date": row.last_trade_date.isoformat() if row and row.last_trade_date else None,
        "rows": row.rows if row else None,
        "coverage": float(row.coverage) if row and row.coverage is not None else None,
        "anomalies": row.anomalies if row else None,
        "error": row.error if row else None,
    }


async def get_freshness_items(
    db: AsyncSession,
    datasets: list[str] | None = None,
) -> list[dict]:
    """批量取新鲜度。datasets 为 None 时返回全部已知数据集。"""
    result = await db.execute(select(MetaDataQuality))
    rows = {r.dataset: r for r in result.scalars().all()}
    wanted = datasets if datasets is not None else list(DATASET_META.keys())
    return [to_item(ds, rows.get(ds)) for ds in wanted]


def summarize(items: list[dict]) -> dict:
    """给出一句话结论，供 /health 与前端顶部提示使用。"""
    critical = [i for i in items if i.get("is_critical")]
    bad_status = {
        QualityStatus.UNKNOWN, QualityStatus.STALE,
        QualityStatus.ERROR, QualityStatus.QUALITY_FAILED,
    }
    bad = [i for i in critical if i["status"] in bad_status]
    if not critical:
        return {"ready": False, "message": "尚未建立数据质量记录"}
    if bad:
        names = "、".join(i["display_name"] for i in bad)
        return {"ready": False, "message": f"关键数据集不可用：{names}"}
    return {"ready": True, "message": "关键数据集均新鲜可用"}


__all__ = [
    "get_freshness_items", "summarize", "classify", "to_item", "write_freshness",
    "DATASET_META",
]
