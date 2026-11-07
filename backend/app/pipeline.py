"""
每日流水线编排（方案 8 / A8）
=============================
一条命令串起「采集 → 落库 → 指标 → 因子 → 板块评分 → 日报 → 选股」，
被 `worker.py`（定时）、`api/v1/admin.py`（手动）、`scripts/pipeline_cli.py`（命令行）共用。

设计要点：
  - **每一步独立 try/except**：一个数据源挂了不阻塞后续（沿用 V3 的好设计）
  - **非交易日短路**：用真实交易日历判断，节假日不跑空、不写脏数据（修 #17）
  - **每一步都写 meta_data_quality**：前端新鲜度徽标的唯一来源（方案 E2）
  - **可重入**：所有落库都是幂等 upsert，重复跑安全
"""
from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any, Iterable, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import bind_run, get_logger
from app.core.status import QualityStatus, TaskStatus
from app.services.freshness import write_freshness

logger = get_logger(__name__)

DEFAULT_DAILY_STEPS = (
    "calendar", "universe", "index", "sector", "sector_members", "snapshot",
    "moneyflow", "sector_agg", "margin", "valuation", "features", "scores",
    "sector_scores", "temperature", "report", "algos", "signal_perf",
)

# 支持 codes 定向执行的 task：这些任务的 rows 只是局部代码，不能代表数据集全量状态。
# 用它的行数/覆盖率覆盖 meta_data_quality 会把"全市场最近一次成功"冲掉
# （920 定向回补曾把 stock_daily 写成 rows=355、coverage=0.9916）。
_SCOPED_TASKS = {"backfill", "history_fast", "moneyflow_history", "valuation_fast", "rebuild"}

# 这些回补函数只返回 FetchResult，不自己写 meta_data_quality；部分失败由编排层补写。
# 不能让 failed>0 的整轮任务继续走 mark_success —— 那会把缺口洗成绿色。
_BULK_BACKFILL_TASKS = {"history_fast", "moneyflow_history", "valuation_fast"}

# task -> dataset（写新鲜度用）
TASK_DATASET = {
    "calendar": "trade_calendar",
    "universe": "stock_universe",
    "snapshot": "stock_daily",
    "backfill": "stock_daily",
    "history_fast": "stock_daily",
    "moneyflow_history": "moneyflow",
    "valuation_fast": "valuation",
    "rebuild": "stock_features",
    "index": "index_daily",
    "sector": "sector_daily",
    "sector_members": "sector_members",
    "sector_fallback": "sector_daily",
    "moneyflow": "moneyflow",
    "valuation": "valuation",
    "sector_agg": "sector_metrics",
    "margin": "margin",
    "features": "stock_features",
    "scores": "stock_scores",
    "sector_scores": "sector_scores",
    "temperature": "market_temperature",
    "report": "daily_report",
    "algos": "algo_run",
    "signal_perf": "signal_perf",
    "news": "news",
}


# ============================================================
# 新鲜度记录
# ============================================================

async def mark_success(
    db: AsyncSession,
    dataset: str,
    *,
    rows: int | None = None,
    coverage: float | None = None,
    anomalies: int = 0,
    trade_date: date | None = None,
    detail: dict | None = None,
) -> None:
    """记录一次成功的数据集更新（upsert）。"""
    await write_freshness(
        db, dataset,
        status=QualityStatus.OK,
        mode="success",
        rows=rows,
        coverage=coverage,
        anomalies=anomalies,
        trade_date=trade_date,
        detail=detail,
    )
    await db.commit()


async def mark_failure(
    db: AsyncSession,
    dataset: str,
    error: str,
    *,
    status: QualityStatus | str = QualityStatus.ERROR,
) -> None:
    """记录一次失败/质量不合格（**不清空历史成功时间**，便于前端区分"上次成功"与"这次失败"）。"""
    await write_freshness(
        db, dataset, status=status, mode="failure", error=error
    )
    await db.commit()


# ============================================================
# 工具
# ============================================================

