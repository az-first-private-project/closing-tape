"""
策略运行、run 快照与缓存（方案 D3）
====================================
**参数真生效的关键**：缓存键 = ``(algo_code, trade_date, params_fingerprint, data_version)``。
旧实现只按 (algo, date) 缓存，导致改参数后仍返回旧结果——这是 V4 的核心 bug。
本模块的规则：

  1. 先把参数按策略 schema 归一（补默认值、转类型），再算稳定指纹；
  2. 数据版本 = 当日四表行数 + 最近写入时间的哈希（行数不变但值被 upsert
     更新时同样失效，见 ``data_version``）；
  3. 命中缓存必须 4 项全等，否则重算；
  4. 任何一次运行都先落 `stg_run(status="running")`，结束更新 success/failed
     ——**没有 run 记录的结果不可复现，不能返回给前端**。

事务所有权（与 Lead 的 API/pipeline 约定）：
  - `db=None`：自建会话并自行 commit/rollback（失败也留 failed 记录）；
  - 传入 `db`（API 依赖注入 / pipeline / 回测）：**只 flush，不 commit**，
    事务提交/回滚由调用方负责。

返回值严格按 `docs/V5-API-CONTRACT.md` 5.2：

    {algo, algo_name, trade_date, run:{run_id, created_at, elapsed_ms,
     universe_count, signal_count, data_version, params}, from_cache, count, data:[...]}
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pandas as pd
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DataUnavailableError, InvalidParamError, UnknownAlgoError
from app.db.models.fact import (
    FactFeatureDaily,
    FactMoneyflow,
    FactStockDaily,
    FactStockScoreDaily,
)
from app.db.models.strategy import StgRun, StgSignal
from app.db.session import AsyncSessionLocal, session_scope
from app.strategy import strategies as strat

logger = logging.getLogger(__name__)

#: 缓存查找时回看的历史 run 条数（同一 (algo, date) 下参数组合通常远小于该值）
_CACHE_LOOKBACK = 50


# =====================================================================
# 基础工具
# =====================================================================

def _coerce_date(value: Any, *, field_name: str = "trade_date") -> date:
    """把 date / datetime / ISO 字符串统一成 date。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            pass
    raise InvalidParamError(
        f"{field_name} 必须是日期（YYYY-MM-DD），收到: {value!r}",
        detail={"field": field_name, "value": str(value)},
    )


def _canonical(value: Any) -> Any:
    """递归规范化（dict 按 key 排序），用于稳定哈希。"""
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, set):
        return sorted((_canonical(item) for item in value), key=str)
    return strat.jsonable(value)


