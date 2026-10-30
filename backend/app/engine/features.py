"""
指标批量计算与落库（方案 C1）
==============================
全市场选股要做到"秒级"，就不能在查询时现算指标：本模块把 MA/RSI/MACD/BOLL/ATR/
动量/波动/量比 预计算进 `fact_feature_daily`，选股与个股详情只查表。

设计要点（对应方案 C1 要求）：
  1. **向量化**：按 code 分组后整块调用 `engine.indicators.compute_features`，
     绝不逐行 `.iterrows()`；`compute_frames` 是纯函数，可脱离数据库单测。
  2. **增量**：只写 `[start, end]` 区间，且用 `ON CONFLICT DO UPDATE` 幂等 upsert，
     重复跑同一区间不会产生重复行。
  3. **预热窗口**：区间起点还要往前多读 `INDICATOR_WARMUP_DAYS` 个自然日，
     否则增量算一天的 MA60/momentum_60 全是 NaN（错误的"数据不足"）。
  4. **批量提交**：每 `WRITE_CHUNK_ROWS`（800）行一个 INSERT + commit，
     避免全市场回补把事务拖到几个小时、锁表、以及内存里堆几百万行 dict。
  5. **外部事务复用**：`db` 参数用于 pipeline 共享 session（只 flush 不 commit）。

单位约定：volume=股，amount=元，指标比率均为百分数（%），与 `fact_stock_daily` 一致。
"""
from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Any, Iterator

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.fact import FactFeatureDaily, FactStockDaily
from app.engine._db import commit_or_flush, session_ctx
from app.engine.indicators import FEATURE_COLUMNS, compute_features

logger = logging.getLogger(__name__)

# 指标预热窗口（自然日）：MA60/momentum_60 需要 60 个交易日，留 ~2 倍冗余；
# 只影响"读多少历史"，不影响写入区间（写入仍严格限制在 [start, end]）。
INDICATOR_WARMUP_DAYS = 240

# 单批最多读入多少只股票（调用方传入的 batch_size 是上限；
# 大区间回补时会自动缩小，防止一只股票 5 年日线 × 800 只把内存打爆）
DEFAULT_CODE_BATCH = 800
MAX_FRAME_ROWS_PER_BATCH = 200_000

# 每条 INSERT ... ON CONFLICT 的**行数上限**，也是自建 session 的 commit 粒度。
# 实际行数还要受 asyncpg 绑定参数上限约束（32767），见 `_chunk_rows`：
# 40 个特征列 × 800 行 ≈ 3.4 万参数，会直接报 "query arguments cannot exceed 32767"。
WRITE_CHUNK_ROWS = 800
_MAX_BIND_PARAMS = 30_000  # 留出余量给索引列与语句自身开销


