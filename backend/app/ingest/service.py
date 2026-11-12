"""
采集编排（方案 A8）
====================
本模块是采集层对外的**唯一入口**：admin API（``app/api/v1/admin.py``）、
每日流水线（``app/pipeline.py``）、命令行（``scripts/ingest_cli.py``）都只调这里。

核心函数签名固定（API / worker / CLI 依赖）::

    sync_stock_universe(db=None)                                      A1 股票池
    collect_daily_snapshot(trade_date, db=None, force=False)          A2 全市场快照
    backfill_stock_history(codes=None, start=None, end=None,
                           resume=True, job_id=None)                  A3 历史日线
    collect_sector_daily(trade_date, db=None)                         A4 板块日线
    collect_sector_members(trade_date, db=None)                       A4 板块成分 SCD2
    collect_moneyflow(trade_date, db=None)                            A5 资金流
    collect_indices(trade_date, db=None, include_us=False)            A6 指数日线
    collect_margin(trade_date, db=None)                               A5 两融/北向
    run_daily_ingest(trade_date, db=None, include=(...))              A8 每日编排

每个函数统一具备（方案 A8/A9 硬要求）：

1. **幂等 upsert**：``INSERT ... ON CONFLICT DO UPDATE``，重跑不产生重复行；
   写 ``fact_stock_daily`` 前调 ``db.views.ensure_partition_for`` 保证分区存在；
2. **DB 分布式锁**：``pg_try_advisory_lock(hashtext(task))``，拿不到锁直接返回
   ``detail["skipped"]="lock_busy"``（多进程/多 worker 不会重复抓取）；
3. **限频**：全市场快照走 ``SNAPSHOT_LIMITER``（60s/次），历史回补走
   ``HISTORY_LIMITER``（2 请求/秒），合规边界见方案 5.1；
4. **非交易日短路**：用 ``calendar.is_trade_day`` 判断，节假日不跑空、不写脏数据；
5. **失败不清空已有数据**：质量不合格/数据源失败只写 ``meta_data_quality.status=error``
   与 ``ingest_job.status=failed``，绝不 DELETE 旧数据；
6. **断点续传**：历史回补把已完成代码写入 ``ingest_job.checkpoint``，重跑自动跳过；
7. **单位统一**：金额 → 元、成交量 → 股（在 Provider 归一化阶段完成，这里只搬运）；
8. **任务可观测**：每步写 ``ingest_job``（total/done/failed/rows_written/elapsed_ms）。

事务约定：``db=None`` 时函数自建会话并提交；传入 ``db`` 时事务归调用方
（函数内只执行语句，不主动 commit），方便 API 把同一请求里的多次写入放在一个事务。
"""
from __future__ import annotations

import hashlib
import math
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any, AsyncIterator, Iterable, Sequence

import numpy as np
import pandas as pd
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.core.constants import (
    INDEX_MAP,
    STATUS_DELISTED,
    STATUS_NORMAL,
    board_of,
    exchange_of,
    is_st_name,
    is_stock_code,
    limit_prices,
)
from app.core.errors import (
    AppError,
    DataUnavailableError,
    ProviderError,
    QualityBlockedError,
)
from app.core.logging import get_logger
from app.core.soe import classify_ownership
from app.core.status import QualityStatus
from app.db.models.dim import DimSectorMember, DimStock
from app.db.models.fact import (
    FactMoneyflow,
    FactSectorDaily,
    FactStockDaily,
    MacroRate,
    MarginTrading,
    MarketIndex,
)
from app.db.models.meta import IngestJob
from app.db.session import engine, session_scope
from app.db.views import ensure_partition_for
from app.ingest import calendar as cal
from app.ingest.akshare_provider import AkshareProvider
from app.ingest.baostock_provider import BaostockProvider
from app.ingest.sina_provider import SinaProvider
from app.ingest.base import (
    HISTORY_LIMITER,
    SNAPSHOT_LIMITER,
    FetchResult,
    ProviderChain,
    upsert_rows,
)
from app.ingest.quality import (
    QualityReport,
    check_daily_frame,
    check_snapshot,
    check_universe,
    make_error_report,
    record_quality,
)

logger = get_logger(__name__)

# 板块日线最低行数阈值（沪 86 个行业 + 数百概念，低于 50 说明源异常）
_MIN_SECTOR_ROWS = 50
# 历史回补每写库/写检查点的批次大小
_BACKFILL_BATCH = 20
_BACKFILL_CHECKPOINT_EVERY = 50
# 板块成分逐个板块抓取：东财整体不可用时连续失败即熔断，避免 480 个板块 × 重试
# 把日更拖住一小时（2026-11-03 实测 58 分钟，进而拖死共享连接）。
# 熔断/超时跳过的板块保持已有成分区间不变，宁可当天不更新也不误关区间。
_SECTOR_MEMBERS_MAX_CONSECUTIVE_FAILURES = 5
_SECTOR_MEMBERS_BUDGET_SEC = 600.0
# "消失板块"关闭门槛：当日板块清单必须覆盖已有成分板块的 98%，
# 否则视为清单不完整（sector_daily 部分成功仍可能过质量门），
# 跳过关闭并把本次成分更新标为不合格，防止一次残缺清单清空大量仍存在的板块。
_SECTOR_LIST_MIN_COVERAGE = 0.98
_SECTOR_LIST_MIN_SECTORS = 20


# ============================================================
# Provider 降级链
# ============================================================
_PROVIDER_REGISTRY: dict[str, type] = {
    "akshare": AkshareProvider,
    "baostock": BaostockProvider,
}

_CHAIN: ProviderChain | None = None


def build_provider_chain() -> ProviderChain:
    """按配置构建降级链：``DATA_PRIMARY_PROVIDER → DATA_FALLBACK_PROVIDER``。"""
    providers = []
    for key in (config.DATA_PRIMARY_PROVIDER, config.DATA_FALLBACK_PROVIDER):
        name = (key or "").strip().lower()
        provider_class = _PROVIDER_REGISTRY.get(name)
        if provider_class is None:
            if name:
                logger.warning("未知数据源配置：%s（忽略）", name)
            continue
        providers.append(provider_class())
    if not providers:
        providers = [AkshareProvider(), BaostockProvider()]
    # 新浪只兜底概念板块成分（精确同名），其余能力抛 NotImplementedError 由链跳过
    if not any(getattr(p, "name", "") == "sina" for p in providers):
        providers.append(SinaProvider())
    return ProviderChain(providers, name="gfna")


def get_provider_chain() -> ProviderChain:
    """获取进程级单例降级链（测试/CLI 可用 :func:`use_provider_chain` 替换）。"""
    global _CHAIN
    if _CHAIN is None:
        _CHAIN = build_provider_chain()
    return _CHAIN


def use_provider_chain(chain: ProviderChain | None) -> None:
    """注入自定义降级链（离线测试用 FakeProvider，或 CLI 指定数据源）。"""
    global _CHAIN
    _CHAIN = chain