def params_fingerprint(algo_code: str, params: dict) -> str:
    """参数指纹：稳定 hash（与 key 顺序无关，值类型归一后参与哈希）。

    用于缓存键，保证"参数不同 → 必不命中缓存"。
    """
    payload = {
        "algo": str(algo_code or "").strip().lower(),
        "params": _canonical(params or {}),
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


async def latest_trade_date(db: AsyncSession) -> date:
    """当前数据库里的最新有行情交易日。"""
    result = await db.execute(select(func.max(FactStockDaily.trade_date)))
    value = result.scalar_one_or_none()
    if value is None:
        raise DataUnavailableError(
            "数据库中尚无个股行情数据",
            next_action="POST /api/v1/admin/ingest/daily_snapshot",
            detail={"dataset": "fact_stock_daily"},
        )
    return _coerce_date(value)


#: 自动选择交易日时，要求该策略所需数据集至少有多少只股票有值。
#: 太低会把"只有几十只试点股"的日期当成可用日，选股结果会误导。
_MIN_READY_ROWS = 100


async def latest_ready_trade_date(
    db: AsyncSession, algo_code: str, *, min_rows: int = _MIN_READY_ROWS
) -> date | None:
    """该策略所需数据最新齐全的交易日。

    股票日线最新日不一定有指标/估值（例如收盘后只跑了快照）。直接拿
    ``max(fact_stock_daily.trade_date)`` 会让策略全部报"数据未计算"，
    所以这里按策略分别找最新可用日：

    - a3：个股综合评分；
    - a4：PE + 股息率；
    - b1–b5：对应的短线技术指标列 + 成交额。

    返回 None 表示没有任何日期达到 ``min_rows``（调用方回退到最新行情日，
    由策略层给出精确的缺失原因）。
    """
    from sqlalchemy import text

    # 列名来自常量映射，不接收外部输入（SQL 文本无法参数化列名）
    b_ready_column = {
        "b1": "ema20",
        "b2": "rsi6",
        "b3": "kdj_j",
        "b4": "boll_width_pct60",
        "b5": "kdj_j",
    }
    if algo_code in b_ready_column:
        sql = text(
            f"""
            SELECT f.trade_date
            FROM (
                SELECT trade_date, count(*) AS n
                FROM fact_feature_daily
                WHERE {b_ready_column[algo_code]} IS NOT NULL
                GROUP BY trade_date
            ) f
            JOIN (
                SELECT trade_date, count(*) AS n
                FROM fact_stock_daily
                WHERE amount IS NOT NULL
                GROUP BY trade_date
            ) d USING (trade_date)
            WHERE f.n >= :min_rows AND d.n >= :min_rows
            ORDER BY f.trade_date DESC
            LIMIT 1
            """
        )
    elif algo_code == "a3":
        sql = text(
            """
            SELECT trade_date
            FROM fact_stock_score_daily
            WHERE composite_score IS NOT NULL
            GROUP BY trade_date
            HAVING count(*) >= :min_rows
            ORDER BY trade_date DESC
            LIMIT 1
            """
        )
    elif algo_code == "a4":
        sql = text(
            """
            SELECT trade_date
            FROM fact_stock_daily
            WHERE pe_ttm IS NOT NULL AND dv_ttm IS NOT NULL
            GROUP BY trade_date
            HAVING count(*) >= :min_rows
            ORDER BY trade_date DESC
            LIMIT 1
            """
        )
    else:  # pragma: no cover —— 策略清单变化时保持安全回退
        return None

    row = (await db.execute(sql, {"min_rows": int(min_rows)})).first()
    return _coerce_date(row[0]) if row and row[0] is not None else None


async def data_version(db: AsyncSession, trade_date: date) -> str:
    """数据版本指纹：四表当日行数 + 最近写入时间，压缩为哈希。

    只统计行数时，历史回填/重算走 upsert 更新同一批行，指纹不变，按旧数据
    算出的 stg_run 会一直命中缓存（其特征覆盖可能只有一半）。把
    ``max(updated_at)`` 一并纳入哈希，任何一次重写都会使缓存失效。
    """
    trade_date = _coerce_date(trade_date)
    parts: list[str] = []
    counts: dict[str, int] = {}
    for prefix, model in (
        ("b", FactStockDaily),
        ("f", FactFeatureDaily),
        ("s", FactStockScoreDaily),
        ("m", FactMoneyflow),
    ):
        result = await db.execute(
            select(func.count(), func.max(model.updated_at)).where(model.trade_date == trade_date)
        )
        rows, stamp = result.one()
        counts[prefix] = int(rows or 0)
        parts.append(f"{prefix}{counts[prefix]}@{stamp.isoformat() if stamp else '-'}")
    if counts["b"] <= 0:
        raise DataUnavailableError(
            f"{trade_date.isoformat()} 无个股行情数据",
            next_action="POST /api/v1/admin/ingest/daily_snapshot",
            detail={"trade_date": trade_date.isoformat()},
        )
    # stg_run.data_version 为 VARCHAR(32)：日期(10) + ':' + 16 位摘要 = 27 字符
    digest = hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]
    return f"{trade_date.isoformat()}:{digest}"


# =====================================================================
# 结果组装（fresh / cached 共用同一套序列化，结构完全一致）
# =====================================================================