def _chunk_rows() -> int:
    """按列数折算单条 INSERT 的安全行数（列增加时自动缩小）。"""
    params_per_row = len(FEATURE_COLUMNS) + 2  # + code / trade_date
    return max(1, min(WRITE_CHUNK_ROWS, _MAX_BIND_PARAMS // params_per_row))

# 从 fact_stock_daily 读取的原始列
_DAILY_COLUMNS = ("open", "high", "low", "close", "volume", "amount")


# ============================================================
# 纯函数：批量计算
# ============================================================

def compute_frames(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """批量计算多只股票的技术指标（**纯函数，不触库**，可直接单测）。

    参数:
        frames: {code: 日线 DataFrame}；每个 DataFrame 必须包含列
                `trade_date / open / high / low / close / volume / amount`，
                行序不强制，但必须包含历史（暖机段），建议按 trade_date 升序传入。
                输入为空的代码会被跳过。

    返回:
        {code: 特征 DataFrame}，列 = `trade_date + indicators.FEATURE_COLUMNS`。
        某只股票缺列/数值异常时记录 warning 并跳过，**不中断整个全市场任务**。

    注意:
        - 输出行序与输入一致（内部仅在乱序时排序，保证 rolling 指标语义正确）；
        - 不检查 60 行下限：不足 60 行的股票照算，MA60 等列为 NaN（落库为 NULL），
          由上游/策略层决定是否过滤次新股，而不是把行整个丢掉。
    """
    result: dict[str, pd.DataFrame] = {}
    keep_columns = ["trade_date", *FEATURE_COLUMNS]

    for code, frame in frames.items():
        if frame is None or len(frame) == 0:
            continue
        try:
            ordered = frame
            trade_dates = ordered["trade_date"]
            if not trade_dates.is_monotonic_increasing:
                ordered = ordered.sort_values("trade_date", kind="mergesort")
            ordered = ordered.reset_index(drop=True)
            features = compute_features(ordered)
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("计算 %s 技术指标失败，已跳过: %s", code, exc)
            continue

        missing = [col for col in keep_columns if col not in features.columns]
        if missing:
            logger.warning("计算 %s 技术指标缺列 %s，已跳过", code, missing)
            continue
        result[str(code)] = features[keep_columns].reset_index(drop=True)
    return result


# ============================================================
# 内部：读库 / 组织行 / upsert
# ============================================================

def _as_db_value(value: Any) -> Any:
    """pandas 标量 → 可落库的 Python 值：NaN/Inf → None，numpy bool → bool。"""
    if value is None:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _frame_to_rows(code: str, features: pd.DataFrame,
                   start: date, end: date) -> list[dict]:
    """把单只股票的区间特征行转成待 upsert 的 dict 列表。"""
    if features.empty:
        return []
    mask = (features["trade_date"] >= start) & (features["trade_date"] <= end)
    subset = features.loc[mask]
    if subset.empty:
        return []

    rows: list[dict] = []
    # to_dict("records") 是 C 层批量转换，不是逐行 iterrows；只在本区间（通常 1 行/天）上调用
    for record in subset.to_dict("records"):
        row: dict = {"code": code, "trade_date": record["trade_date"]}
        for column in FEATURE_COLUMNS:
            row[column] = _as_db_value(record.get(column))
        rows.append(row)
    return rows


def _iter_feature_rows(computed: dict[str, pd.DataFrame], start: date,
                       end: date) -> Iterator[list[dict]]:
    """把多只股票的特征按安全行数切块产出，避免一次性构造巨大列表。"""
    chunk_rows = _chunk_rows()
    buffer: list[dict] = []
    for code, features in computed.items():
        buffer.extend(_frame_to_rows(code, features, start, end))
        while len(buffer) >= chunk_rows:
            yield buffer[:chunk_rows]
            buffer = buffer[chunk_rows:]
    if buffer:
        yield buffer


def _effective_code_batch(start: date, end: date, batch_size: int) -> int:
    """按区间长度自适应缩小代码批大小，控制单批读入内存的日线行数。

    估算：区间交易日 ≈ 自然日 × 5/7，再加热机窗口约 170 个交易日。
    单批目标不超过 `MAX_FRAME_ROWS_PER_BATCH` 行；单日增量时仍用传入的 batch_size。
    """
    span_days = max(1, (end - start).days + 1)
    est_rows_per_code = int(span_days * 5 / 7) + 170
    max_codes = max(20, MAX_FRAME_ROWS_PER_BATCH // max(1, est_rows_per_code))
    return max(1, min(int(batch_size), max_codes))


async def _list_daily_codes(session: AsyncSession, start: date,
                            end: date) -> list[str]:
    """区间内有日线数据的股票代码（去重升序）。"""
    stmt = (
        select(FactStockDaily.code)
        .where(FactStockDaily.trade_date >= start, FactStockDaily.trade_date <= end)
        .distinct()
        .order_by(FactStockDaily.code)
    )
    rows = (await session.execute(stmt)).all()
    return [str(row[0]) for row in rows if row[0] is not None]


async def _load_daily_frames(session: AsyncSession, codes: list[str],
                             start: date, end: date) -> dict[str, pd.DataFrame]:
    """批量读日线并按 code 分组为 DataFrame（trade_date 升序）。"""
    if not codes:
        return {}
    columns = [FactStockDaily.code, FactStockDaily.trade_date]
    columns.extend(getattr(FactStockDaily, name) for name in _DAILY_COLUMNS)
    stmt = (
        select(*columns)
        .where(
            FactStockDaily.code.in_(codes),
            FactStockDaily.trade_date >= start,
            FactStockDaily.trade_date <= end,
        )
        .order_by(FactStockDaily.code, FactStockDaily.trade_date)
    )
    raw_rows = (await session.execute(stmt)).all()
    if not raw_rows:
        return {}

    # 显式转 tuple：SQLAlchemy Row 直接交给 pandas 在部分版本会被当成 Mapping
    records = [tuple(row) for row in raw_rows]
    frame = pd.DataFrame(records, columns=["code", "trade_date", *_DAILY_COLUMNS])
    for column in _DAILY_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    frames: dict[str, pd.DataFrame] = {}
    for code, group in frame.groupby("code", sort=False):
        frames[str(code)] = group.drop(columns=["code"]).reset_index(drop=True)
    return frames


async def _upsert_feature_chunks(session: AsyncSession,
                                 chunks: Iterator[list[dict]],
                                 external: bool) -> int:
    """逐块执行 `INSERT ... ON CONFLICT (code, trade_date) DO UPDATE`。"""
    written = 0
    for chunk in chunks:
        if not chunk:
            continue
        stmt = pg_insert(FactFeatureDaily).values(chunk)
        update_columns = {column: stmt.excluded[column] for column in FEATURE_COLUMNS}
        update_columns["updated_at"] = func.now()
        stmt = stmt.on_conflict_do_update(
            index_elements=["code", "trade_date"],
            set_=update_columns,
        )
        await session.execute(stmt)
        await commit_or_flush(session, external)
        written += len(chunk)
    return written


# ============================================================
# 对外 API
# ============================================================

async def compute_features_for_range(
    start: date,
    end: date | None = None,
    codes: list[str] | None = None,
    batch_size: int = DEFAULT_CODE_BATCH,
    db: AsyncSession | None = None,
) -> int:
    """批量计算并 upsert `[start, end]` 区间内所有股票的技术指标。

    参数:
        start: 起始交易日（含）
        end: 结束交易日（含）；None 时默认今天
        codes: 指定股票代码；None 表示区间内有日线数据的全部股票
        batch_size: 每批处理的股票数上限（800 只）；大区间回补内部还会自适应缩小
        db: 外部共享 session（pipeline 传）；None 时自建事务

    返回:
        int: 实际写入（upsert）的行数。

    行为:
        - 读库窗口 = `[start - INDICATOR_WARMUP_DAYS, end]`，保证区间首日的
          MA60/momentum_60 等指标有足够历史，不是 NaN；
        - 只写 `[start, end]` 区间，区间外行不落库；
        - 重复调用幂等（upsert），可用于回补与重算。
    """
    end_date = end or date.today()
    if end_date < start:
        raise ValueError(f"结束日期 {end_date} 早于开始日期 {start}")
    if not codes and end_date == start:
        logger.info("compute_features_for_range: 单日任务 %s", start)

    warmup_start = start - timedelta(days=INDICATOR_WARMUP_DAYS)
    effective_batch = _effective_code_batch(start, end_date, batch_size)
    total_written = 0

    async with session_ctx(db) as session:
        code_list = [str(code) for code in codes] if codes else await _list_daily_codes(
            session, start, end_date
        )
        if not code_list:
            logger.warning("区间 %s~%s 无日线数据，跳过指标计算", start, end_date)
            return 0

        logger.info(
            "指标批量计算: %s~%s, 股票 %d 只, 每批 %d 只",
            start, end_date, len(code_list), effective_batch,
        )
        for offset in range(0, len(code_list), effective_batch):
            batch = code_list[offset:offset + effective_batch]
            frames = await _load_daily_frames(session, batch, warmup_start, end_date)
            if not frames:
                continue
            computed = compute_frames(frames)
            chunks = _iter_feature_rows(computed, start, end_date)
            total_written += await _upsert_feature_chunks(
                session, chunks, external=db is not None
            )

    logger.info("指标批量计算完成: %s~%s, 写入 %d 行", start, end_date, total_written)
    return total_written


async def compute_features_for_date(
    trade_date: date,
    db: AsyncSession | None = None,
    codes: list[str] | None = None,
) -> int:
    """计算并落库单个交易日全市场（或指定股票）的技术指标。

    pipeline 的 `features` 任务入口；等价于
    `compute_features_for_range(trade_date, trade_date, codes, db=db)`。

    参数:
        trade_date: 交易日
        db: 外部共享 session（pipeline 传）；None 时自建事务
        codes: 指定代码；None 表示全市场

    返回:
        int: 写入行数。
    """
    return await compute_features_for_range(
        trade_date, trade_date, codes=codes, db=db
    )


__all__ = [
    "INDICATOR_WARMUP_DAYS",
    "DEFAULT_CODE_BATCH",
    "WRITE_CHUNK_ROWS",
    "compute_frames",
    "compute_features_for_range",
    "compute_features_for_date",
]