async def resolve_trade_date(
    db: AsyncSession,
    requested: date | None = None,
) -> tuple[date | None, bool]:
    """把请求日期解析成"最近的一个交易日"。

    返回 (trade_date, is_today_open)。
    - 未指定日期：取今天；若今天不是交易日，取最近一个交易日并在调用侧决定是否跳过
    - 指定了非交易日：返回 None（调用方应拒绝写入）
    """
    from app.ingest.calendar import is_trade_day, latest_trade_date

    if requested is not None:
        if not await is_trade_day(db, requested):
            return None, False
        return requested, requested == date.today()

    today = date.today()
    if await is_trade_day(db, today):
        return today, True

    latest = await latest_trade_date(db, on_or_before=today)
    return latest, False


def _summary(result: Any) -> dict:
    """把 FetchResult / dict / int 统一成可序列化的统计。"""
    if result is None:
        return {"rows": 0}
    if isinstance(result, dict):
        return result
    if isinstance(result, int):
        return {"rows": result}
    rows = getattr(result, "rows", None)
    payload = {
        "rows": rows if rows is not None else getattr(result, "written", 0),
    }
    for attr in ("total", "failed", "elapsed_ms", "detail", "coverage", "anomalies"):
        value = getattr(result, attr, None)
        if value is not None:
            payload[attr] = value
    quality_status = getattr(result, "quality_status", None)
    if quality_status and quality_status != QualityStatus.OK:
        payload["quality_status"] = quality_status
    return payload


# ============================================================
# 单任务执行
# ============================================================

async def _reset_session(db: AsyncSession) -> None:
    """失败/断连后回滚共享会话，让下一步能重新取连接。

    日更全链路共用同一个 session：某一步因连接断开失败后，若不清掉失效事务，
    后续每一步都会复用同一个已关闭连接并瞬间失败（2026-11-03 sector_members
    重试风暴后的 10 余步 InterfaceError 即此原因）。
    """
    try:
        await db.rollback()
    except Exception:                                 # noqa: BLE001
        logger.warning("会话回滚失败（连接可能已断开），下一步将重新取连接", exc_info=True)