def _iso(value: Any) -> str | None:
    """datetime → ISO-8601 字符串。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _signal_item(code: str, name: Any, rank: Any, score: Any, metrics: dict) -> dict:
    """单条信号：契约 5.2 data[] 元素（顶层展示列由 metrics 派生，缓存前后一致）。"""
    safe_metrics = strat.jsonable(metrics or {}) or {}
    item: dict[str, Any] = {
        "code": str(code).zfill(6) if code is not None else "",
        "name": name,
        "rank": int(rank or 0),
        "score": strat.jsonable(score),
    }
    for field_name in strat.DISPLAY_FIELDS:
        item[field_name] = safe_metrics.get(field_name)
    item["metrics"] = safe_metrics
    return item


def _items_from_frame(frame: pd.DataFrame) -> list[dict]:
    """rank_signals 输出的 DataFrame → 契约 data[] 列表。"""
    if frame is None or len(frame) == 0:
        return []
    items: list[dict] = []
    for record in frame.to_dict("records"):
        items.append(
            _signal_item(
                code=record.get("code"),
                name=strat.jsonable(record.get("name")),
                rank=record.get("rank"),
                score=record.get("score"),
                metrics=record.get("metrics") or {},
            )
        )
    return items


def _items_from_signals(rows: list[StgSignal]) -> list[dict]:
    """数据库信号行 → 契约 data[] 列表。"""
    return [
        _signal_item(
            code=row.code,
            name=row.name,
            rank=row.rank,
            score=row.score,
            metrics=row.metrics or {},
        )
        for row in rows
    ]


def _run_meta(run: StgRun) -> dict:
    """契约 5.2 的 run 元信息（额外带 status/error 方便前端展示失败 run）。"""
    meta: dict[str, Any] = {
        "run_id": run.run_id,
        "created_at": _iso(run.created_at),
        "elapsed_ms": int(run.elapsed_ms) if run.elapsed_ms is not None else None,
        "universe_count": int(run.universe_count or 0),
        "signal_count": int(run.signal_count or 0),
        "data_version": run.data_version,
        "params": strat.jsonable(run.params or {}),
        "status": run.status,
    }
    if run.error:
        meta["error"] = run.error
    if run.is_backtest:
        meta["is_backtest"] = True
    return meta


def _assemble(
    run: StgRun,
    spec: strat.StrategySpec,
    items: list[dict],
    *,
    from_cache: bool,
) -> dict:
    """组装契约 5.2 响应。"""
    return {
        "algo": spec.code,
        "algo_name": spec.name,
        "trade_date": run.trade_date.isoformat() if run.trade_date else None,
        "run": _run_meta(run),
        "from_cache": bool(from_cache),
        "count": len(items),
        "data": items,
    }


async def _load_signal_rows(db: AsyncSession, run_id: str) -> list[StgSignal]:
    """读取某次 run 的信号明细（按 rank 升序，保证顺序确定）。"""
    result = await db.execute(
        select(StgSignal)
        .where(StgSignal.run_id == run_id)
        .order_by(StgSignal.rank.asc(), StgSignal.code.asc())
    )
    return list(result.scalars().all())


# =====================================================================
# 缓存查找
# =====================================================================

async def get_cached_run(
    algo_code: str,
    trade_date: date,
    params: dict,
    db: AsyncSession,
) -> dict | None:
    """按 4 元缓存键查找可复用的成功 run；未命中返回 None。"""
    spec = strat.get_spec(algo_code)
    normalized = strat._validate_params(spec.code, params)  # noqa: SLF001 - 同包内部约定
    trade_date = _coerce_date(trade_date)
    version = await data_version(db, trade_date)
    fingerprint = params_fingerprint(spec.code, normalized)

    result = await db.execute(
        select(StgRun)
        .where(StgRun.algo_code == spec.code)
        .where(StgRun.trade_date == trade_date)
        .where(StgRun.status == "success")
        .order_by(StgRun.created_at.desc())
        .limit(_CACHE_LOOKBACK)
    )
    candidates = list(result.scalars().all())
    for run in candidates:
        stored_params = run.params or {}
        if params_fingerprint(spec.code, stored_params) != fingerprint:
            continue
        if run.data_version != version:
            continue
        signals = await _load_signal_rows(db, run.run_id)
        logger.info(
            "策略缓存命中 algo=%s date=%s run_id=%s params=%s",
            spec.code, trade_date, run.run_id, fingerprint,
        )
        return _assemble(run, spec, _items_from_signals(signals), from_cache=True)
    return None


# =====================================================================
# 主入口：执行策略
# =====================================================================

async def run_strategy(
    algo_code: str,
    trade_date: date | None = None,
    params: dict | None = None,
    force: bool = False,
    db: AsyncSession | None = None,
    *,
    is_backtest: bool = False,
) -> dict:
    """执行策略并落 run 快照（缓存优先）。

    参数：
        algo_code:  a3/a4/b1–b5
        trade_date: 交易日；None 时取库中最新可用交易日
        params:     策略参数（会补默认值并校验）
        force:      True 忽略缓存强制重算
        db:         可复用已有会话（回测内部传同一个会话）；None 时自开会话
        is_backtest: 仅用于标记 run 归属（不影响缓存命中）
    """
    spec = strat.get_spec(algo_code)
    normalized = strat._validate_params(spec.code, params)

    if db is not None:
        # 调用方（API 依赖注入 / pipeline）持有事务：只 flush，不 commit
        return await _run_strategy_impl(
            spec, trade_date, normalized, force, db, is_backtest, owns_session=False
        )
    async with AsyncSessionLocal() as session:
        return await _run_strategy_impl(
            spec, trade_date, normalized, force, session, is_backtest, owns_session=True
        )


async def _run_strategy_impl(
    spec: strat.StrategySpec,
    trade_date: date | None,
    normalized: dict,
    force: bool,
    db: AsyncSession,
    is_backtest: bool,
    *,
    owns_session: bool,
) -> dict:
    """策略执行实现（run 落库 → 计算 → 信号落库 → 更新状态）。

    事务所有权：
      - ``owns_session=True``（db=None 自建会话）：每个阶段自行 commit，失败也要落 failed 记录；
      - ``owns_session=False``（调用方传 db）：**只 flush**，commit/rollback 由调用方负责。
    """
    if trade_date is not None:
        resolved_date = _coerce_date(trade_date)
    else:
        resolved_date = await latest_ready_trade_date(db, spec.code)
        if resolved_date is None:
            resolved_date = await latest_trade_date(db)
    version = await data_version(db, resolved_date)

    if not force:
        cached = await get_cached_run(spec.code, resolved_date, normalized, db)
        if cached is not None:
            return cached

    run_id = str(uuid.uuid4())
    run = StgRun(
        run_id=run_id,
        algo_code=spec.code,
        algo_name=spec.name,
        trade_date=resolved_date,
        params=normalized,
        universe_count=0,
        signal_count=0,
        data_version=version,
        is_backtest=bool(is_backtest),
        status="running",
    )
    db.add(run)
    if owns_session:
        await db.commit()  # 先落 running 记录：即使后续崩溃也留痕（D3 硬要求）
    else:
        await db.flush()

    started = time.perf_counter()
    try:
        candidates = await strat.build_candidates(spec.code, resolved_date, normalized, db)
        universe_count = int(candidates.attrs.get("universe_count", len(candidates)))
        signals = strat.rank_signals(spec.code, candidates, normalized)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        items = _items_from_frame(signals)

        rows: list[StgSignal] = []
        for record in signals.to_dict("records") if len(signals) else []:
            metrics = strat.jsonable(record.get("metrics") or {}) or {}
            rows.append(
                StgSignal(
                    run_id=run_id,
                    code=str(record.get("code")),
                    name=strat.jsonable(record.get("name")),
                    rank=int(record.get("rank") or 0),
                    score=strat.jsonable(record.get("score")),
                    metrics=metrics,
                )
            )
        if rows:
            db.add_all(rows)

        run.universe_count = universe_count
        run.signal_count = len(items)
        run.elapsed_ms = elapsed_ms
        run.data_version = version
        run.status = "success"
        if owns_session:
            await db.commit()
        else:
            await db.flush()

        logger.info(
            "策略执行完成 algo=%s date=%s run_id=%s universe=%d signals=%d elapsed=%dms",
            spec.code, resolved_date, run_id, universe_count, len(items), elapsed_ms,
        )
        return _assemble(run, spec, items, from_cache=False)

    except Exception as exc:  # noqa: BLE001 - 失败也要留 run 记录再抛出
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.exception(
            "策略执行失败 algo=%s date=%s run_id=%s", spec.code, resolved_date, run_id
        )
        if owns_session:
            try:
                await db.rollback()
            except Exception:  # pragma: no cover - 会话已损坏时忽略
                logger.exception("回滚失败 run_id=%s", run_id)
            try:
                await db.execute(
                    update(StgRun)
                    .where(StgRun.run_id == run_id)
                    .values(status="failed", error=str(exc)[:2000], elapsed_ms=elapsed_ms)
                )
                await db.commit()
            except Exception:  # pragma: no cover - 更新失败不能掩盖原始异常
                logger.exception("写入失败 run 状态失败 run_id=%s", run_id)
                try:
                    await db.rollback()
                except Exception:
                    pass
        else:
            # 只 flush：若会话已被底层错误破坏则忽略（事务由调用方回滚）
            try:
                await db.execute(
                    update(StgRun)
                    .where(StgRun.run_id == run_id)
                    .values(status="failed", error=str(exc)[:2000], elapsed_ms=elapsed_ms)
                )
                await db.flush()
            except Exception:  # pragma: no cover
                logger.warning("外部事务中写入 failed run 状态失败（由调用方回滚）run_id=%s", run_id)
        raise


# =====================================================================
# run 查询
# =====================================================================

async def get_run(run_id: str) -> dict | None:
    """按 run_id 取单次 run 明细（契约 5.3）。"""
    if not run_id:
        raise InvalidParamError("run_id 不能为空")
    async with session_scope() as db:
        run = await db.get(StgRun, str(run_id))
        if run is None:
            return None
        try:
            spec = strat.get_spec(run.algo_code)
        except UnknownAlgoError:
            spec = strat.StrategySpec(
                code=run.algo_code,
                name=run.algo_name or run.algo_code,
                params_schema=[],
                defaults={},
            )
        signals = await _load_signal_rows(db, run.run_id)
        return _assemble(run, spec, _items_from_signals(signals), from_cache=True)


async def list_runs(
    algo_code: str,
    limit: int = 10,
    include_backtest: bool = False,
) -> list[dict]:
    """历史 run 列表（契约 5.3，不含信号明细）。

    默认**排除回测 run**（``is_backtest=True``），因为前端"历史 run"下拉与
    个股页"最近信号"只应展示实盘/日常选股结果；需要回测 run 时显式传
    ``include_backtest=True``。
    """
    spec = strat.get_spec(algo_code)
    try:
        count = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        raise InvalidParamError(f"limit 必须是整数，收到: {limit!r}") from None

    statement = select(StgRun).where(StgRun.algo_code == spec.code)
    if not include_backtest:
        statement = statement.where(StgRun.is_backtest.is_(False))
    statement = statement.order_by(StgRun.created_at.desc()).limit(count)

    async with session_scope() as db:
        result = await db.execute(statement)
        runs = list(result.scalars().all())

    return [
        {
            "run_id": run.run_id,
            "algo": spec.code,
            "algo_name": run.algo_name or spec.name,
            "trade_date": run.trade_date.isoformat() if run.trade_date else None,
            "created_at": _iso(run.created_at),
            "elapsed_ms": int(run.elapsed_ms) if run.elapsed_ms is not None else None,
            "universe_count": int(run.universe_count or 0),
            "signal_count": int(run.signal_count or 0),
            "data_version": run.data_version,
            "params": strat.jsonable(run.params or {}),
            "status": run.status,
            "error": run.error,
            "is_backtest": bool(run.is_backtest),
        }
        for run in runs
    ]


__all__ = [
    "params_fingerprint",
    "data_version",
    "latest_trade_date",
    "run_strategy",
    "get_cached_run",
    "get_run",
    "list_runs",
]
