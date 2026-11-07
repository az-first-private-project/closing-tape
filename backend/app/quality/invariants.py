"""数据不变量只读检查（W0-3）。

所有查询只读，不修改任何业务表。检查结果分两类：
- ``violation``：数据自相矛盾或停在已知坏状态，脚本退出码 1；
- ``warning``：需要关注但可能是合法状态（如大板块 partial 名单）。

首批检查对应《GFNA V6 遗留问题修复与回归防控计划》W0-3：
SCD2 区间、open partial、最新行情与新鲜度一致性、行情/特征/评分/资金流
覆盖断层、超时 running 任务、失败运行是否留下成功产物。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Sequence

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    BacktestRun,
    FactFeatureDaily,
    FactMoneyflow,
    FactStockDaily,
    FactStockScoreDaily,
    IngestJob,
    MetaDataQuality,
    StgRun,
    TradeCalendar,
)

STALE_JOB_HOURS = 2
COVERAGE_WINDOW = 5
MIN_COVERAGE_RATIO = 0.8
SAMPLE_LIMIT = 10

# (dataset, ORM 模型, 是否要求最新交易日当天有数据)
COVERAGE_TABLES: tuple[tuple[str, Any, bool], ...] = (
    ("stock_daily", FactStockDaily, True),
    ("stock_features", FactFeatureDaily, True),
    ("stock_scores", FactStockScoreDaily, True),
    ("moneyflow", FactMoneyflow, True),
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _sample(rows: Sequence[Any]) -> list[dict[str, Any]]:
    return [
        {key: _jsonable(value) for key, value in dict(row).items()}
        for row in list(rows)[:SAMPLE_LIMIT]
    ]


@dataclass(slots=True)
class InvariantCheck:
    name: str
    severity: str
    count: int
    message: str
    sample: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "severity": self.severity,
            "count": self.count,
            "message": self.message,
            "sample": self.sample,
        }


@dataclass(slots=True)
class InvariantReport:
    checks: list[InvariantCheck]

    @property
    def violations(self) -> list[InvariantCheck]:
        return [
            item for item in self.checks
            if item.severity == "violation" and item.count > 0
        ]

    @property
    def warnings(self) -> list[InvariantCheck]:
        return [
            item for item in self.checks
            if item.severity == "warning" and item.count > 0
        ]

    @property
    def ok(self) -> bool:
        return not self.violations

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": {
                "checks": len(self.checks),
                "violations": len(self.violations),
                "warnings": len(self.warnings),
                "violation_names": [item.name for item in self.violations],
            },
            "checks": [item.as_dict() for item in self.checks],
        }


def _check(
    name: str,
    count: int,
    message: str,
    *,
    severity: str = "violation",
    sample: Sequence[Any] = (),
) -> InvariantCheck:
    return InvariantCheck(
        name=name,
        severity=severity,
        count=count,
        message=message,
        sample=_sample(sample) if count else [],
    )


async def _scd2_checks(db: AsyncSession) -> list[InvariantCheck]:
    cte = """
        WITH intervals AS (
            SELECT sector_code, stock_code, effective_from, effective_to,
                   lead(effective_from) OVER (
                       PARTITION BY sector_code, stock_code ORDER BY effective_from
                   ) AS next_from
            FROM dim_sector_member
        )
    """
    negatives = """
        effective_to IS NOT NULL AND effective_to < effective_from
    """
    overlaps = """
        (effective_to IS NULL AND next_from IS NOT NULL)
        OR (effective_to IS NOT NULL AND next_from IS NOT NULL AND effective_to > next_from)
    """
    zero_length = """
        effective_to IS NOT NULL AND effective_to = effective_from
    """

    async def _count_and_sample(where: str) -> tuple[int, list[Any]]:
        count = int(
            await db.scalar(text(f"{cte} SELECT count(*) FROM intervals WHERE {where}"))
            or 0
        )
        rows = (
            await db.execute(
                text(
                    f"{cte} SELECT * FROM intervals WHERE {where} "
                    "ORDER BY sector_code, stock_code, effective_from LIMIT :limit"
                ),
                {"limit": SAMPLE_LIMIT},
            )
        ).mappings().all()
        return count, rows

    negative_count, negative_sample = await _count_and_sample(negatives)
    overlap_count, overlap_sample = await _count_and_sample(overlaps)
    zero_count, zero_sample = await _count_and_sample(zero_length)

    checks = [
        _check(
            "scd2_negative_interval",
            negative_count,
            "SCD2 区间结束日早于开始日",
            sample=negative_sample,
        ),
        _check(
            "scd2_overlap",
            overlap_count,
            "同一板块+股票的 SCD2 区间重叠",
            sample=overlap_sample,
        ),
        _check(
            "scd2_zero_length",
            zero_count,
            "SCD2 存在开始日=结束日的零长区间（多为当日加入又移除）",
            severity="warning",
            sample=zero_sample,
        ),
    ]

    partial_rows = (
        await db.execute(
            text(
                "SELECT sector_code, sector_name, count(*) AS members "
                "FROM dim_sector_member "
                "WHERE effective_to IS NULL AND is_partial "
                "GROUP BY sector_code, sector_name "
                "ORDER BY members DESC, sector_code"
            )
        )
    ).mappings().all()
    checks.append(
        _check(
            "open_partial_sectors",
            len(partial_rows),
            "存在 open 的 partial 板块名单；这些板块不参与市值/家数聚合，需确认仍属预期",
            severity="warning",
            sample=partial_rows,
        )
    )
    return checks


async def _coverage_days(
    db: AsyncSession, latest: date
) -> list[date]:
    days = list(
        (
            await db.execute(
                select(TradeCalendar.cal_date)
                .where(
                    TradeCalendar.is_open.is_(True),
                    TradeCalendar.cal_date <= latest,
                )
                .order_by(TradeCalendar.cal_date.desc())
                .limit(COVERAGE_WINDOW)
            )
        ).scalars()
    )
    if not days:
        days = list(
            (
                await db.execute(
                    select(FactStockDaily.trade_date)
                    .distinct()
                    .where(FactStockDaily.trade_date <= latest)
                    .order_by(FactStockDaily.trade_date.desc())
                    .limit(COVERAGE_WINDOW)
                )
            ).scalars()
        )
    return sorted(days)


async def _counts_by_day(db: AsyncSession, model: Any, days: Sequence[date]) -> dict[date, int]:
    if not days:
        return {}
    rows = (
        await db.execute(
            select(model.trade_date, func.count())
            .where(model.trade_date.in_(list(days)))
            .group_by(model.trade_date)
        )
    ).all()
    return {row[0]: int(row[1]) for row in rows}


async def _coverage_checks(
    db: AsyncSession, latest: date
) -> tuple[list[InvariantCheck], dict[str, date | None]]:
    days = await _coverage_days(db, latest)
    daily_counts = await _counts_by_day(db, FactStockDaily, days)
    checks: list[InvariantCheck] = []
    max_dates: dict[str, date | None] = {"stock_daily": latest}

    missing_daily = [
        {"trade_date": day, "rows": daily_counts.get(day, 0)}
        for day in days
        if daily_counts.get(day, 0) <= 0
    ]
    checks.append(
        _check(
            "stock_daily_recent_coverage",
            len(missing_daily),
            f"最近 {len(days)} 个交易日存在完全缺失的行情日",
            sample=missing_daily,
        )
    )

    for name, model, require_latest in COVERAGE_TABLES:
        if name == "stock_daily":
            continue
        max_date = await db.scalar(select(func.max(model.trade_date)))
        max_dates[name] = max_date
        counts = await _counts_by_day(db, model, days)
        gaps: list[dict[str, Any]] = []
        if require_latest and max_date != latest:
            gaps.append(
                {
                    "trade_date": latest,
                    "rows": 0,
                    "reason": f"最新日期 {max_date} != 行情日 {latest}",
                }
            )
        for day in days:
            if max_date is not None and day > max_date:
                continue
            rows = counts.get(day, 0)
            baseline = daily_counts.get(day, 0)
            if rows <= 0:
                gaps.append({"trade_date": day, "rows": 0, "baseline": baseline})
            elif baseline and rows < baseline * MIN_COVERAGE_RATIO:
                gaps.append(
                    {
                        "trade_date": day,
                        "rows": rows,
                        "baseline": baseline,
                        "ratio": round(rows / baseline, 4),
                    }
                )
        checks.append(
            _check(
                f"{name}_recent_coverage",
                len(gaps),
                f"最近 {len(days)} 个交易日 {name} 缺失或覆盖不足 "
                f"{MIN_COVERAGE_RATIO:.0%}",
                sample=gaps,
            )
        )
    return checks, max_dates


async def _freshness_checks(
    db: AsyncSession, latest: date, max_dates: dict[str, date | None]
) -> list[InvariantCheck]:
    checks: list[InvariantCheck] = []
    for name, _model, _require_latest in COVERAGE_TABLES:
        meta = await db.get(MetaDataQuality, name)
        data_max = max_dates.get(name) or latest
        issues: list[dict[str, Any]] = []
        if meta is None:
            issues.append({"dataset": name, "reason": "meta_data_quality 缺行"})
            checks.append(
                _check(f"{name}_freshness", 1, "数据集缺少新鲜度记录", sample=issues)
            )
            continue
        if meta.status != "ok":
            checks.append(
                _check(
                    f"{name}_status",
                    1,
                    f"数据集状态为 {meta.status}（错误：{meta.error or '无'}）",
                    severity="warning",
                    sample=[{"dataset": name, "status": meta.status}],
                )
            )
        if meta.status == "ok":
            if meta.last_success_at is None:
                issues.append({"dataset": name, "reason": "status=ok 但 last_success_at 为空"})
            elif data_max and meta.last_success_at.date() < data_max:
                issues.append(
                    {
                        "dataset": name,
                        "last_success_at": meta.last_success_at,
                        "data_date": data_max,
                        "reason": "最近成功时间早于数据日期",
                    }
                )
        if meta.last_trade_date is not None and data_max and meta.last_trade_date != data_max:
            issues.append(
                {
                    "dataset": name,
                    "meta_trade_date": meta.last_trade_date,
                    "data_date": data_max,
                    "reason": "meta 交易日与表内最新日期不一致",
                }
            )
        checks.append(
            _check(
                f"{name}_freshness",
                len(issues),
                "新鲜度记录与库内数据不一致",
                sample=issues,
            )
        )
    return checks


async def _stale_job_checks(db: AsyncSession) -> list[InvariantCheck]:
    jobs = (
        await db.execute(
            text(
                "SELECT job_id, task, status, started_at "
                "FROM ingest_job "
                "WHERE status = 'running' "
                "AND started_at < now() - make_interval(hours => :hours) "
                "ORDER BY started_at LIMIT :limit"
            ),
            {"hours": STALE_JOB_HOURS, "limit": SAMPLE_LIMIT},
        )
    ).mappings().all()
    job_count = int(
        await db.scalar(
            text(
                "SELECT count(*) FROM ingest_job "
                "WHERE status = 'running' "
                "AND started_at < now() - make_interval(hours => :hours)"
            ),
            {"hours": STALE_JOB_HOURS},
        )
        or 0
    )

    runs = (
        await db.execute(
            text(
                "SELECT run_id, algo_code, trade_date, status, created_at "
                "FROM stg_run "
                "WHERE status = 'running' "
                "AND created_at < now() - make_interval(hours => :hours) "
                "ORDER BY created_at LIMIT :limit"
            ),
            {"hours": STALE_JOB_HOURS, "limit": SAMPLE_LIMIT},
        )
    ).mappings().all()
    run_count = int(
        await db.scalar(
            text(
                "SELECT count(*) FROM stg_run "
                "WHERE status = 'running' "
                "AND created_at < now() - make_interval(hours => :hours)"
            ),
            {"hours": STALE_JOB_HOURS},
        )
        or 0
    )
    return [
        _check(
            "stale_running_jobs",
            job_count,
            f"ingest_job 存在超过 {STALE_JOB_HOURS} 小时仍为 running 的任务",
            sample=jobs,
        ),
        _check(
            "stale_running_strategy_runs",
            run_count,
            f"stg_run 存在超过 {STALE_JOB_HOURS} 小时仍为 running 的运行",
            sample=runs,
        ),
    ]


async def _misclassified_run_checks(db: AsyncSession) -> list[InvariantCheck]:
    failed_signals = (
        await db.execute(
            text(
                "SELECT r.run_id, r.algo_code, r.trade_date, r.status "
                "FROM stg_run r "
                "WHERE r.status = 'failed' "
                "AND EXISTS (SELECT 1 FROM stg_signal s WHERE s.run_id = r.run_id) "
                "ORDER BY r.trade_date DESC LIMIT :limit"
            ),
            {"limit": SAMPLE_LIMIT},
        )
    ).mappings().all()
    failed_signals_count = int(
        await db.scalar(
            text(
                "SELECT count(*) FROM stg_run r "
                "WHERE r.status = 'failed' "
                "AND EXISTS (SELECT 1 FROM stg_signal s WHERE s.run_id = r.run_id)"
            )
        )
        or 0
    )

    success_with_failures = (
        await db.execute(
            text(
                "SELECT job_id, task, status, failed, error FROM ingest_job "
                "WHERE status = 'success' AND failed > 0 "
                "ORDER BY started_at DESC LIMIT :limit"
            ),
            {"limit": SAMPLE_LIMIT},
        )
    ).mappings().all()
    success_with_failures_count = int(
        await db.scalar(
            text("SELECT count(*) FROM ingest_job WHERE status = 'success' AND failed > 0")
        )
        or 0
    )

    failed_backtests = (
        await db.execute(
            text(
                "SELECT bt_id, algo_code, status, created_at FROM backtest_run "
                "WHERE status = 'failed' AND metrics IS NOT NULL "
                "ORDER BY created_at DESC LIMIT :limit"
            ),
            {"limit": SAMPLE_LIMIT},
        )
    ).mappings().all()
    failed_backtests_count = int(
        await db.scalar(
            text(
                "SELECT count(*) FROM backtest_run "
                "WHERE status = 'failed' AND metrics IS NOT NULL"
            )
        )
        or 0
    )
    return [
        _check(
            "failed_run_with_signals",
            failed_signals_count,
            "状态为 failed 的策略 run 仍残留信号，可能被成功统计误计",
            sample=failed_signals,
        ),
        _check(
            "success_job_with_failures",
            success_with_failures_count,
            "ingest_job 标记 success 但 failed > 0",
            sample=success_with_failures,
        ),
        _check(
            "failed_backtest_with_metrics",
            failed_backtests_count,
            "回测标记 failed 但仍写入 metrics，可能被成功统计误计",
            sample=failed_backtests,
        ),
    ]


async def collect_invariants(db: AsyncSession) -> InvariantReport:
    checks: list[InvariantCheck] = []
    latest = await db.scalar(select(func.max(FactStockDaily.trade_date)))
    if latest is None:
        checks.append(
            _check("stock_daily_empty", 1, "fact_stock_daily 为空，无法判断最新行情日")
        )
        max_dates: dict[str, date | None] = {}
    else:
        checks.extend(await _scd2_checks(db))
        coverage_checks, max_dates = await _coverage_checks(db, latest)
        checks.extend(coverage_checks)
        checks.extend(await _freshness_checks(db, latest, max_dates))
    checks.extend(await _stale_job_checks(db))
    checks.extend(await _misclassified_run_checks(db))
    return InvariantReport(checks=checks)


async def log_invariants(logger: logging.Logger) -> InvariantReport | None:
    """在独立会话里跑一次只读检查并写日志；任何异常都不影响主流程。"""
    from app.db.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            report = await collect_invariants(db)
    except Exception:                                   # noqa: BLE001
        logger.exception("数据不变量检查失败（不阻断主流程）")
        return None
    if report.violations:
        logger.warning(
            "数据不变量违规 %d 项：%s",
            len(report.violations),
            [item.name for item in report.violations],
        )
    else:
        logger.info("数据不变量检查通过（warning=%d）", len(report.warnings))
    return report


__all__ = [
    "COVERAGE_TABLES", "MIN_COVERAGE_RATIO", "STALE_JOB_HOURS",
    "InvariantCheck", "InvariantReport", "collect_invariants", "log_invariants",
]