async def run_task(
    task: str,
    *,
    trade_date: date | None = None,
    start: date | None = None,
    end: date | None = None,
    codes: str | None = None,
    db: AsyncSession | None = None,
) -> dict:
    """执行单个任务并返回统计。这是 admin API 与 worker 的统一入口。"""
    if db is None:
        from app.db.session import AsyncSessionLocal
        async with AsyncSessionLocal() as own_db:
            return await run_task(task, trade_date=trade_date, start=start, end=end,
                                  codes=codes, db=own_db)

    started = time.perf_counter()
    dataset = TASK_DATASET.get(task, task)
    # 定向回补只覆盖部分代码：rows/coverage 不代表数据集全量，写新鲜度会误导监控
    scoped = bool(codes and codes.strip()) and task in _SCOPED_TASKS

    with bind_run(task=task, dataset=dataset):
        logger.info("任务开始")
        try:
            payload = await _dispatch(task, trade_date=trade_date, start=start,
                                      end=end, codes=codes, db=db)
        except Exception as exc:                      # noqa: BLE001 —— 兜底并记录，不吞掉
            logger.exception("任务失败")
            await _reset_session(db)
            if scoped:
                logger.warning("定向回补失败，保留数据集 %s 原有新鲜度", dataset)
            else:
                try:
                    await mark_failure(db, dataset, f"{type(exc).__name__}: {exc}")
                except Exception:                     # noqa: BLE001 —— DB 不可用时只能留日志
                    logger.exception("记录任务失败状态也失败 dataset=%s", dataset)
            return {
                "task": task, "dataset": dataset, "status": TaskStatus.FAILED.value,
                "error": f"{type(exc).__name__}: {exc}",
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
            }

        elapsed = int((time.perf_counter() - started) * 1000)
        # 显式提交：引擎/策略函数在外部 session 下只 flush，事务所有权在编排层
        try:
            await db.commit()
        except Exception:
            await _reset_session(db)
            raise
        # 返回体可能是 FetchResult（采集层）或 dict（引擎层），统一成可序列化摘要
        stats = _summary(payload)
        rows = stats.get("rows")
        failed_count = int(stats.get("failed") or 0)
        quality_status = QualityStatus(stats.get("quality_status") or QualityStatus.OK)
        detail = stats.get("detail") if isinstance(stats.get("detail"), dict) else None
        if failed_count:
            logger.warning("任务 %s 自动重试后仍有 %d 个标的失败", task, failed_count)
        # FetchResult 没有 coverage 字段，采集层把覆盖率放在 detail 里；
        # 不取出来会把 record_quality 刚写入的精确覆盖率覆盖成 NULL。
        coverage = stats.get("coverage")
        if coverage is None and detail is not None:
            coverage = detail.get("coverage")
        # 跳过标记：FetchResult 放在 detail.skipped，引擎层 dict 放在顶层 skipped
        skipped_reason = stats.get("skipped") or (detail or {}).get("skipped")
        if task != "calendar":
            if scoped:
                logger.info("定向回补（codes 非空）不更新数据集 %s 的新鲜度", dataset)
            elif skipped_reason and skipped_reason != "already_collected":
                # 跳过不等于成功：之前这里会 mark_success(rows=0)，把快照未到点/
                # 限频/锁占用写成"0 行且成功"，监控误判为已更新。
                logger.info(
                    "任务 %s 跳过（%s），不更新数据集 %s 的新鲜度",
                    task, skipped_reason, dataset,
                )
            elif quality_status is QualityStatus.OK:
                if skipped_reason == "already_collected":
                    # 数据已在库且达到完整阈值：记真实存量行数，不能记 0。
                    rows = detail.get("existing") or rows
                await mark_success(
                    db, dataset, rows=rows,
                    coverage=coverage,
                    anomalies=int(stats.get("anomalies") or 0),
                    trade_date=trade_date,
                    detail=detail,
                )
            else:
                # 采集层已写入非 ok 状态（如指数部分缺失）：mark_success 会把 stale
                # 覆盖成 ok，导致监控看不到缺口。保留采集层记录（事务在下方已 commit）。
                if task in _BULK_BACKFILL_TASKS and failed_count > 0:
                    # 回补函数自己不写质量记录：这里补一条 quality_failed，
                    # 保留 last_success_at，但让监控和前端能看到本轮有代码失败。
                    sample = (detail or {}).get("failed_codes") or []
                    suffix = f"：{', '.join(map(str, sample[:10]))}" if sample else ""
                    try:
                        await mark_failure(
                            db, dataset,
                            f"自动重试后仍有 {failed_count} 个标的失败{suffix}",
                            status=QualityStatus.QUALITY_FAILED,
                        )
                    except Exception:                 # noqa: BLE001 —— 记录失败不能改变任务结果
                        logger.exception("记录部分失败状态失败 dataset=%s", dataset)
                else:
                    logger.warning(
                        "任务 %s 质量状态为 %s，保留采集层质量记录", task, quality_status
                    )
        logger.info("任务完成 rows=%s elapsed_ms=%s", rows, elapsed)
        if quality_status is not QualityStatus.OK:
            status = TaskStatus.PARTIAL.value
        elif skipped_reason:
            status = TaskStatus.SKIPPED.value
        else:
            status = TaskStatus.SUCCESS.value
        return {"task": task, "dataset": dataset,
                "status": status,
                "elapsed_ms": elapsed, **stats}


