"""
数据新鲜度服务（方案 A10 / E2）
==============================
把"某个数据集现在可信吗"的判断集中在一处，供 `/meta/freshness` 与各业务接口共用。
业务接口返回时附带 freshness 子集，前端就能在每张卡片上标注来源与时效。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import TIMEZONE
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
    "trade_calendar": ("交易日历", 24 * 30, True),
    "news": ("财经新闻", 6, False),
    "algo_run": ("选股任务", 30, False),
    "signal_perf": ("信号前瞻统计", 30, False),
    "daily_report": ("AI 日报", 30, False),
}

_TZ = ZoneInfo(TIMEZONE)


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=_TZ)
    return value


def classify(dataset: str, row: MetaDataQuality | None) -> str:
    """ok / stale / quality_failed / error / unknown"""
    if row is None or row.last_success_at is None:
        return "unknown"
    if row.status in {"error", "quality_failed"}:
        return row.status
    last = _as_aware(row.last_success_at)
    max_age_h = DATASET_META.get(dataset, ("", 48, False))[1]
    if last is not None and datetime.now(tz=last.tzinfo) - last > timedelta(hours=max_age_h):
        return "stale"
    return row.status or "ok"


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
    bad = [i for i in critical if i["status"] in {"unknown", "stale", "error", "quality_failed"}]
    if not critical:
        return {"ready": False, "message": "尚未建立数据质量记录"}
    if bad:
        names = "、".join(i["display_name"] for i in bad)
        return {"ready": False, "message": f"关键数据集不可用：{names}"}
    return {"ready": True, "message": "关键数据集均新鲜可用"}


__all__ = ["get_freshness_items", "summarize", "classify", "to_item", "DATASET_META"]