# ============================================================
# 通用小工具
# ============================================================
def _as_date(value: Any) -> date:
    """把 date/datetime/str/Timestamp 统一成 ``datetime.date``。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, pd.Timestamp):
        return value.date()
    text_value = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text_value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"无法解析日期: {value!r}")


def _today_cn() -> date:
    """A 股"今天"（Asia/Shanghai）。"""
    return _now_cn().date()


def _now_cn() -> datetime:
    """A 股当前时刻（Asia/Shanghai，带时区）。"""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(config.TIMEZONE))
    except Exception:  # pragma: no cover
        return datetime.now(timezone.utc) + timedelta(hours=8)


def _after_close_ready() -> bool:
    """当前是否已过"收盘数据可用"时刻（``SNAPSHOT_READY_HOUR:MINUTE``，默认 15:30）。

    为什么需要：盘中抓 spot 拿到的是**未完成交易日**的数据，当成收盘日线落库
    会让当日涨跌幅/成交额/市值全部失真。这是数据正确性红线。
    """
    now = _now_cn()
    return (now.hour, now.minute) >= (
        int(config.SNAPSHOT_READY_HOUR),
        int(config.SNAPSHOT_READY_MINUTE),
    )


async def _latest_trade_date(db: AsyncSession | None, on_or_before: date) -> date | None:
    async with _db_scope(db) as session:
        return await cal.latest_trade_date(session, on_or_before)


async def _snapshot_readiness(trade_date: date, db: AsyncSession | None) -> str | None:
    """当日快照类数据（A2/A5 资金流）的前置检查，返回跳过原因或 None。

    这两类数据源只提供"当前快照"（东财 spot / 资金流排行），无法重建历史某天的
    全市场截面，因此只允许：目标日 == 最近一个交易日，且（若就是今天）已到收盘后。
    """
    today = _today_cn()
    latest = await _latest_trade_date(db, today)
    if latest is None:
        return "no_trade_calendar"
    if trade_date != latest:
        return "not_latest_trade_date"
    if trade_date == today and not _after_close_ready():
        return "before_close_ready_time"
    return None


def _clean(value: Any) -> Any:
    """NaN / NaT / numpy 标量 → Python 原生类型或 None。

    为什么必须做：pandas ``to_dict("records")`` 会把空值变成 ``float('nan')``，
    直接写库会在 NUMERIC 列里落 NaN（后续 SQL 比较全部失效），也会让 asyncpg
    对 numpy 类型报 "unsupported type"。
    """
    if value is None:
        return None
    if isinstance(value, (str, bytes)):
        return value
    try:
        if value is pd.NaT or value is pd.NA:
            return None
        missing = pd.isna(value)
        if isinstance(missing, bool) and missing:
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, np.floating):
        number = float(value)
        return None if math.isnan(number) or math.isinf(number) else number
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    return value


def _records(frame: Any) -> list[dict[str, Any]]:
    """DataFrame → 清洗后的 dict 列表（空表返回 []）。"""
    if frame is None:
        return []
    try:
        if len(frame) == 0:
            return []
    except TypeError:  # pragma: no cover
        return []
    return [{key: _clean(value) for key, value in record.items()}
            for record in frame.to_dict("records")]


def _skipped_result(task: str, reason: str, **extra: Any) -> FetchResult:
    """构造"跳过"结果（非交易日 / 已有数据 / 限频 / 锁被占用）。"""
    logger.warning("采集任务 %s 跳过：%s", task, reason)
    return FetchResult(
        rows=0,
        total=0,
        elapsed_ms=0,
        detail={"skipped": reason, "task": task, **extra},
    )


def _result_summary(result: FetchResult, *, step_ms: int | None = None) -> dict[str, Any]:
    """FetchResult → 可 JSON 序列化的摘要（用于 run_daily_ingest 的返回值/日志）。"""
    if result.is_skipped:
        status = "skipped"
    elif result.rows == 0 and result.failed == 0 and result.detail.get("no_change"):
        # 幂等重跑：SCD2 成分没有变化，属于成功而不是"空结果"
        status = "ok"
    elif result.failed and result.rows:
        status = "partial"
    elif result.failed and not result.rows:
        status = "failed"
    else:
        status = "ok" if result.rows else "empty"
    payload: dict[str, Any] = {
        "status": status,
        "rows": result.rows,
        "total": result.total,
        "failed": result.failed,
        "source": result.source,
        "elapsed_ms": step_ms if step_ms is not None else result.elapsed_ms,
    }
    if result.is_skipped:
        payload["skipped"] = result.detail.get("skipped")
    extra_detail = result.summary().get("detail") or {}
    if extra_detail:
        payload["detail"] = extra_detail
    return payload


def _db_backed_count(value: Any) -> int:  # pragma: no cover —— 兼容老 SQLAlchemy 返回
    return int(value or 0)


@asynccontextmanager
async def _db_scope(db: AsyncSession | None) -> AsyncIterator[AsyncSession]:
    """会话作用域：未传入 db 时自建会话并自动提交；传入时沿用调用方事务。"""
    if db is not None:
        yield db
        return
    async with session_scope() as session:
        yield session


# ============================================================
# DB 分布式锁（方案 A8）
# ============================================================
@asynccontextmanager
async def _advisory_lock(task: str) -> AsyncIterator[bool]:
    """PG 会话级 advisory lock，拿不到锁直接让调用方返回 skipped。

    实现要点：
        - 用**独立连接**持有锁（与业务会话分开），避免业务会话 commit 后连接归还
          连接池导致"锁悬浮在池里"的经典事故；
        - 锁名 ``gfna:ingest:<task>``，不同任务互不阻塞；
        - finally 里显式 unlock，连接关闭也会兜底释放。
    """
    key = f"gfna:ingest:{task}"
    async with engine.connect() as conn:
        acquired = bool(
            (
                await conn.execute(
                    text("SELECT pg_try_advisory_lock(hashtext(:key)::bigint)"),
                    {"key": key},
                )
            ).scalar()
        )
        logger.debug("advisory lock %s acquired=%s", key, acquired)
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    await conn.execute(
                        text("SELECT pg_advisory_unlock(hashtext(:key)::bigint)"),
                        {"key": key},
                    )
                    await conn.commit()
                except Exception:  # noqa: BLE001 —— 连接断开时 PG 会自动释放会话锁
                    logger.warning("释放 advisory lock 失败（连接可能已断开）：%s", key)


# ============================================================
# 任务记录（ingest_job，断点续传的载体）
# ============================================================
class _JobTracker:
    """写 ``ingest_job`` 的小工具（独立会话，保证 checkpoint 及时落库）。

    - ``begin`` 建/重置任务行（status=running）；
    - ``update`` 写进度与 checkpoint（断点续传读这里）；
    - ``finish`` 写终态与耗时。
    update/finish 失败只记日志，不让"监控写失败"拖垮采集本身。
    """

    def __init__(
        self,
        task: str,
        dataset: str,
        *,
        trade_date: date | None = None,
        params: dict[str, Any] | None = None,
        job_id: str | None = None,
    ) -> None:
        self.task = task
        self.dataset = dataset
        self.trade_date = trade_date
        self.params = params or {}
        self.job_id = job_id or str(uuid.uuid4())
        self.started_at = datetime.now(timezone.utc)
        self._started_perf = time.perf_counter()

    async def begin(self, total: int | None = None) -> None:
        row = {
            "job_id": self.job_id,
            "task": self.task,
            "dataset": self.dataset,
            "trade_date": self.trade_date,
            "params": self.params,
            "status": "running",
            "total": total,
            "done": 0,
            "failed": 0,
            "rows_written": 0,
            "started_at": self.started_at,
            "finished_at": None,
            "elapsed_ms": None,
            "error": None,
            "checkpoint": None,
        }
        async with session_scope() as session:
            await upsert_rows(
                session,
                IngestJob,
                [row],
                ["job_id"],
                update_columns=[
                    "task", "dataset", "trade_date", "params", "status", "total",
                    "done", "failed", "rows_written", "started_at", "finished_at",
                    "elapsed_ms", "error", "checkpoint",
                ],
                update_timestamp=False,
            )
        logger.info("采集任务开始 job=%s task=%s total=%s", self.job_id, self.task, total)

    async def update(
        self,
        *,
        total: int | None = None,
        done: int | None = None,
        failed: int | None = None,
        rows_written: int | None = None,
        checkpoint: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        fields: dict[str, Any] = {}
        if total is not None:
            fields["total"] = total
        if done is not None:
            fields["done"] = done
        if failed is not None:
            fields["failed"] = failed
        if rows_written is not None:
            fields["rows_written"] = rows_written
        if checkpoint is not None:
            fields["checkpoint"] = _json_safe(checkpoint)
        if error is not None:
            fields["error"] = error[:2000]
        if not fields:
            return
        try:
            async with session_scope() as session:
                await session.execute(
                    update(IngestJob)
                    .where(IngestJob.job_id == self.job_id)
                    .values(**fields)
                )
        except Exception:  # noqa: BLE001 —— 监控写入失败不影响采集
            logger.exception("更新 ingest_job 进度失败 job=%s", self.job_id)

    async def finish(
        self,
        status: str,
        *,
        error: str | None = None,
        done: int | None = None,
        failed: int | None = None,
        rows_written: int | None = None,
        checkpoint: dict[str, Any] | None = None,
    ) -> None:
        elapsed_ms = int((time.perf_counter() - self._started_perf) * 1000)
        fields: dict[str, Any] = {
            "status": status,
            "finished_at": datetime.now(timezone.utc),
            "elapsed_ms": elapsed_ms,
        }
        if error is not None:
            fields["error"] = error[:2000]
        if done is not None:
            fields["done"] = done
        if failed is not None:
            fields["failed"] = failed
        if rows_written is not None:
            fields["rows_written"] = rows_written
        if checkpoint is not None:
            fields["checkpoint"] = _json_safe(checkpoint)
        try:
            async with session_scope() as session:
                await session.execute(
                    update(IngestJob)
                    .where(IngestJob.job_id == self.job_id)
                    .values(**fields)
                )
        except Exception:  # noqa: BLE001
            logger.exception("写入 ingest_job 终态失败 job=%s", self.job_id)
        logger.info(
            "采集任务结束 job=%s task=%s status=%s elapsed_ms=%s",
            self.job_id, self.task, status, elapsed_ms,
        )


def _json_safe(value: Any) -> Any:
    """把 checkpoint 里可能出现的 date 等类型转成 JSON 可序列化值（JSONB 列要求）。"""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


async def _skip_with_job(
    task: str,
    dataset: str,
    trade_date: date | None,
    reason: str,
    *,
    params: dict[str, Any] | None = None,
) -> FetchResult:
    """被锁/非交易日跳过时也留一条 ingest_job（任务监控页能看到"为什么没跑"）。"""
    try:
        job = _JobTracker(task, dataset, trade_date=trade_date, params=params)
        await job.begin()
        await job.finish("skipped", error=reason)
    except Exception:  # noqa: BLE001
        logger.exception("记录 skipped 任务失败 task=%s", task)
    return _skipped_result(task, reason)


async def _record_error(
    dataset: str,
    trade_date: date | None,
    error: str,
    report: QualityReport | None = None,
) -> None:
    """采集失败：写 ``meta_data_quality.status=error``（保留 error 文本），不删除旧数据。"""
    try:
        async with session_scope() as session:
            payload = report or make_error_report(dataset, error)
            await record_quality(session, dataset, payload, trade_date, QualityStatus.ERROR)
    except Exception:  # noqa: BLE001 —— 记录失败不能覆盖原始异常
        logger.exception("记录质量失败状态失败 dataset=%s", dataset)


async def _record_quality_isolated(
    dataset: str,
    report: QualityReport,
    trade_date: date | None,
    status: str,
) -> None:
    """用独立会话写质量记录（避免失败时污染调用方事务）。"""
    async with session_scope() as session:
        await record_quality(session, dataset, report, trade_date, status)


async def _load_resume_checkpoint(
    task: str, dataset: str, params: dict[str, Any]
) -> dict[str, Any]:
    """读取最近一次同 task 的 checkpoint（仅当参数完全一致时才可复用）。"""
    try:
        async with session_scope() as session:
            stmt = (
                select(IngestJob)
                .where(IngestJob.task == task, IngestJob.dataset == dataset)
                .order_by(IngestJob.started_at.desc())
                .limit(1)
            )
            job = (await session.execute(stmt)).scalars().first()
            if job is None or not job.checkpoint:
                return {}
            checkpoint = job.checkpoint
            if isinstance(checkpoint, dict) and checkpoint.get("params") == params:
                return checkpoint
    except Exception:  # noqa: BLE001
        logger.exception("读取断点失败 task=%s", task)
    return {}


# ============================================================
# 读取辅助
# ============================================================
async def _scalar(db: AsyncSession | None, statement: Any) -> Any:
    async with _db_scope(db) as session:
        return (await session.execute(statement)).scalar_one_or_none()


async def _active_stock_count(db: AsyncSession | None) -> int:
    """库内正常状态标的数（质量覆盖率的分母参考）。"""
    try:
        value = await _scalar(
            db,
            select(func.count()).select_from(DimStock).where(
                DimStock.status == STATUS_NORMAL
            ),
        )
        return _db_backed_count(value)
    except Exception:  # noqa: BLE001 —— 空库/表未建时按 0 处理
        return 0


async def _fact_rows_on(db: AsyncSession | None, trade_date: date) -> int:
    try:
        value = await _scalar(
            db,
            select(func.count()).select_from(FactStockDaily).where(
                FactStockDaily.trade_date == trade_date
            ),
        )
        return _db_backed_count(value)
    except Exception:  # noqa: BLE001
        return 0


async def _is_trade_day(db: AsyncSession | None, day: date) -> bool:
    try:
        async with _db_scope(db) as session:
            return await cal.is_trade_day(session, day)
    except Exception:  # noqa: BLE001 —— 日历不可用时保守返回 False，宁可不采集
        logger.exception("交易日判断失败 date=%s", day)
        return False


async def _ensure_partition(day: date) -> None:
    """保证 ``fact_stock_daily`` 该年分区存在（独立连接执行 DDL 并提交）。

    为什么用独立连接：DDL 失败会让当前事务进入 aborted 状态，后续写入全挂；
    独立连接失败只影响这一条 DDL，业务事务仍然可用。
    """
    async with engine.connect() as conn:
        await conn.run_sync(lambda sync_conn: ensure_partition_for(sync_conn, day))
        await conn.commit()


async def _ensure_partitions_for_dates(dates: Iterable[date]) -> None:
    years = sorted({day.year for day in dates})
    for year in years:
        await _ensure_partition(date(year, 1, 1))


# ============================================================
# 行构造（DataFrame → ORM 列字典）
# ============================================================
def _limit_flags(
    code: str, pre_close: Any, close: Any, name: str | None = None
) -> tuple[bool, bool]:
    """按 A 股涨跌停规则判断当日是否涨停/跌停（回测"不可成交"依据）。"""
    pre_close_value = _clean(pre_close)
    close_value = _clean(close)
    if pre_close_value is None or close_value is None:
        return False, False
    try:
        up, down = limit_prices(float(pre_close_value), code, is_st_name(name))
    except (TypeError, ValueError):  # pragma: no cover
        return False, False
    if up is None or down is None:
        return False, False
    tolerance = 0.005  # 半分钱容差，规避浮点误差
    return (
        float(close_value) >= float(up) - tolerance,
        float(close_value) <= float(down) + tolerance,
    )


def _non_stock_active_codes(existing: dict[str, dict[str, Any]]) -> list[str]:
    """存量里被错误标成活跃的非 A 股标的（定转/B 股），同步时一并停用。"""
    return [
        code for code, item in existing.items()
        if item.get("status") == STATUS_NORMAL and not is_stock_code(code)
    ]


def _build_universe_rows(
    frame: pd.DataFrame | None,
    existing: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """股票池 DataFrame → ``dim_stock`` 行。

    规则：
        - 退市清单里的代码：``delist_date`` 非空、``status=9``（**必须保留**，否则回测有幸存者偏差）；
        - ``list_date`` / ``industry`` / ``ownership`` 以"新数据优先、旧数据兜底"合并；
        - 股本优先用交易所名单口径（深市 A股总股本/A股流通股本），缺失时用
          总市值/收盘价 反推（AKShare 东财股票池不直接给股本），仅作近似。
    """
    rows: list[dict[str, Any]] = []
    skipped_non_stock: list[str] = []
    for record in _records(frame):
        code = str(record.get("code") or "").zfill(6)
        if not code or not code.isdigit() or len(code) != 6:
            continue
        if not is_stock_code(code):
            skipped_non_stock.append(code)
            continue
        old = existing.get(code, {})
        name = record.get("name") or old.get("name") or code
        delist_date = _as_date_optional(record.get("delist_date")) or _as_date_optional(
            old.get("delist_date")
        )
        list_date = _as_date_optional(record.get("list_date")) or _as_date_optional(
            old.get("list_date")
        )
        close = record.get("close")
        market_cap = record.get("market_cap")
        float_cap = record.get("float_cap")
        # 交易所名单给的股本最准；没有再按 市值/收盘价 反推
        total_share = record.get("total_share")
        if total_share is None:
            total_share = (
                round(float(market_cap) / float(close), 4)
                if market_cap and close and float(close) > 0
                else old.get("total_share")
            )
        float_share = record.get("float_share")
        if float_share is None:
            float_share = (
                round(float(float_cap) / float(close), 4)
                if float_cap and close and float(close) > 0
                else old.get("float_share")
            )
        rows.append(
            {
                "code": code,
                "exchange": exchange_of(code),
                "board": board_of(code),
                "name": str(name).strip()[:64],
                "industry_l1": record.get("industry_l1") or old.get("industry_l1"),
                "industry_l2": old.get("industry_l2"),
                # 每次同步都重新分类：旧逻辑只在写入时沿用 old 值，导致全市场
                # 5000+ 只里只有 20 多只有所有制，A3 的候选池被人为掐死。
                "ownership": classify_ownership(code, str(name)),
                "list_date": list_date,
                "delist_date": delist_date,
                "is_st": is_st_name(name),
                "status": STATUS_DELISTED if delist_date else STATUS_NORMAL,
                "total_share": total_share,
                "float_share": float_share,
            }
        )
    if skipped_non_stock:
        # 北交所定向可转债（810xxx）与 B 股不是 A 股股票，不能进股票池
        logger.info(
            "股票池剔除非 A 股标的 %d 个：%s",
            len(skipped_non_stock), ",".join(sorted(set(skipped_non_stock))[:10]),
        )
    return rows


def _as_date_optional(value: Any) -> date | None:
    if value is None:
        return None
    try:
        return _as_date(value)
    except (TypeError, ValueError):
        return None


def _snapshot_rows(frame: pd.DataFrame | None, trade_date: date) -> list[dict[str, Any]]:
    """全市场快照 → ``fact_stock_daily`` 行（A2）。

    - ``adj_factor=1.0``：快照对应"最新交易日"，后复权因子在该日恒为 1；
    - 停牌判定：成交量缺失/为 0；收盘价缺失（停牌/退市整理）的行直接跳过；
    - 涨跌停：用前收盘 + 板块涨幅规则精确计算，不能只看 pct_chg 阈值。
    """
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in _records(frame):
        code = str(record.get("code") or "").zfill(6)
        if not code.isdigit() or len(code) != 6 or code in seen:
            continue
        seen.add(code)
        close = record.get("close")
        pre_close = record.get("pre_close")
        volume = record.get("volume")
        # 停牌/退市整理标的没有当日行情（东财榜单仍会列出），
        # 与历史回补口径一致：不落库，否则当日行情会多出大量空值行。
        if close is None or float(close) <= 0:
            continue
        is_limit_up, is_limit_down = _limit_flags(
            code, pre_close, close, record.get("name")
        )
        suspended = volume is None or float(volume) <= 0
        rows.append(
            {
                "code": code,
                "trade_date": trade_date,
                "open": record.get("open"),
                "high": record.get("high"),
                "low": record.get("low"),
                "close": close,
                "pre_close": pre_close,
                "pct_chg": record.get("pct_chg"),
                "amplitude": record.get("amplitude"),
                "volume": int(volume) if volume is not None else None,
                "amount": record.get("amount"),
                "turnover_rate": record.get("turnover_rate"),
                "adj_factor": 1.0,
                "market_cap": record.get("market_cap"),
                "float_cap": record.get("float_cap"),
                "pe_ttm": record.get("pe_ttm"),
                "pb": record.get("pb"),
                "ps_ttm": record.get("ps_ttm"),
                "dv_ttm": record.get("dv_ttm"),
                "is_limit_up": is_limit_up,
                "is_limit_down": is_limit_down,
                "is_suspended": suspended,
                "src": "akshare",
            }
        )
    return rows


def _history_rows(
    code: str, frame: pd.DataFrame | None, source: str
) -> list[dict[str, Any]]:
    """单只股票历史日线 → ``fact_stock_daily`` 行（A3）。

    估值字段（market_cap/pe/pb）AKShare 历史日线接口不提供，保持 NULL；
    当日快照会补上最新交易日的估值。
    """
    rows: list[dict[str, Any]] = []
    for record in _records(frame):
        trade_date = _as_date_optional(record.get("trade_date"))
        if trade_date is None:
            continue
        close = record.get("close")
        if close is None or float(close) <= 0:
            continue
        volume = record.get("volume")
        is_limit_up, is_limit_down = _limit_flags(code, record.get("pre_close"), close)
        rows.append(
            {
                "code": code,
                "trade_date": trade_date,
                "open": record.get("open"),
                "high": record.get("high"),
                "low": record.get("low"),
                "close": close,
                "pre_close": record.get("pre_close"),
                "pct_chg": record.get("pct_chg"),
                "amplitude": record.get("amplitude"),
                "volume": int(volume) if volume is not None else None,
                "amount": record.get("amount"),
                "turnover_rate": record.get("turnover_rate"),
                "adj_factor": record.get("adj_factor"),
                "is_limit_up": is_limit_up,
                "is_limit_down": is_limit_down,
                "is_suspended": volume is None or float(volume) <= 0,
                "src": source[:16],
            }
        )
    return rows


def _index_rows(frame: pd.DataFrame | None, trade_date: date) -> list[dict[str, Any]]:
    """指数日线 DataFrame → ``market_indices`` 行。"""
    rows: list[dict[str, Any]] = []
    for record in _records(frame):
        index_code = record.get("index_code")
        if not index_code:
            continue
        index_name = record.get("index_name")
        if not index_name:
            index_name = INDEX_MAP.get(str(index_code), (str(index_code), True))[0]
        rows.append(
            {
                "index_code": str(index_code),
                "trade_date": trade_date,
                "index_name": str(index_name)[:50],
                "open": record.get("open"),
                "high": record.get("high"),
                "low": record.get("low"),
                "close": record.get("close"),
                "pre_close": record.get("pre_close"),
                "pct_chg": record.get("pct_chg"),
                "volume": int(record["volume"]) if record.get("volume") is not None else None,
                "amount": record.get("amount"),
                "src": "akshare",
            }
        )
    return rows


def _moneyflow_rows(
    frame: pd.DataFrame | None, trade_date: date
) -> list[dict[str, Any]]:
    """个股资金流 DataFrame → ``fact_moneyflow`` 行。

    当前只落 T0（今日）与 5 日合计；T1–T4 留给后续扩展（源暂不提供）。
    """
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in _records(frame):
        code = str(record.get("code") or "").zfill(6)
        if not code.isdigit() or len(code) != 6 or code in seen:
            continue
        # 资金流排行会混入 810xxx 定向可转债、B 股，进入 fact_moneyflow 会污染聚合
        if not is_stock_code(code):
            continue
        seen.add(code)
        rows.append(
            {
                "code": code,
                "trade_date": trade_date,
                "main_net_t0": record.get("main_net_t0"),
                "main_net_t1": None,
                "main_net_t2": None,
                "main_net_t3": None,
                "main_net_t4": None,
                "main_net_5d": record.get("main_net_5d"),
                "north_hold_ratio": None,
                "src": "akshare",
            }
        )
    return rows


def _margin_row(
    frame: pd.DataFrame | None,
    trade_date: date,
    previous_balance: float | None,
) -> dict[str, Any] | None:
    """两融 DataFrame → ``margin_trading`` 行（``margin_change`` 需要前值）。

    注意：Provider 已按"两融 T+1 披露"把行日期换成实际已披露日（可能早于请求日），
    所以这里**优先用数据行自带的 trade_date**，它才是这条数据的真实归属日。
    """
    records = _records(frame)
    if not records:
        return None
    record = records[0]
    actual_date = _as_date_optional(record.get("trade_date")) or trade_date
    margin_balance = record.get("margin_balance")
    margin_change = None
    if margin_balance is not None and previous_balance is not None:
        margin_change = round(float(margin_balance) - float(previous_balance), 4)
    return {
        "trade_date": actual_date,
        "margin_balance": margin_balance,
        "short_balance": record.get("short_balance"),
        "margin_change": margin_change,
        "northward_net": record.get("northward_net"),
        "src": "akshare",
    }


# ============================================================
# A1 股票池同步
# ============================================================
async def sync_stock_universe(db: AsyncSession | None = None) -> FetchResult:
    """A1 股票池同步（全市场标的 + 退市 + ST + 行业归属 + 市值/股本）。

    Args:
        db: 可选会话；None 时自建并提交。

    Returns:
        ``FetchResult``（rows=写入 dim_stock 行数）。

    Raises:
        ProviderError: 数据源完全拿不到股票池。
        QualityBlockedError: 行数/覆盖率/重复代码不达标（**不写库**，保留旧股票池）。
    """
    started = time.perf_counter()
    task, dataset = "sync_universe", "stock_universe"

    async with _advisory_lock("universe") as acquired:
        if not acquired:
            return await _skip_with_job(task, dataset, None, "lock_busy")
        job = _JobTracker(task, dataset, params={})
        await job.begin()

        try:
            chain = get_provider_chain()
            fetched = await chain.fetch_stock_universe()
            frame = fetched.detail.get("data")

            existing = await _load_existing_stocks(db)
            active_before = sum(
                1 for item in existing.values() if item.get("status") == STATUS_NORMAL
            )
            report = check_universe(frame, active_before)
            if not report.ok:
                await _record_quality_isolated(
                    dataset, report, None, QualityStatus.QUALITY_FAILED
                )
                raise QualityBlockedError(
                    f"股票池质量不合格：{report.error_text()}",
                    detail=report.to_detail(),
                )

            rows = _build_universe_rows(frame, existing)
            stale_non_stock = _non_stock_active_codes(existing)
            async with _db_scope(db) as session:
                written = await upsert_rows(session, DimStock, rows, ["code"])
                if stale_non_stock:
                    await session.execute(
                        update(DimStock)
                        .where(
                            DimStock.code.in_(stale_non_stock),
                            DimStock.status == STATUS_NORMAL,
                        )
                        .values(status=STATUS_DELISTED)
                    )
                    logger.info(
                        "股票池停用存量非 A 股标的 %d 个：%s",
                        len(stale_non_stock), ",".join(stale_non_stock[:10]),
                    )
                await record_quality(
                    session, dataset, report, _today_cn(), QualityStatus.OK
                )

            elapsed = int((time.perf_counter() - started) * 1000)
            result = FetchResult(
                rows=written,
                total=len(rows),
                failed=max(0, len(rows) - written),
                elapsed_ms=elapsed,
                detail={
                    "source": fetched.source,
                    "industry_mapped": fetched.detail.get("industry_mapped"),
                    "delisted": sum(1 for row in rows if row["status"] == STATUS_DELISTED),
                    "attempts": fetched.detail.get("attempts"),
                },
            )
            await job.finish("success", done=written, rows_written=written)
            return result
        except Exception as exc:
            if not isinstance(exc, QualityBlockedError):
                await _record_error(dataset, _today_cn(), f"{type(exc).__name__}: {exc}")
            await job.finish("failed", error=f"{type(exc).__name__}: {exc}")
            raise


async def _load_existing_stocks(db: AsyncSession | None) -> dict[str, dict[str, Any]]:
    """加载现有 dim_stock 关键字段（股票池合并时保留 list_date/行业/所有制）。"""
    try:
        async with _db_scope(db) as session:
            stmt = select(
                DimStock.code,
                DimStock.name,
                DimStock.list_date,
                DimStock.delist_date,
                DimStock.industry_l1,
                DimStock.industry_l2,
                DimStock.ownership,
                DimStock.status,
                DimStock.total_share,
                DimStock.float_share,
            )
            result: dict[str, dict[str, Any]] = {}
            for row in (await session.execute(stmt)).all():
                result[row[0]] = {
                    "name": row[1],
                    "list_date": row[2],
                    "delist_date": row[3],
                    "industry_l1": row[4],
                    "industry_l2": row[5],
                    "ownership": row[6],
                    "status": row[7],
                    "total_share": float(row[8]) if row[8] is not None else None,
                    "float_share": float(row[9]) if row[9] is not None else None,
                }
            return result
    except Exception:  # noqa: BLE001 —— 表未建/空库
        logger.warning("读取现有股票池失败（按空库处理）", exc_info=True)
        return {}


# ============================================================
# A2 全市场当日快照
# ============================================================
async def collect_daily_snapshot(
    trade_date: date, db: AsyncSession | None = None, force: bool = False
) -> FetchResult:
    """A2 全市场当日快照（一次请求拿全市场，落 ``fact_stock_daily``）。

    Args:
        trade_date: 目标交易日（非交易日直接 skipped）。
        db: 可选会话。
        force: 强制重新采集。**仍然遵守 60s 最小间隔**（合规要求不可被参数绕过），
            只是"已有数据直接跳过"不再生效。

    Returns:
        ``FetchResult``；跳过时 ``detail["skipped"]`` 为
        ``non_trade_day / already_collected / rate_limited / lock_busy``。
    """
    started = time.perf_counter()
    trade_date = _as_date(trade_date)
    task, dataset = "daily_snapshot", "stock_daily"

    async with _advisory_lock("snapshot") as acquired:
        if not acquired:
            return await _skip_with_job(task, dataset, trade_date, "lock_busy")

        job = _JobTracker(task, dataset, trade_date=trade_date)
        await job.begin()

        try:
            if not await _is_trade_day(db, trade_date):
                await job.finish("skipped", error="non_trade_day")
                return _skipped_result(task, "non_trade_day", trade_date=trade_date.isoformat())

            # 当日快照只能对应"最近交易日 + 收盘后"；盘中/历史日一律拒绝（防脏数据）
            not_ready = await _snapshot_readiness(trade_date, db)
            if not_ready is not None:
                await job.finish("skipped", error=not_ready)
                return _skipped_result(
                    task, not_ready, trade_date=trade_date.isoformat()
                )

            existing = await _fact_rows_on(db, trade_date)
            active_count = await _active_stock_count(db)
            expected = max(config.QUALITY_MIN_SNAPSHOT_ROWS, active_count)
            # "已有数据直接跳过"必须确认当日**基本采全**：
            # 若当日只有历史回补写入的少量行（例如先 backfill 后 snapshot），
            # 绝不能因为 count>0 就跳过，否则这一天的全市场快照永远缺数据。
            complete_threshold = max(
                config.QUALITY_MIN_SNAPSHOT_ROWS, int(active_count * 0.9)
            )
            if existing >= complete_threshold and not force:
                await job.finish("skipped", error="already_collected",
                                 rows_written=0, done=existing)
                return _skipped_result(
                    task, "already_collected", existing=existing,
                    coverage=round(min(existing / expected, 1.0), 4)
                    if expected else None,
                    trade_date=trade_date.isoformat(),
                )

            # 限频：非 force 时非阻塞占令牌；force 时等待（合规 > 便利）
            if not force:
                if not SNAPSHOT_LIMITER.try_acquire():
                    await job.finish("skipped", error="rate_limited")
                    return _skipped_result(
                        task, "rate_limited",
                        wait_seconds=round(SNAPSHOT_LIMITER.wait_seconds(), 1),
                        trade_date=trade_date.isoformat(),
                    )
            else:
                await SNAPSHOT_LIMITER.acquire()

            chain = get_provider_chain()
            fetched = await chain.fetch_daily_snapshot(
                trade_date, timeout=config.SNAPSHOT_FETCH_TIMEOUT_SEC
            )
            frame = fetched.detail.get("data")
            report = check_snapshot(frame, expected, config)
            if not report.ok:
                await _record_quality_isolated(
                    dataset, report, trade_date, QualityStatus.QUALITY_FAILED
                )
                raise QualityBlockedError(
                    f"快照质量不合格：{report.error_text()}",
                    detail=report.to_detail(),
                )

            rows = _snapshot_rows(frame, trade_date)
            if not rows:
                raise ProviderError(
                    "快照归一化后为空（接口返回结构与预期不符）",
                    detail={"provider": fetched.source},
                )

            await _ensure_partition(trade_date)
            async with _db_scope(db) as session:
                written = await upsert_rows(
                    session, FactStockDaily, rows, ["code", "trade_date"]
                )
                await record_quality(
                    session, dataset, report, trade_date, QualityStatus.OK
                )

            elapsed = int((time.perf_counter() - started) * 1000)
            result = FetchResult(
                rows=written,
                total=expected,
                failed=0,
                elapsed_ms=elapsed,
                detail={
                    "source": fetched.source,
                    "coverage": report.coverage,
                    "anomalies": len(report.anomalies),
                    "attempts": fetched.detail.get("attempts"),
                },
            )
            await job.finish("success", done=written, rows_written=written)
            return result
        except Exception as exc:
            if not isinstance(exc, QualityBlockedError):
                await _record_error(dataset, trade_date, f"{type(exc).__name__}: {exc}")
            await job.finish("failed", error=f"{type(exc).__name__}: {exc}")
            raise


# ============================================================
# A3 历史日线回补（分批 + 限频 + 断点续传）
# ============================================================
async def backfill_stock_history(
    codes: list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
    resume: bool = True,
    job_id: str | None = None,
) -> FetchResult:
    """A3 历史日线回补（近 5 年，分批 + 限频 + 断点续传）。

    流程：
        1. 用真实交易日历算区间（质量校验"缺日"的基准）；
        2. 确定待回补代码清单（``codes`` 指定，否则取 ``dim_stock`` 全部未退市/区间内退市的标的）；
        3. ``resume=True`` 时读最近一次同 task/同参数的 checkpoint，跳过已完成代码；
        4. 逐只拉取（每只 2 个请求，2 req/s），逐只质量校验；
        5. 每 20 只批量 upsert（保证断点粒度），每 50 只刷新 ingest_job.checkpoint；
        6. 失败的代码记录在 ``detail["failed_codes"]``，不中断整个回补。

    Args:
        codes: 指定代码列表（None = 全市场）。
        start: 起始日期，默认 ``config.HISTORY_START``（2021-01-01）。
        end: 结束日期，默认今天。
        resume: 是否从断点续传。
        job_id: 指定任务 id（不传则新生成；监控页可据此追踪）。

    Returns:
        ``FetchResult``：rows=写入行数，total=标的数，failed=失败标的数。
    """
    started = time.perf_counter()
    trade_date = _as_date(end) if end is not None else _today_cn()
    start_date = _as_date(start) if start is not None else config.HISTORY_START
    if start_date > trade_date:
        start_date, trade_date = trade_date, start_date
    task, dataset = "backfill_daily", "stock_daily"

    async with _advisory_lock("backfill") as acquired:
        if not acquired:
            return await _skip_with_job(task, dataset, trade_date, "lock_busy")

        code_list = await _resolve_backfill_codes(codes, start_date)
        scope = "all" if codes is None else "codes:" + hashlib.sha1(
            ",".join(code_list).encode("utf-8")
        ).hexdigest()[:12]
        params = {
            "start": start_date.isoformat(),
            "end": trade_date.isoformat(),
            "scope": scope,
        }

        job = _JobTracker(task, dataset, trade_date=trade_date, params=params,
                          job_id=job_id)
        await job.begin(total=len(code_list))

        written = 0
        completed: set[str] = set()
        failed_codes: dict[str, str] = {}
        empty_codes: list[str] = []

        try:
            # 1. 交易日历（区间内开市日，供"缺日"质量校验）
            async with _db_scope(None) as session:
                calendar_dates = await cal.get_trade_dates(session, start_date, trade_date)
            if not calendar_dates:
                raise DataUnavailableError(
                    "交易日历为空，无法校验历史日线完整性",
                    next_action="POST /api/v1/admin/ingest/calendar",
                )

            # 2. 断点续传
            if resume:
                checkpoint = await _load_resume_checkpoint(task, dataset, params)
                completed = {
                    str(code) for code in checkpoint.get("completed_codes", []) if code
                }
                if completed:
                    logger.info("断点续传：跳过已完成 %d 只股票", len(completed))
            pending = [code for code in code_list if code not in completed]
            await job.update(total=len(code_list), done=len(completed))

            chain = get_provider_chain()
            batch_rows: list[dict[str, Any]] = []
            batch_codes: list[str] = []

            async def _flush_batch() -> int:
                nonlocal written
                if not batch_rows:
                    return 0
                await _ensure_partitions_for_dates(row["trade_date"] for row in batch_rows)
                async with session_scope() as session:
                    count = await upsert_rows(
                        session, FactStockDaily, batch_rows, ["code", "trade_date"]
                    )
                written += count
                for code in batch_codes:
                    completed.add(code)
                batch_rows.clear()
                batch_codes.clear()
                return count

            for index, code in enumerate(pending, start=1):
                try:
                    await HISTORY_LIMITER.acquire()
                    fetched = await chain.fetch_stock_history(
                        code, start_date, trade_date, timeout=config.FETCH_TIMEOUT_SEC
                    )
                    frame = fetched.detail.get("data")
                    if frame is None or len(frame) == 0:
                        empty_codes.append(code)
                        logger.warning("历史日线为空 code=%s（可能区间内未上市/已退市）", code)
                        continue
                    report = check_daily_frame(
                        frame, calendar_dates, code=code, dataset=dataset
                    )
                    if not report.ok:
                        failed_codes[code] = report.error_text() or "quality_failed"
                        logger.warning("历史日线质量不合格 code=%s：%s", code, report.error_text())
                        continue
                    rows = _history_rows(code, frame, fetched.source or "akshare")
                    if not rows:
                        empty_codes.append(code)
                        continue
                    batch_rows.extend(rows)
                    batch_codes.append(code)
                    if len(batch_codes) >= _BACKFILL_BATCH:
                        await _flush_batch()
                except Exception as exc:  # noqa: BLE001 —— 单只失败不影响整体
                    failed_codes[code] = f"{type(exc).__name__}: {exc}"
                    logger.warning("历史日线回补失败 code=%s：%s", code, exc)

                if index % _BACKFILL_CHECKPOINT_EVERY == 0 or index == len(pending):
                    await _flush_batch()
                    logger.info(
                        "历史回补进度 %d/%d（已完成 %d，失败 %d，写入 %d 行）",
                        index, len(pending), len(completed), len(failed_codes), written,
                    )
                    await job.update(
                        done=len(completed),
                        failed=len(failed_codes),
                        rows_written=written,
                        checkpoint={
                            "params": params,
                            "completed_codes": sorted(completed),
                            "failed_codes": sorted(failed_codes)[:500],
                        },
                    )

            await _flush_batch()
            elapsed = int((time.perf_counter() - started) * 1000)

            # 数据集级质量（标的覆盖率 = 成功标的 / 计划标的）
            coverage = len(completed) / len(code_list) if code_list else 0.0
            report = QualityReport(
                dataset=dataset,
                rows=len(completed),
                expected=len(code_list) or None,
                coverage=round(coverage, 4),
            )
            if failed_codes:
                report.add(
                    "stock_backfill_failed",
                    count=len(failed_codes),
                    severity="warning",
                    message=f"{len(failed_codes)} 只标的回补失败（未写库，重跑可补）",
                    samples=sorted(failed_codes)[:5],
                )
            if empty_codes:
                report.add(
                    "stock_backfill_empty",
                    count=len(empty_codes),
                    severity="warning",
                    message=f"{len(empty_codes)} 只标的区间内无数据（次新股/退市股属正常）",
                    samples=empty_codes[:5],
                )
            if code_list and coverage < config.QUALITY_MIN_COVERAGE:
                report.add(
                    "coverage_below_threshold",
                    count=len(completed),
                    severity="warning",
                    message=(
                        f"标的覆盖率 {coverage:.2%} < {config.QUALITY_MIN_COVERAGE:.0%}"
                        "（中断/失败过多，可重跑断点续传）"
                    ),
                )
            await _record_quality_isolated(dataset, report, trade_date, QualityStatus.OK)

            final_checkpoint = {
                "params": params,
                "completed_codes": sorted(completed),
                "failed_codes": sorted(failed_codes)[:500],
            }
            status = "success" if not failed_codes else "partial"
            await job.finish(
                status,
                done=len(completed),
                failed=len(failed_codes),
                rows_written=written,
                checkpoint=final_checkpoint,
            )
            return FetchResult(
                rows=written,
                total=len(code_list),
                failed=len(failed_codes),
                elapsed_ms=elapsed,
                detail={
                    "completed": len(completed),
                    "empty": len(empty_codes),
                    "failed_codes": sorted(failed_codes)[:100],
                    "start": start_date.isoformat(),
                    "end": trade_date.isoformat(),
                    "attempts": getattr(chain, "last_attempts", None),
                },
            )
        except Exception as exc:
            if not isinstance(exc, (QualityBlockedError, DataUnavailableError)):
                await _record_error(dataset, trade_date, f"{type(exc).__name__}: {exc}")
            await job.finish(
                "failed",
                error=f"{type(exc).__name__}: {exc}",
                done=len(completed),
                failed=len(failed_codes),
                rows_written=written,
                checkpoint={
                    "params": params,
                    "completed_codes": sorted(completed),
                    "failed_codes": sorted(failed_codes)[:500],
                },
            )
            raise


async def _resolve_backfill_codes(
    codes: list[str] | None, start_date: date
) -> list[str]:
    """确定回补代码清单：指定优先，否则取 dim_stock（含区间内退市股）。"""
    if codes:
        cleaned = []
        for code in codes:
            digits = "".join(ch for ch in str(code) if ch.isdigit())
            if digits:
                cleaned.append(digits.zfill(6)[-6:])
        if not cleaned:
            raise DataUnavailableError("--codes 参数为空")
        return sorted(set(cleaned))

    async with session_scope() as session:
        stmt = select(DimStock.code, DimStock.delist_date).order_by(DimStock.code)
        rows = (await session.execute(stmt)).all()
    code_list = [
        code
        for code, delist_date in rows
        if delist_date is None or delist_date >= start_date
    ]
    if not code_list:
        raise DataUnavailableError(
            "股票池为空，无法回补历史日线",
            next_action="POST /api/v1/admin/ingest/universe",
        )
    return code_list


# ============================================================
# A4 板块日线 / 板块成分（SCD2）
# ============================================================
async def _fill_sector_rows_from_live(
    rows: list[dict[str, Any]], trade_date: date
) -> int:
    """用同花顺**实时**板块快照补齐历史接口缺失的板块（仅当日可用）。

    实时快照只有指数点位/涨跌幅/领涨股，没有成交量、成交额与 OHLC —— 这些字段
    由 ``build_sector_daily_rows`` 如实置空，绝不拿其它字段冒充。返回补齐行数。
    """
    from app.ingest.ths_sector import build_sector_daily_rows, fetch_sector_fundflow

    try:
        industry_df, concept_df = await fetch_sector_fundflow()
    except Exception as exc:  # noqa: BLE001 —— 补齐失败不阻断已拿到的历史行
        logger.warning("同花顺实时板块快照补齐失败（%s）：%s", trade_date, exc)
        return 0

    live_rows = (
        build_sector_daily_rows(industry_df, {}, trade_date)
        + build_sector_daily_rows(concept_df, {}, trade_date)
    )
    seen = {str(row["sector_code"]) for row in rows}
    extra: dict[str, dict[str, Any]] = {}
    for row in live_rows:
        code = str(row["sector_code"])
        if code not in seen:
            extra.setdefault(code, row)
    rows.extend(extra.values())
    return len(extra)


async def collect_sector_daily(
    trade_date: date, db: AsyncSession | None = None
) -> FetchResult:
    """A4 板块日线（行业 + 概念），统一同花顺口径（``THS_<名称>``）。

    与 ``dim_sector_member`` 成分名单同一口径，东财 BK 代码无法与 THS 成分聚合，
    因此不再写入 BK 行。当日历史 K 线尚未发布时，用同花顺实时快照补齐缺失板块
    （成交量/成交额等该源没有的字段如实置空）。
    """
    started = time.perf_counter()
    trade_date = _as_date(trade_date)
    task, dataset = "sector_daily", "sector_daily"

    async with _advisory_lock("sector_daily") as acquired:
        if not acquired:
            return await _skip_with_job(task, dataset, trade_date, "lock_busy")
        job = _JobTracker(task, dataset, trade_date=trade_date)
        await job.begin()

        try:
            if not await _is_trade_day(db, trade_date):
                await job.finish("skipped", error="non_trade_day")
                return _skipped_result(task, "non_trade_day", trade_date=trade_date.isoformat())

            # 当日板块快照同样需要"收盘后"；历史日期走板块历史接口，不在此限制
            today = _today_cn()
            if trade_date > today:
                await job.finish("skipped", error="future_date")
                return _skipped_result(task, "future_date", trade_date=trade_date.isoformat())
            if trade_date == today and not _after_close_ready():
                await job.finish("skipped", error="before_close_ready_time")
                return _skipped_result(
                    task, "before_close_ready_time", trade_date=trade_date.isoformat()
                )

            from app.ingest.ths_board import fetch_daily_rows

            previous_day = await _scalar(
                db,
                select(func.max(FactSectorDaily.trade_date))
                .where(FactSectorDaily.trade_date < trade_date),
            )
            previous_rows = 0
            if previous_day is not None:
                previous_rows = await _scalar(
                    db,
                    select(func.count())
                    .select_from(FactSectorDaily)
                    .where(FactSectorDaily.trade_date == previous_day),
                )
            expected = max(_db_backed_count(previous_rows), _MIN_SECTOR_ROWS)

            rows, ths_detail = await fetch_daily_rows(trade_date)
            source = str(ths_detail.get("source") or "ths_hist")
            failed_boards = int(ths_detail.get("failed") or 0)
            live_filled = 0
            if trade_date == today and len(rows) < expected:
                live_filled = await _fill_sector_rows_from_live(rows, trade_date)
                if live_filled:
                    source = f"{source}+live"

            report = QualityReport(
                dataset=dataset,
                rows=len(rows),
                expected=expected,
                coverage=round(min(len(rows) / expected, 1.0), 4) if expected else 0.0,
            )
            if not rows:
                report.add("empty_frame", severity="error", message="板块日线为空")
            elif len(rows) < _MIN_SECTOR_ROWS:
                report.add(
                    "row_count_below_threshold",
                    count=len(rows),
                    severity="error",
                    message=f"板块日线 {len(rows)} 行 < 阈值 {_MIN_SECTOR_ROWS} 行",
                )
            if len(rows) < expected:
                report.add(
                    "coverage_below_previous_day", count=len(rows),
                    severity="warning",
                    message=f"板块日线 {len(rows)} 行 < 上一交易日 {expected} 行",
                )
            duplicated = len(rows) - len({row["sector_code"] for row in rows})
            if duplicated:
                report.add(
                    "duplicate_rows", count=duplicated, severity="error",
                    message=f"{duplicated} 个重复板块代码",
                )
            if failed_boards:
                report.add(
                    "partial_sector_fetch", count=failed_boards, severity="warning",
                    message=f"{failed_boards} 个板块历史拉取失败",
                )
            if live_filled:
                report.add(
                    "live_snapshot_filled", count=live_filled, severity="warning",
                    message=f"{live_filled} 个板块当日历史K线缺失，已用实时快照补齐"
                            "（无成交量/成交额）",
                )
            if not report.ok:
                await _record_quality_isolated(
                    dataset, report, trade_date, QualityStatus.QUALITY_FAILED
                )
                raise QualityBlockedError(
                    f"板块日线质量不合格：{report.error_text()}",
                    detail=report.to_detail(),
                )

            async with _db_scope(db) as session:
                written = await upsert_rows(
                    session, FactSectorDaily, rows, ["sector_code", "trade_date"]
                )
                await record_quality(
                    session, dataset, report, trade_date, QualityStatus.OK
                )

            elapsed = int((time.perf_counter() - started) * 1000)
            result = FetchResult(
                rows=written,
                total=len(rows),
                failed=failed_boards,
                elapsed_ms=elapsed,
                detail={"source": source,
                        "mode": "ths_daily",
                        "boards": ths_detail.get("boards"),
                        "empty_boards": ths_detail.get("empty"),
                        "live_filled": live_filled},
            )
            await job.finish("success" if not failed_boards else "partial",
                             done=written, failed=failed_boards, rows_written=written)
            return result
        except Exception as exc:
            if not isinstance(exc, QualityBlockedError):
                await _record_error(dataset, trade_date, f"{type(exc).__name__}: {exc}")
            await job.finish("failed", error=f"{type(exc).__name__}: {exc}")
            raise


async def collect_sector_members(
    trade_date: date, db: AsyncSession | None = None
) -> FetchResult:
    """A4 板块成分 SCD2（缓慢变化维）。

    规则（回测防未来函数的核心）：
        - 新成分：插入 ``effective_from=trade_date, effective_to=NULL``；
        - 移除成分：把已有开区间的 ``effective_to`` 设为 ``trade_date``（区间左闭右开，
          即"从今天起不再是成分"）；
        - **抓取失败的板块保持现状**（不误关区间）；返回空名单也按失败处理；
        - 成功拿到完整名单时，同时结束该板块所有 partial 开区间（残缺名单被取代），
          否则历史 partial 行会与完整名单并存，把板块永久标成 partial；
        - 消失的板块：仅当当日板块清单覆盖足够完整时才关闭其全部开区间。
    """
    started = time.perf_counter()
    trade_date = _as_date(trade_date)
    task, dataset = "sector_members", "sector_members"

    async with _advisory_lock("sector_members") as acquired:
        if not acquired:
            return await _skip_with_job(task, dataset, trade_date, "lock_busy")
        job = _JobTracker(task, dataset, trade_date=trade_date)
        await job.begin()

        try:
            if not await _is_trade_day(db, trade_date):
                await job.finish("skipped", error="non_trade_day")
                return _skipped_result(task, "non_trade_day", trade_date=trade_date.isoformat())

            sector_info = await _load_sector_info(db, trade_date)
            if not sector_info:
                # 当日板块日线还没采：现场拉一次板块列表（不写 fact_sector_daily）
                chain = get_provider_chain()
                fetched_list = await chain.fetch_sector_list(trade_date)
                for record in _records(fetched_list.detail.get("data")):
                    sector_code = record.get("sector_code")
                    if sector_code:
                        sector_info[str(sector_code)] = {
                            "sector_name": record.get("sector_name") or str(sector_code),
                            "sector_type": record.get("sector_type") or "concept",
                        }
            if not sector_info:
                raise ProviderError("板块清单为空，无法采集成分（请先执行 sector 任务）")

            open_members = await _load_open_members(db)
            partial_members = await _load_partial_members(db)
            sectors = sorted(sector_info.keys())
            await job.update(total=len(sectors))

            chain = get_provider_chain()
            fetched_sets: dict[str, set[str] | None] = {}
            failed = 0
            skipped = 0
            unmatched = 0
            unmatched_sectors: list[str] = []
            circuit_open = False
            budget_exceeded = False
            consecutive_failures = 0
            deadline = started + _SECTOR_MEMBERS_BUDGET_SEC
            for index, sector_code in enumerate(sectors, start=1):
                if circuit_open or budget_exceeded or time.perf_counter() >= deadline:
                    if not circuit_open and not budget_exceeded:
                        budget_exceeded = True
                        logger.error(
                            "板块成分抓取超过 %.0fs 预算，剩余 %d 个板块本次跳过",
                            _SECTOR_MEMBERS_BUDGET_SEC, len(sectors) - index + 1,
                        )
                    skipped += 1
                    fetched_sets[sector_code] = None
                else:
                    info = sector_info[sector_code]
                    try:
                        await HISTORY_LIMITER.acquire()
                        fetched = await chain.fetch_sector_members(
                            sector_code,
                            info["sector_name"],
                            info["sector_type"],
                            timeout=config.FETCH_TIMEOUT_SEC,
                        )
                        if fetched.detail.get("skip_reason"):
                            # 源清单里没有这个板块（名称口径不一致）：不是可用性
                            # 故障，不计失败、不触发熔断，保持已有成分区间不变。
                            unmatched += 1
                            unmatched_sectors.append(str(info["sector_name"]))
                            fetched_sets[sector_code] = None
                        else:
                            member_codes = {
                                str(record.get("stock_code")).zfill(6)
                                for record in _records(fetched.detail.get("data"))
                                if record.get("stock_code")
                            }
                            if not member_codes and open_members.get(sector_code):
                                # 源返回空表而不是抛异常：空集合做差集会关掉全部区间
                                raise ProviderError(
                                    f"返回空名单但该板块已有 "
                                    f"{len(open_members[sector_code])} 个开区间成分，"
                                    "按抓取失败处理（防止误关全部区间）"
                                )
                            fetched_sets[sector_code] = member_codes
                            consecutive_failures = 0
                    except Exception as exc:  # noqa: BLE001 —— 单板块失败保持现状
                        failed += 1
                        fetched_sets[sector_code] = None
                        consecutive_failures += 1
                        logger.warning("板块成分获取失败 sector=%s：%s", sector_code, exc)
                        if consecutive_failures >= _SECTOR_MEMBERS_MAX_CONSECUTIVE_FAILURES:
                            circuit_open = True
                            logger.error(
                                "板块成分连续 %d 个抓取失败，熔断：剩余 %d 个板块本次跳过"
                                "（已有成分保持不变）",
                                consecutive_failures, len(sectors) - index,
                            )

                if index % _BACKFILL_CHECKPOINT_EVERY == 0 or index == len(sectors):
                    logger.info(
                        "板块成分进度 %d/%d（失败 %d，未匹配 %d，跳过 %d）",
                        index, len(sectors), failed, unmatched, skipped,
                    )
                    await job.update(
                        done=index,
                        failed=failed,
                        checkpoint={"trade_date": trade_date.isoformat(),
                                    "done": index, "failed": failed,
                                    "unmatched": unmatched, "skipped": skipped},
                    )

            added = 0
            closed = 0
            async with _db_scope(db) as session:
                for sector_code, member_codes in fetched_sets.items():
                    if member_codes is None:
                        continue  # 抓取失败：绝不基于空集合做差集
                    info = sector_info[sector_code]
                    current = open_members.get(sector_code, set())
                    # partial 区间马上会被下面的 UPDATE 关闭：重叠成分必须按完整
                    # 名单重新落一条开区间，否则关闭后该股票在板块里彻底消失。
                    complete_current = current - partial_members.get(sector_code, set())
                    to_add = member_codes - complete_current
                    to_close = complete_current - member_codes
                    if to_add:
                        add_rows = [
                            {
                                "sector_code": sector_code,
                                "stock_code": stock_code,
                                "effective_from": trade_date,
                                "effective_to": None,
                                "sector_name": str(info["sector_name"])[:64],
                                "sector_type": info["sector_type"],
                                "is_partial": False,
                            }
                            for stock_code in sorted(to_add)
                        ]
                        added += await upsert_rows(
                            session,
                            DimSectorMember,
                            add_rows,
                            ["sector_code", "stock_code", "effective_from"],
                            update_columns=[
                                "effective_to", "sector_name", "sector_type",
                                "is_partial",
                            ],
                        )
                    if to_close:
                        result = await session.execute(
                            update(DimSectorMember)
                            .where(
                                DimSectorMember.sector_code == sector_code,
                                DimSectorMember.stock_code.in_(sorted(to_close)),
                                DimSectorMember.effective_to.is_(None),
                            )
                            .values(effective_to=trade_date)
                        )
                        closed += result.rowcount or 0
                    if sector_code in partial_members:
                        # 本次拿到完整名单：此前只有前端 5 页的 partial 区间到此结束，
                        # 否则它会与完整名单并存，把板块永久标成 partial。
                        result = await session.execute(
                            update(DimSectorMember)
                            .where(
                                DimSectorMember.sector_code == sector_code,
                                DimSectorMember.is_partial.is_(True),
                                DimSectorMember.effective_to.is_(None),
                            )
                            .values(effective_to=trade_date)
                        )
                        closed += result.rowcount or 0

                # 板块整体消失：仅在当日板块清单完整时关闭其全部开区间。
                # sector_daily 部分成功（例如只拿到行业板块）仍可能通过质量门，
                # 直接按"消失"关闭会误伤大量仍存在的板块。
                open_sector_codes = set(open_members)
                current_sector_codes = set(sector_info)
                missing_sectors = open_sector_codes - current_sector_codes
                sector_list_coverage = (
                    len(open_sector_codes & current_sector_codes) / len(open_sector_codes)
                    if open_sector_codes else 1.0
                )
                sector_list_incomplete = (
                    len(open_sector_codes) >= _SECTOR_LIST_MIN_SECTORS
                    and sector_list_coverage < _SECTOR_LIST_MIN_COVERAGE
                )
                if missing_sectors and not sector_list_incomplete:
                    for sector_code in missing_sectors:
                        result = await session.execute(
                            update(DimSectorMember)
                            .where(
                                DimSectorMember.sector_code == sector_code,
                                DimSectorMember.effective_to.is_(None),
                            )
                            .values(effective_to=trade_date)
                        )
                        closed += result.rowcount or 0
                if sector_list_incomplete:
                    logger.error(
                        "当日板块清单仅覆盖 %d/%d 个已有成分板块（%.1f%%），"
                        "%d 个缺失板块本次不关闭成分区间",
                        len(open_sector_codes & current_sector_codes),
                        len(open_sector_codes), sector_list_coverage * 100,
                        len(missing_sectors),
                    )

                success_count = sum(
                    1 for value in fetched_sets.values() if value is not None
                )
                # 未匹配板块在源清单里不存在，永远抓不到；覆盖率按"可抓板块"算，
                # 否则会被固定缺口长期压在阈值以下。
                attempted = max(len(sectors) - unmatched, 0)
                report = QualityReport(
                    dataset=dataset,
                    rows=added + closed,
                    expected=attempted,
                    coverage=round(success_count / attempted, 4) if attempted else 0.0,
                )
                if unmatched:
                    report.add(
                        "sector_name_unmatched",
                        count=unmatched,
                        severity="warning",
                        message=(
                            f"{unmatched} 个板块名称不在东财/新浪清单（口径差异，"
                            "保持已有成分区间不变）"
                        ),
                        samples=sorted(unmatched_sectors)[:10],
                    )
                if failed:
                    report.add(
                        "partial_sector_fetch",
                        count=failed,
                        severity="warning",
                        message=f"{failed} 个板块成分抓取失败（未改动其区间）",
                    )
                if skipped:
                    report.add(
                        "sector_members_skipped",
                        count=skipped,
                        severity="warning",
                        message=(
                            f"{skipped} 个板块成分因熔断/超时本次跳过"
                            "（未改动其区间，重跑可补）"
                        ),
                    )
                if circuit_open or budget_exceeded:
                    # 结构性中止（源整体不可用/超预算）：即使有部分板块成功，
                    # 本次也只更新了一小部分，必须标为不合格，不能让新鲜度显示 ok。
                    report.add(
                        "sector_members_breaker",
                        count=skipped,
                        severity="error",
                        message=(
                            "板块成分抓取因连续失败/超预算提前中止，本次仅更新部分板块"
                            "（已有成分未动，重跑可补）"
                        ),
                    )
                if success_count == 0:
                    report.add(
                        "no_sector_fetched", severity="error",
                        message="所有板块成分抓取失败",
                    )
                if sector_list_incomplete:
                    report.add(
                        "sector_list_incomplete",
                        count=len(missing_sectors),
                        severity="error",
                        message=(
                            f"当日板块清单仅覆盖已有成分板块的 "
                            f"{sector_list_coverage:.1%}，{len(missing_sectors)} 个缺失板块"
                            "本次不关闭成分区间（防止清单不完整误关）"
                        ),
                    )
                await record_quality(
                    session, dataset, report, trade_date,
                    QualityStatus.OK if report.ok else QualityStatus.QUALITY_FAILED,
                )

            elapsed = int((time.perf_counter() - started) * 1000)
            result = FetchResult(
                rows=added + closed,
                total=len(sectors),
                failed=failed,
                elapsed_ms=elapsed,
                detail={
                    "added": added,
                    "closed": closed,
                    "sectors": len(sectors),
                    "unmatched": unmatched,
                    "unmatched_sectors": sorted(unmatched_sectors)[:10],
                    "skipped": skipped,
                    "circuit_open": circuit_open,
                    "budget_exceeded": budget_exceeded,
                    "missing_sectors_skipped": (
                        len(missing_sectors) if sector_list_incomplete else 0
                    ),
                    "no_change": (added + closed) == 0,
                    "attempts": getattr(chain, "last_attempts", None),
                },
                # 全部失败时质量记录是 quality_failed，不能被 run_task 的 mark_success 洗成 ok
                quality_status=(
                    QualityStatus.OK.value if report.ok else QualityStatus.STALE.value
                ),
            )
            await job.finish(
                "success" if not failed else "partial",
                done=len(sectors),
                failed=failed,
                rows_written=added + closed,
            )
            return result
        except Exception as exc:
            if not isinstance(exc, QualityBlockedError):
                await _record_error(dataset, trade_date, f"{type(exc).__name__}: {exc}")
            await job.finish("failed", error=f"{type(exc).__name__}: {exc}")
            raise


async def _load_sector_info(
    db: AsyncSession | None, trade_date: date
) -> dict[str, dict[str, Any]]:
    """读取当日板块清单（fact_sector_daily 有则用，否则空）。"""
    try:
        async with _db_scope(db) as session:
            stmt = select(
                FactSectorDaily.sector_code,
                FactSectorDaily.sector_name,
                FactSectorDaily.sector_type,
            ).where(FactSectorDaily.trade_date == trade_date)
            return {
                row[0]: {"sector_name": row[1], "sector_type": row[2]}
                for row in (await session.execute(stmt)).all()
            }
    except Exception:  # noqa: BLE001
        logger.warning("读取当日板块清单失败", exc_info=True)
        return {}


async def _load_open_members(
    db: AsyncSession | None,
) -> dict[str, set[str]]:
    """读取当前所有板块的"仍有效"成分（effective_to IS NULL）。"""
    try:
        async with _db_scope(db) as session:
            stmt = select(DimSectorMember.sector_code, DimSectorMember.stock_code).where(
                DimSectorMember.effective_to.is_(None)
            )
            result: dict[str, set[str]] = {}
            for sector_code, stock_code in (await session.execute(stmt)).all():
                result.setdefault(sector_code, set()).add(stock_code)
            return result
    except Exception:  # noqa: BLE001
        logger.warning("读取现有板块成分失败（按空处理）", exc_info=True)
        return {}


async def _load_partial_members(
    db: AsyncSession | None,
) -> dict[str, set[str]]:
    """读取仍有 partial 开区间的板块及其成分（完整名单取代时要用）。"""
    try:
        async with _db_scope(db) as session:
            stmt = select(
                DimSectorMember.sector_code, DimSectorMember.stock_code
            ).where(
                DimSectorMember.is_partial.is_(True),
                DimSectorMember.effective_to.is_(None),
            )
            result: dict[str, set[str]] = {}
            for sector_code, stock_code in (await session.execute(stmt)).all():
                result.setdefault(sector_code, set()).add(stock_code)
            return result
    except Exception:  # noqa: BLE001
        logger.warning("读取 partial 成分失败（按空处理）", exc_info=True)
        return {}


# ============================================================
# A5 资金流 / 两融
# ============================================================
async def collect_moneyflow(
    trade_date: date, db: AsyncSession | None = None
) -> FetchResult:
    """A5 个股资金流（主力今日净流入 + 5 日合计，单位元）。"""
    started = time.perf_counter()
    trade_date = _as_date(trade_date)
    task, dataset = "moneyflow", "moneyflow"

    async with _advisory_lock("moneyflow") as acquired:
        if not acquired:
            return await _skip_with_job(task, dataset, trade_date, "lock_busy")
        job = _JobTracker(task, dataset, trade_date=trade_date)
        await job.begin()

        try:
            if not await _is_trade_day(db, trade_date):
                await job.finish("skipped", error="non_trade_day")
                return _skipped_result(task, "non_trade_day", trade_date=trade_date.isoformat())

            # 资金流排行同样只提供"当前快照"，历史日无法重建
            not_ready = await _snapshot_readiness(trade_date, db)
            if not_ready is not None:
                await job.finish("skipped", error=not_ready)
                return _skipped_result(
                    task, not_ready, trade_date=trade_date.isoformat()
                )

            chain = get_provider_chain()
            fetched = await chain.fetch_moneyflow(
                trade_date, timeout=config.SNAPSHOT_FETCH_TIMEOUT_SEC
            )
            frame = fetched.detail.get("data")
            rows = _moneyflow_rows(frame, trade_date)

            active_count = await _active_stock_count(db)
            expected = max(active_count, config.QUALITY_MIN_SNAPSHOT_ROWS)
            report = QualityReport(
                dataset=dataset,
                rows=len(rows),
                expected=expected,
                coverage=round(min(len(rows) / expected, 1.0), 4) if expected else 0.0,
            )
            if not rows:
                report.add("empty_frame", severity="error", message="资金流数据为空")
            elif report.coverage < config.QUALITY_MIN_COVERAGE:
                report.add(
                    "coverage_below_threshold",
                    count=len(rows),
                    severity="error",
                    message=f"资金流覆盖率 {report.coverage:.2%} < "
                            f"{config.QUALITY_MIN_COVERAGE:.0%}",
                )
            missing_t0 = sum(1 for row in rows if row.get("main_net_t0") is None)
            if rows and missing_t0 / len(rows) > config.QUALITY_MAX_MISSING_RATIO:
                report.add(
                    "missing_ratio_exceeded",
                    count=missing_t0,
                    severity="warning",
                    message=f"{missing_t0} 行缺少主力净流入（{missing_t0 / len(rows):.2%}）",
                )
            if not report.ok:
                await _record_quality_isolated(
                    dataset, report, trade_date, QualityStatus.QUALITY_FAILED
                )
                raise QualityBlockedError(
                    f"资金流质量不合格：{report.error_text()}",
                    detail=report.to_detail(),
                )

            async with _db_scope(db) as session:
                written = await upsert_rows(
                    session, FactMoneyflow, rows, ["code", "trade_date"]
                )
                await record_quality(
                    session, dataset, report, trade_date, QualityStatus.OK
                )

            elapsed = int((time.perf_counter() - started) * 1000)
            result = FetchResult(
                rows=written,
                total=expected,
                elapsed_ms=elapsed,
                detail={"source": fetched.source,
                        "coverage": report.coverage,
                        "five_day_available": fetched.detail.get("five_day_available")},
            )
            await job.finish("success", done=written, rows_written=written)
            return result
        except Exception as exc:
            if not isinstance(exc, QualityBlockedError):
                await _record_error(dataset, trade_date, f"{type(exc).__name__}: {exc}")
            await job.finish("failed", error=f"{type(exc).__name__}: {exc}")
            raise


async def collect_margin(
    trade_date: date, db: AsyncSession | None = None
) -> FetchResult:
    """A5 两融 + 北向（市场级，单位亿元；北向可能为空，属预期内）。"""
    started = time.perf_counter()
    trade_date = _as_date(trade_date)
    task, dataset = "margin", "margin"

    async with _advisory_lock("margin") as acquired:
        if not acquired:
            return await _skip_with_job(task, dataset, trade_date, "lock_busy")
        job = _JobTracker(task, dataset, trade_date=trade_date)
        await job.begin()

        try:
            if not await _is_trade_day(db, trade_date):
                await job.finish("skipped", error="non_trade_day")
                return _skipped_result(task, "non_trade_day", trade_date=trade_date.isoformat())

            chain = get_provider_chain()
            fetched = await chain.fetch_margin(
                trade_date, timeout=config.FETCH_TIMEOUT_SEC
            )
            frame = fetched.detail.get("data")
            # 两融 T+1 披露：provider 可能把实际已披露日放在数据行里（effective_date）
            row = _margin_row(frame, trade_date, previous_balance=None)
            if row is None:
                raise ProviderError("两融数据归一化后为空")
            if row.get("margin_balance") is None and row.get("short_balance") is None:
                # 收盘前/数据源延迟时两融尚未披露：不能写一条全空行冒充"已采集"
                raise ProviderError(
                    "两融数据尚未更新（可能未收盘或数据源延迟）",
                    detail={"trade_date": trade_date.isoformat()},
                )

            actual_date: date = row["trade_date"]
            previous_balance = await _previous_margin_balance(db, actual_date)
            if row.get("margin_balance") is not None and previous_balance is not None:
                row["margin_change"] = round(
                    float(row["margin_balance"]) - float(previous_balance), 4
                )

            report = QualityReport(
                dataset=dataset, rows=1, expected=1, coverage=1.0
            )
            if actual_date != trade_date:
                report.add(
                    "published_date_lag",
                    severity="warning",
                    message=(
                        f"两融数据 T+1 披露：请求 {trade_date}，实际落库 {actual_date}"
                    ),
                )
            if not fetched.detail.get("north_available"):
                report.add(
                    "northward_empty",
                    severity="warning",
                    message="北向当日成交净买额为空（2024-08 后口径变化，属预期内）",
                )
            if fetched.failed:
                report.add(
                    "partial_margin", count=fetched.failed, severity="warning",
                    message="沪深两融只有一个市场数据",
                )

            async with _db_scope(db) as session:
                written = await upsert_rows(
                    session, MarginTrading, [row], ["trade_date"]
                )
                await record_quality(
                    session, dataset, report, actual_date, QualityStatus.OK
                )

            elapsed = int((time.perf_counter() - started) * 1000)
            result = FetchResult(
                rows=written,
                total=1,
                failed=fetched.failed,
                elapsed_ms=elapsed,
                detail={"source": fetched.source,
                        "requested_trade_date": trade_date.isoformat(),
                        "actual_trade_date": actual_date.isoformat(),
                        "margin_balance": row.get("margin_balance"),
                        "northward_net": row.get("northward_net"),
                        "attempts": fetched.detail.get("attempts")},
            )
            await job.finish("success" if not fetched.failed else "partial",
                             done=1, failed=fetched.failed, rows_written=written)
            return result
        except Exception as exc:
            await _record_error(dataset, trade_date, f"{type(exc).__name__}: {exc}")
            await job.finish("failed", error=f"{type(exc).__name__}: {exc}")
            raise


async def _previous_margin_balance(
    db: AsyncSession | None, trade_date: date
) -> float | None:
    """取上一个交易日的融资余额（算 margin_change 用）。"""
    try:
        async with _db_scope(db) as session:
            stmt = (
                select(MarginTrading.margin_balance)
                .where(MarginTrading.trade_date < trade_date)
                .order_by(MarginTrading.trade_date.desc())
                .limit(1)
            )
            value = (await session.execute(stmt)).scalar_one_or_none()
            return float(value) if value is not None else None
    except Exception:  # noqa: BLE001
        return None


# ============================================================
# 宏观利率（中债国债收益率）
# ============================================================
async def collect_macro_rates(
    trade_date: date, db: AsyncSession | None = None
) -> FetchResult:
    """中债 1Y/10Y 国债收益率（日频）。

    中债接口按自然日返回，节假日没有曲线行；这里回看 15 个自然日并 upsert
    已有行，既覆盖 T+1 发布延迟，也保证幂等。整段失败不阻断后续管线。
    """
    started = time.perf_counter()
    trade_date = _as_date(trade_date)
    task, dataset = "macro_rates", "macro_rates"

    async with _advisory_lock("macro_rates") as acquired:
        if not acquired:
            return await _skip_with_job(task, dataset, trade_date, "lock_busy")
        job = _JobTracker(task, dataset, trade_date=trade_date)
        await job.begin()

        try:
            if not await _is_trade_day(db, trade_date):
                await job.finish("skipped", error="non_trade_day")
                return _skipped_result(task, "non_trade_day", trade_date=trade_date.isoformat())

            chain = get_provider_chain()
            fetched = await chain.fetch_bond_yields(
                trade_date - timedelta(days=15),
                trade_date,
                timeout=config.FETCH_TIMEOUT_SEC,
            )
            frame = fetched.detail.get("data")
            rows: list[dict[str, Any]] = []
            if frame is not None and not getattr(frame, "empty", True):
                for record in frame.to_dict("records"):
                    day = _as_date_optional(record.get("trade_date"))
                    if day is None or day > trade_date:
                        continue
                    rows.append({
                        "trade_date": day,
                        "cn_1y": record.get("cn_1y"),
                        "cn_10y": record.get("cn_10y"),
                        "term_spread": record.get("term_spread"),
                        "src": "chinabond",
                    })
            if not rows:
                raise ProviderError("中债国债收益率归一化后为空")

            report = QualityReport(
                dataset=dataset, rows=len(rows), expected=len(rows), coverage=1.0
            )
            async with _db_scope(db) as session:
                written = await upsert_rows(
                    session, MacroRate, rows, ["trade_date"]
                )
                await record_quality(
                    session, dataset, report,
                    max(row["trade_date"] for row in rows), QualityStatus.OK,
                )
            await job.finish("success", done=len(rows), rows_written=written)
            return FetchResult(
                rows=written,
                total=len(rows),
                failed=fetched.failed,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                detail={"source": fetched.source, "latest": rows[-1]["trade_date"].isoformat()},
            )
        except Exception as exc:
            await _record_error(dataset, trade_date, f"{type(exc).__name__}: {exc}")
            await job.finish("failed", error=f"{type(exc).__name__}: {exc}")
            raise


# ============================================================
# A6 指数日线
# ============================================================
async def collect_indices(
    trade_date: date, db: AsyncSession | None = None, include_us: bool = False
) -> FetchResult:
    """A6 指数日线（上证/深成/创业板/科创50/沪深300，可选美股三指数）。"""
    started = time.perf_counter()
    trade_date = _as_date(trade_date)
    task, dataset = "index_daily", "index_daily"

    async with _advisory_lock("indices") as acquired:
        if not acquired:
            return await _skip_with_job(task, dataset, trade_date, "lock_busy")
        job = _JobTracker(task, dataset, trade_date=trade_date)
        await job.begin()

        try:
            if not await _is_trade_day(db, trade_date):
                await job.finish("skipped", error="non_trade_day")
                return _skipped_result(task, "non_trade_day", trade_date=trade_date.isoformat())

            expected_a = sum(1 for _, is_a_share in INDEX_MAP.values() if is_a_share)
            expected_us = sum(1 for _, is_a_share in INDEX_MAP.values() if not is_a_share)
            expected = expected_a + (expected_us if include_us else 0)

            chain = get_provider_chain()
            fetched = await chain.fetch_index_daily(
                trade_date, include_us=include_us, timeout=config.FETCH_TIMEOUT_SEC
            )
            frame = fetched.detail.get("data")
            rows = _index_rows(frame, trade_date)
            report = QualityReport(
                dataset=dataset,
                rows=len(rows),
                expected=expected,
                coverage=round(min(len(rows) / expected, 1.0), 4) if expected else 0.0,
            )
            if len(rows) < expected_a:
                report.add(
                    "index_rows_below_expected",
                    count=len(rows),
                    severity="error",
                    message=f"指数日线 {len(rows)} 条 < A 股指数应有 {expected_a} 条",
                )
            if fetched.failed:
                report.add(
                    "partial_index_fetch", count=fetched.failed, severity="warning",
                    message=f"{fetched.failed} 个指数拉取失败",
                )
            if include_us and len(rows) < expected:
                report.add(
                    "us_index_missing", count=expected - len(rows), severity="warning",
                    message="美股指数缺失（yfinance 未安装或休市，不阻断）",
                )
            # 指数是**展示型辅助数据**，单个指数瞬时失败不应清空整批：
            # 之前这里直接 raise，导致 5 个指数里 2 个网络抖动就全部不落库，
            # 仪表盘指数行空掉（实测踩到）。改为"部分成功即写库 + 记异常"。
            partial_ok = len(rows) >= 1
            if not report.ok and not partial_ok:
                await _record_quality_isolated(
                    dataset, report, trade_date, QualityStatus.QUALITY_FAILED
                )
                raise QualityBlockedError(
                    f"指数日线质量不合格：{report.error_text()}",
                    detail=report.to_detail(),
                )
            if not report.ok:
                logger.warning(
                    "指数日线部分缺失（%d/%d），按已取到的行落库：%s",
                    len(rows), expected, report.error_text(),
                )

            async with _db_scope(db) as session:
                written = await upsert_rows(
                    session, MarketIndex, rows, ["index_code", "trade_date"]
                )
                await record_quality(
                    session, dataset, report, trade_date,
                    QualityStatus.OK if report.ok else QualityStatus.STALE,
                )

            elapsed = int((time.perf_counter() - started) * 1000)
            result = FetchResult(
                rows=written,
                total=expected,
                failed=fetched.failed,
                elapsed_ms=elapsed,
                detail={"source": fetched.source,
                        "attempts": fetched.detail.get("attempts"),
                        # FetchResult 没有 coverage 字段；run_task 做 mark_success
                        # 时从 detail 回填，否则会把这里的精确覆盖率覆盖成 NULL。
                        "coverage": report.coverage,
                        "index_sources": fetched.detail.get("index_sources")},
                # 部分缺失时质量记录为 stale；run_task 不能再用 mark_success 洗成 ok
                quality_status=(
                    QualityStatus.OK.value if report.ok else QualityStatus.STALE.value
                ),
            )
            await job.finish("success" if not fetched.failed else "partial",
                             done=written, failed=fetched.failed, rows_written=written)
            return result
        except Exception as exc:
            if not isinstance(exc, QualityBlockedError):
                await _record_error(dataset, trade_date, f"{type(exc).__name__}: {exc}")
            await job.finish("failed", error=f"{type(exc).__name__}: {exc}")
            raise


# ============================================================
# A8 每日采集编排
# ============================================================
async def run_daily_ingest(
    trade_date: date,
    db: AsyncSession | None = None,
    include: Sequence[str] = (
        "calendar", "universe", "indices", "sectors", "snapshot", "moneyflow", "margin",
    ),
) -> dict[str, Any]:
    """A8 每日采集编排：按顺序跑完各数据集，每步独立 try/except。

    Args:
        trade_date: 目标交易日；非交易日直接返回 ``status=skipped``（不写任何数据）。
        db: 可选会话。
        include: 要执行的步骤（默认全套）。``sectors`` 步骤 = 板块日线 + 板块成分。

    Returns:
        可 JSON 序列化的汇总::

            {
              "trade_date": "2026-10-21",
              "status": "success|partial|failed|skipped",
              "ok": true,
              "job_id": "...",
              "elapsed_ms": 12345,
              "steps": {"snapshot": {"status": "ok", "rows": 5382, ...}, ...},
            }
    """
    started = time.perf_counter()
    trade_date = _as_date(trade_date)
    steps = [str(step) for step in include]
    task, dataset = "daily_ingest", "daily_ingest"

    async with _advisory_lock("daily_ingest") as acquired:
        if not acquired:
            return {
                "trade_date": trade_date.isoformat(),
                "status": "skipped",
                "ok": False,
                "skipped": "lock_busy",
                "steps": {},
            }

        if not await _is_trade_day(db, trade_date):
            return {
                "trade_date": trade_date.isoformat(),
                "status": "skipped",
                "ok": False,
                "skipped": "non_trade_day",
                "steps": {},
            }

        # 盘中跑"每日全链路"会写入不完整的当日截面：必须在收盘数据就绪后执行
        if trade_date == _today_cn() and not _after_close_ready():
            return {
                "trade_date": trade_date.isoformat(),
                "status": "skipped",
                "ok": False,
                "skipped": "before_close_ready_time",
                "steps": {},
            }

        job = _JobTracker(
            task, dataset, trade_date=trade_date, params={"include": steps}
        )
        await job.begin(total=len(steps))
        results: dict[str, Any] = {}

        handlers = {
            "calendar": _step_calendar,
            "universe": _step_universe,
            "indices": collect_indices,
            "sectors": _step_sectors,
            "snapshot": collect_daily_snapshot,
            "moneyflow": collect_moneyflow,
            "margin": collect_margin,
        }

        try:
            for index, step in enumerate(steps, start=1):
                step_started = time.perf_counter()
                handler = handlers.get(step)
                if handler is None:
                    results[step] = {
                        "status": "failed",
                        "error": "unknown_step",
                        "message": f"未知采集步骤：{step}",
                    }
                    continue
                try:
                    result = await handler(trade_date, db)
                    step_ms = int((time.perf_counter() - step_started) * 1000)
                    results[step] = _result_summary(result, step_ms=step_ms)
                except AppError as exc:
                    results[step] = {
                        "status": "failed",
                        "error": exc.code,
                        "message": exc.message,
                        "elapsed_ms": int((time.perf_counter() - step_started) * 1000),
                    }
                    logger.warning("每日采集步骤失败 step=%s：%s %s", step, exc.code, exc.message)
                except Exception as exc:  # noqa: BLE001 —— 一步失败不阻塞后续
                    results[step] = {
                        "status": "failed",
                        "error": type(exc).__name__,
                        "message": str(exc)[:500],
                        "elapsed_ms": int((time.perf_counter() - step_started) * 1000),
                    }
                    logger.exception("每日采集步骤异常 step=%s", step)

                await job.update(
                    done=index,
                    rows_written=sum(
                        int(item.get("rows") or 0)
                        for item in results.values()
                        if isinstance(item, dict)
                    ),
                    checkpoint={"steps": results},
                )

            failed_steps = [
                name for name, item in results.items() if item.get("status") == "failed"
            ]
            if not failed_steps:
                status = "success"
            elif len(failed_steps) < len(steps):
                status = "partial"
            else:
                status = "failed"
            elapsed = int((time.perf_counter() - started) * 1000)
            await job.finish(status, checkpoint={"steps": results})
            return {
                "trade_date": trade_date.isoformat(),
                "status": status,
                "ok": status == "success",
                "job_id": job.job_id,
                "elapsed_ms": elapsed,
                "steps": results,
            }
        except Exception as exc:
            await job.finish("failed", error=f"{type(exc).__name__}: {exc}")
            raise


async def _step_calendar(trade_date: date, db: AsyncSession | None) -> FetchResult:
    """每日流水线的 calendar 步骤：同步目标日前后一段日历（保留历史区间完整性）。"""
    async with _db_scope(db) as session:
        return await cal.sync_trade_calendar(
            session, trade_date - timedelta(days=40), trade_date + timedelta(days=7)
        )


async def _step_universe(trade_date: date, db: AsyncSession | None) -> FetchResult:
    """每日流水线的 universe 步骤（适配 (trade_date, db) 统一签名）。"""
    return await sync_stock_universe(db)


async def _step_sectors(trade_date: date, db: AsyncSession | None) -> FetchResult:
    """每日流水线的 sectors 步骤 = 板块日线 + 板块成分 SCD2。"""
    daily = await collect_sector_daily(trade_date, db)
    members = await collect_sector_members(trade_date, db)
    detail = {"daily": _result_summary(daily), "members": _result_summary(members)}
    return FetchResult(
        rows=daily.rows + members.rows,
        total=(daily.total or 0) + (members.total or 0),
        failed=daily.failed + members.failed,
        elapsed_ms=daily.elapsed_ms + members.elapsed_ms,
        detail=detail,
    )


# ============================================================
# A9 附加：复权因子交叉校验（akshare vs baostock）
# ============================================================
async def verify_adj_factors(
    codes: list[str] | None = None,
    sample_size: int = 5,
    start: date | None = None,
    end: date | None = None,
) -> FetchResult:
    """用 Baostock 交叉校验 AKShare 的 ``adj_factor``（方案 9：复权口径风险应对）。

    方案要求"用 Baostock 交叉校验 5–10 只样本"：两边都取后复权/不复权收盘价，
    比较 ``adj_factor = hfq_close / raw_close`` 的相对误差；超过 1% 记为不一致。

    Args:
        codes: 指定样本代码；None 时从 ``dim_stock`` 取前 ``sample_size`` 只（确定性）。
        sample_size: 样本数量（默认 5）。
        start / end: 校验区间，默认最近 90 天。

    Returns:
        ``FetchResult``；``detail["checks"]`` 为逐只对比结果。
    """
    started = time.perf_counter()
    end_date = _as_date(end) if end is not None else _today_cn()
    start_date = _as_date(start) if start is not None else end_date - timedelta(days=90)

    if codes:
        code_list = [str(code).zfill(6) for code in codes][:sample_size]
    else:
        async with session_scope() as session:
            stmt = select(DimStock.code).order_by(DimStock.code).limit(sample_size)
            code_list = [row[0] for row in (await session.execute(stmt)).all()]
    if not code_list:
        raise DataUnavailableError("股票池为空，无法做复权交叉校验（请先同步 universe）")

    ak_provider = AkshareProvider()
    bs_provider = BaostockProvider()
    checks: list[dict[str, Any]] = []

    for code in code_list:
        await HISTORY_LIMITER.acquire()
        check: dict[str, Any] = {"code": code, "ok": False}
        try:
            ak_result = ak_provider.fetch_stock_history(code, start_date, end_date)
            bs_result = bs_provider.fetch_stock_history(code, start_date, end_date)
            ak_frame = ak_result.detail.get("data")
            bs_frame = bs_result.detail.get("data")
            ak_map = {
                _as_date(row["trade_date"]): float(row["adj_factor"])
                for row in _records(ak_frame)
                if row.get("trade_date") is not None and row.get("adj_factor") is not None
            }
            bs_map = {
                _as_date(row["trade_date"]): float(row["adj_factor"])
                for row in _records(bs_frame)
                if row.get("trade_date") is not None and row.get("adj_factor") is not None
            }
            common = sorted(set(ak_map) & set(bs_map))
            diffs = [
                abs(ak_map[day] - bs_map[day]) / bs_map[day]
                for day in common
                if bs_map[day]
            ]
            max_diff = max(diffs) if diffs else None
            check.update(
                {
                    "ok": bool(common) and max_diff is not None and max_diff <= 0.01,
                    "common_days": len(common),
                    "max_rel_diff": round(max_diff, 6) if max_diff is not None else None,
                    "sample": [
                        {
                            "trade_date": day.isoformat(),
                            "akshare": round(ak_map[day], 6),
                            "baostock": round(bs_map[day], 6),
                        }
                        for day in common[-2:]
                    ],
                }
            )
        except Exception as exc:  # noqa: BLE001
            check["error"] = f"{type(exc).__name__}: {exc}"
        checks.append(check)

    failed = sum(1 for check in checks if not check.get("ok"))
    report = QualityReport(
        dataset="adj_factor_check",
        rows=len(checks),
        expected=len(code_list),
        coverage=round((len(checks) - failed) / len(checks), 4) if checks else 0.0,
    )
    if failed:
        report.add(
            "adj_factor_mismatch",
            count=failed,
            severity="warning",
            message=f"{failed}/{len(checks)} 只样本复权因子与 baostock 偏差 > 1%",
            samples=[check["code"] for check in checks if not check.get("ok")],
        )
    await _record_quality_isolated(
        "adj_factor_check", report, end_date, QualityStatus.OK
    )

    elapsed = int((time.perf_counter() - started) * 1000)
    return FetchResult(
        rows=len(checks),
        total=len(code_list),
        failed=failed,
        elapsed_ms=elapsed,
        detail={"checks": checks, "source": "akshare+baostock",
                "start": start_date.isoformat(), "end": end_date.isoformat()},
    )


__all__ = [
    "build_provider_chain",
    "get_provider_chain",
    "use_provider_chain",
    "sync_stock_universe",
    "collect_daily_snapshot",
    "backfill_stock_history",
    "collect_sector_daily",
    "collect_sector_members",
    "collect_moneyflow",
    "collect_indices",
    "collect_margin",
    "collect_macro_rates",
    "run_daily_ingest",
    "verify_adj_factors",
]