async def _dispatch(
    task: str,
    *,
    trade_date: date | None,
    start: date | None,
    end: date | None,
    codes: str | None,
    db: AsyncSession,
) -> Any:
    """按任务名分发到具体实现。延迟 import：避免 API 启动时拉起 pandas/akshare。"""
    code_list = [c.strip() for c in codes.split(",") if c.strip()] if codes else None

    if task == "calendar":
        from app.ingest.calendar import sync_trade_calendar
        return await sync_trade_calendar(db, start or date(date.today().year - 2, 1, 1),
                                         end or (date.today() + timedelta(days=30)))

    if task == "universe":
        from app.ingest.service import sync_stock_universe
        return await sync_stock_universe(db)

    if task == "backfill":
        from app.ingest.service import backfill_stock_history
        return await backfill_stock_history(
            codes=code_list, start=start, end=end or date.today(), resume=True
        )

    if task == "history_fast":
        # 腾讯日线并发回补（全市场、可断点续传）；旧 backfill 走 provider 链，
        # 东财断连/Baostock 挂起时基本跑不完。
        from app.ingest.bulk_backfill import backfill_prices
        return await backfill_prices(start=start, end=end, codes=code_list, db=db)

    if task == "moneyflow_history":
        # 新浪个股资金流历史（东财排行只有当前快照，无法重建历史）
        from app.ingest.bulk_backfill import backfill_moneyflow
        return await backfill_moneyflow(
            start=start, end=end, codes=code_list, db=db
        )

    if task == "valuation_fast":
        # 东财 datacenter 逐日 PE/PB/PS/市值，单只一次请求
        from app.ingest.bulk_backfill import backfill_valuation
        return await backfill_valuation(
            start=start, end=end, codes=code_list, db=db
        )

    if task == "rebuild":
        # 数据回补后重算指标与因子评分（可指定 --start/--end，默认最新可完整计算的一天）
        from app.engine.features import compute_features_for_range
        from app.engine.stock_score import compute_stock_scores_range
        from app.ingest.bulk_backfill import last_completed_trade_date

        end_date = end or trade_date or await last_completed_trade_date(db)
        start_date = start or end_date
        # 不传外层 db：让引擎按 800 行/批自行提交。三百万行级别的回补若共用
        # 一个事务，既占 WAL 又会把数据锁到任务结束才可见。
        features = await compute_features_for_range(
            start_date, end_date, codes=code_list
        )
        scores = await compute_stock_scores_range(start_date, end_date)
        return {
            "rows": int(features) + int(scores),
            "detail": {"features": int(features), "scores": int(scores),
                       "start": start_date.isoformat(), "end": end_date.isoformat()},
        }

    resolved, is_today = await resolve_trade_date(db, trade_date)
    if resolved is None:
        return {"rows": 0, "skipped": True,
                "reason": "非交易日或无可用交易日（必须先同步交易日历）"}

    if task == "snapshot":
        from app.ingest.service import collect_daily_snapshot
        return await collect_daily_snapshot(resolved, db)

    if task == "index":
        from app.ingest.service import collect_indices
        return await collect_indices(resolved, db)

    if task == "sector":
        # 板块日线统一同花顺口径（与 dim_sector_member 成分名单一致）；
        # 当日历史 K 线缺失时由采集函数内部用实时快照补齐，不再写入东财 BK 行。
        from app.ingest.service import collect_sector_daily
        return await collect_sector_daily(resolved, db)

    if task == "sector_members":
        from app.ingest.service import collect_sector_members
        return await collect_sector_members(resolved, db)

    if task == "sector_fallback":
        # 东财板块不可达时的同花顺兜底（行业/概念资金流 → 板块日线）
        from app.ingest.ths_sector import collect_sector_fallback
        return await collect_sector_fallback(resolved, db)

    if task == "valuation":
        # 历史估值：新浪财报 + 已有收盘价 → PE/PB/股息率（point-in-time，带披露滞后）
        from app.ingest.valuation import backfill_valuation, collect_latest_valuation
        if start is not None:
            return await backfill_valuation(code_list, start, end or resolved)
        return await collect_latest_valuation(resolved, db=db)

    if task == "moneyflow":
        if start is not None:
            # 指定区间即重建历史资金流（东财排行只有当日快照）
            from app.ingest.bulk_backfill import backfill_moneyflow
            return await backfill_moneyflow(start=start, end=end, codes=code_list, db=db)
        from app.ingest.service import collect_moneyflow
        return await collect_moneyflow(resolved, db)

    if task == "sector_agg":
        # 依赖已落地的板块行情、个股市值与资金流；只更新当日已有板块行
        from app.engine.sector_agg import compute_and_save_sector_metrics
        return await compute_and_save_sector_metrics(resolved, db=db)

    if task == "margin":
        from app.ingest.service import collect_margin
        return await collect_margin(resolved, db)

    if task == "features":
        from app.engine.features import compute_features_for_date, compute_features_for_range
        if start is not None:
            # 区间回补不共用外层事务：引擎按批自提交，避免几百万行压在一个事务里
            return {"rows": await compute_features_for_range(start, end or resolved)}
        return {"rows": await compute_features_for_date(resolved, db=db)}

    if task == "scores":
        from app.engine.stock_score import compute_stock_scores, compute_stock_scores_range
        if start is not None:
            return {"rows": await compute_stock_scores_range(start, end or resolved)}
        return {"rows": await compute_stock_scores(resolved, db=db)}

    if task == "sector_scores":
        from app.engine.sector_analysis import save_sector_scores, score_all_sectors
        rows = await score_all_sectors(resolved, db=db)
        return {"rows": await save_sector_scores(resolved, rows, db=db)}

    if task == "temperature":
        # 依赖当日 features（MA20）与日线，必须排在 features/scores 之后
        from app.engine.market_temperature import compute_and_save_temperature
        return await compute_and_save_temperature(resolved, db=db)

    if task == "report":
        from app.services.report_generator import generate_report
        report = await generate_report(resolved, db)
        return {"rows": 1, "chars": len(report.get("content", "")) if isinstance(report, dict) else 0}

    if task == "algos":
        from app.strategy.runner import run_strategy
        from app.strategy.strategies import SPECS
        results = {}
        for algo_code in SPECS:
            try:
                result = await run_strategy(algo_code, resolved, {}, force=False, db=db)
                results[algo_code] = result.get("count", 0)
            except Exception as exc:                  # noqa: BLE001
                logger.warning("策略 %s 失败: %s", algo_code, exc)
                results[algo_code] = f"failed: {exc}"
        return {"rows": sum(v for v in results.values() if isinstance(v, int)),
                "detail": results}

    if task == "signal_perf":
        # 事后统计：只读 stg_run/stg_signal 与行情，不参与出信号。
        # 每次重算最近 25 个交易日：覆盖新成熟样本，以及除权后 adj_factor 变化。
        from app.engine.signal_perf import recent_window_start, recompute_signal_perf
        from app.strategy.strategies import SPECS

        window_start = await recent_window_start(db, resolved, days=25)
        if window_start is None:
            return {"rows": 0, "skipped": True, "reason": "交易日历为空"}
        return await recompute_signal_perf(
            window_start, resolved, algos=list(SPECS), db=db
        )

    if task == "all":
        return await run_daily_pipeline(resolved, db)

    raise ValueError(f"未实现的任务: {task}")


# ============================================================
# 每日全链路
# ============================================================

async def run_daily_pipeline(
    trade_date: date,
    db: AsyncSession | None = None,
    steps: Iterable[str] = DEFAULT_DAILY_STEPS,
) -> dict:
    """执行完整每日流水线。每步失败不影响后续（但会记入结果）。"""
    if db is None:
        from app.db.session import AsyncSessionLocal
        async with AsyncSessionLocal() as own_db:
            return await run_daily_pipeline(trade_date, own_db, steps)

    summary: dict[str, Any] = {}
    with bind_run(trade_date=trade_date.isoformat()):
        logger.info("每日流水线开始 steps=%s", list(steps))
        for step in steps:
            if step == "algos":
                # 选股必须在评分与日报之后
                pass
            try:
                summary[step] = await run_task(step, trade_date=trade_date, db=db)
            except Exception as exc:                  # noqa: BLE001
                logger.exception("步骤 %s 失败", step)
                summary[step] = {"status": TaskStatus.FAILED.value, "error": str(exc)}
        from app.quality.invariants import log_invariants
        await log_invariants(logger)
        logger.info("每日流水线结束")
    return {"trade_date": trade_date.isoformat(), "steps": summary}


async def run_task_background(
    task: str,
    trade_date: date | None = None,
    start: date | None = None,
    end: date | None = None,
    codes: str | None = None,
) -> None:
    """后台任务入口：自建会话，异常只记日志（BackgroundTasks 无人接收返回值）。"""
    from app.db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        try:
            await run_task(task, trade_date=trade_date, start=start, end=end,
                           codes=codes, db=db)
        except Exception:                             # noqa: BLE001
            logger.exception("后台任务 %s 失败", task)


# ============================================================
# 数据库锁（方案 A8：DB 分布式锁）
# ============================================================

async def try_advisory_lock(db: AsyncSession, key: str) -> bool:
    """尝试获取 PG advisory lock（会话级）。调用方必须在结束时 release。"""
    result = await db.execute(text("SELECT pg_try_advisory_lock(hashtext(:k))"), {"k": key})
    return bool(result.scalar())


async def advisory_unlock(db: AsyncSession, key: str) -> None:
    await db.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": key})


__all__ = [
    "run_task", "run_task_background", "run_daily_pipeline", "resolve_trade_date",
    "mark_success", "mark_failure", "try_advisory_lock", "advisory_unlock",
    "DEFAULT_DAILY_STEPS", "TASK_DATASET",
]
