"""
板块龙头识别（方案 C5：接入原 `services/leader_identifier.py` 的 223 行零引用代码）
====================================================================================
旧问题：`leader_identifier.py` 写好了却从未被分析链路调用，`strength_leaders` 永远为空。
V5 把识别逻辑迁到 `engine/leaders.py`，由 `engine/sector_analysis.score_all_sectors`
全量调用并写入 `sector_score.strength_leaders / sentiment_leaders`。

两类龙头（纯规则、不依赖 AI）：
  1. **实力龙头 strength**：市值大 + 成交活跃（机构抱团/价值资金）
       先按市值取 Top 10，再在其中按成交额取前 `top_n`；
  2. **情绪龙头 sentiment**：小盘 + 涨幅 × 换手率高（游资短线炒作）
       过滤市值区间 `min_cap < market_cap < max_cap`，按 `pct_chg × turnover_rate` 排序；
       全板块下跌时退化为"最抗跌"（按涨幅最接近 0 排序），并标记 `fallback=True`。

**单位约定（V5 硬约束，旧代码注释写"亿元"但实际传入元，属误导）**：
  - 输入的 `market_cap` / `float_cap` / `amount` 一律为**元**；
  - 输出同时给 `market_cap`（元）与 `market_cap_yi`（亿元，展示用），
    `amount`（元）与 `amount_yi`（亿元），并统一 round(2)；
  - 情绪龙头的市值阈值参数也是**元**：默认 50 亿 ~ 2000 亿 = 5e9 ~ 2e11。
"""
from __future__ import annotations

import logging
import math
from datetime import date
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import POOL_BOARDS, STATUS_NORMAL
from app.db.models.dim import DimSectorMember, DimStock
from app.db.models.fact import FactStockDaily
from app.engine._db import session_ctx

logger = logging.getLogger(__name__)

# 实力龙头候选池：先按市值取前 10，再按成交额排序（沿用旧版可解释规则）
STRENGTH_MARKET_CAP_POOL = 10


# ============================================================
# 内部工具
# ============================================================

def _to_float(value: Any) -> float | None:
    """宽松转 float：None/NaN/非法 → None。"""
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _pick(stock: dict, *keys: str) -> Any:
    """按候选键取第一个非空值（兼容旧字段名 change_pct 与 V5 字段 pct_chg）。"""
    for key in keys:
        if key in stock and stock[key] is not None:
            return stock[key]
    return None


def _code(stock: dict) -> str:
    return str(_pick(stock, "code", "stock_code") or "")


def _name(stock: dict) -> str:
    return str(_pick(stock, "name", "stock_name") or _code(stock))


def _market_cap(stock: dict) -> float | None:
    """市值（元）。"""
    return _to_float(_pick(stock, "market_cap", "total_cap", "market_cap_yuan"))


def _amount(stock: dict) -> float | None:
    """成交额（元）。优先当日 amount，其次 20 日均额。"""
    return _to_float(_pick(stock, "amount", "amount_ma20", "avg_amount"))


def _pct_chg(stock: dict) -> float | None:
    """涨跌幅（%）。"""
    return _to_float(_pick(stock, "pct_chg", "change_pct"))


def _turnover(stock: dict) -> float | None:
    """换手率（%）。"""
    return _to_float(_pick(stock, "turnover_rate", "turnover"))


def _rank_of(sorted_stocks: list[dict], target: dict) -> int:
    """在已排序列表里找目标股票的 1-based 排名（按 code 匹配，重复时取第一个）。"""
    target_code = _code(target)
    for index, item in enumerate(sorted_stocks, 1):
        if _code(item) == target_code:
            return index
    return len(sorted_stocks)


# ============================================================
# 实力龙头
# ============================================================

def identify_strength_leaders(stocks: list[dict], top_n: int = 3) -> list[dict]:
    """识别实力龙头：市值大 + 成交活跃。

    算法（旧版规则保留）：
      1. 过滤有有效市值（`market_cap` > 0，单位元）的股票；
      2. 按市值降序取前 `STRENGTH_MARKET_CAP_POOL`（默认 10）只；
      3. 在这 10 只里按成交额降序取前 `top_n` 只。

    经济含义："板块里真正的带头大哥"——先看体量（市值），再看谁有资金在交易
    （成交额），避免选出"大而僵尸"的股票。

    数据局限：
      - 市值用总市值，未剔除限售股；次新股/重组股市值失真；
      - 成交额只反映当日热度，主力资金持续流入需结合 `FactMoneyflow`；
      - 停牌股无当日行情，调用方应只传有行情的成分股。

    参数:
        stocks: 板块成分股，每项至少含 `code`、`market_cap`（元）、`amount`（元），
                可选 `pct_chg`、`name`
        top_n: 返回数量，默认 3

    返回:
        list[dict]；无有效市值时返回空列表。
    """
    if not stocks:
        return []
    valid = [s for s in stocks if (_market_cap(s) or 0.0) > 0.0]
    if not valid:
        logger.info("无有效市值数据，跳过实力龙头识别")
        return []

    by_cap = sorted(valid, key=lambda s: _market_cap(s) or 0.0, reverse=True)
    top_by_cap = by_cap[:STRENGTH_MARKET_CAP_POOL]
    by_amount = sorted(top_by_cap, key=lambda s: _amount(s) or 0.0, reverse=True)
    leaders = by_amount[:top_n]

    result: list[dict] = []
    for stock in leaders:
        cap = _market_cap(stock) or 0.0
        amount = _amount(stock) or 0.0
        cap_rank = _rank_of(by_cap, stock)
        amount_rank = _rank_of(by_amount, stock)
        result.append({
            "code": _code(stock),
            "name": _name(stock),
            # 单位：元（V5 统一口径）；另给亿元展示字段
            "market_cap": round(cap, 2),
            "market_cap_yi": round(cap / 1e8, 2),
            "amount": round(amount, 2),
            "amount_yi": round(amount / 1e8, 2),
            "change_pct": _pct_chg(stock),
            "type": "strength",
            "rank_text": f"市值第{cap_rank} + 成交额第{amount_rank}",
        })
    return result


# ============================================================
# 情绪龙头
# ============================================================

def identify_sentiment_leaders(
    stocks: list[dict],
    top_n: int = 2,
    min_cap: float = 5e9,
    max_cap: float = 2e11,
) -> list[dict]:
    """识别情绪龙头：小盘 + 涨幅 × 换手率高。

    算法（旧版规则保留并修单位）：
      1. 过滤 `min_cap < 市值 < max_cap`（**单位元**，默认 50 亿 ~ 2000 亿），
         排除"拉不动的大象"和"庄股嫌疑的微盘"；
      2. 优先只看上涨股，按 `涨幅 × 换手率`（热度）降序取前 `top_n`；
      3. 若全板块下跌 → 退化为"最抗跌"：按涨幅降序取候选，并标记 `fallback=True`，
         rank_text 前缀"全线下跌·抗跌第X"，让前端能解释为什么选的是跌得最少的。

    经济含义：情绪龙头是短线资金的"旗手"，涨幅 × 换手率越高说明承接越强。

    数据局限：
      - 涨幅 × 换手率无法区分"主升浪"与"出货放量"，需结合资金流方向；
      - 20% 涨跌幅（创业板/科创板）与 10%（主板）直接比热度会偏向双创；
      - 市值阈值对北交所（3 亿以下小盘）不适用，北交所板块应单独放宽下限。

    参数:
        stocks: 板块成分股，至少含 `code`、`market_cap`（元）、`pct_chg`（%）、
                `turnover_rate`（%）
        top_n: 返回数量，默认 2
        min_cap: 市值下界（元），默认 5e9（50 亿）
        max_cap: 市值上界（元），默认 2e11（2000 亿）

    返回:
        list[dict]；无符合市值区间的候选时返回空列表。
    """
    if not stocks:
        return []

    filtered: list[dict] = []
    for stock in stocks:
        cap = _market_cap(stock)
        if cap is None or not (min_cap < cap < max_cap):
            continue
        filtered.append(stock)
    if not filtered:
        logger.info("无符合市值区间(%.0f~%.0f元)的情绪龙头候选", min_cap, max_cap)
        return []

    def _heat(stock: dict) -> float:
        return (_pct_chg(stock) or 0.0) * (_turnover(stock) or 0.0)

    positive = [s for s in filtered if (_pct_chg(s) or 0.0) > 0.0]
    fallback = not positive
    if positive:
        candidates = positive
    else:
        # 全跌：先取最抗跌的若干只再比热度，避免"跌得最多但换手极高"被误选
        candidates = sorted(
            filtered,
            key=lambda s: _pct_chg(s) if _pct_chg(s) is not None else -999.0,
            reverse=True,
        )[: max(top_n * 2, 5)]

    by_heat = sorted(candidates, key=_heat, reverse=True)
    leaders = by_heat[:top_n]

    by_pct = sorted(
        candidates,
        key=lambda s: _pct_chg(s) if _pct_chg(s) is not None else -999.0,
        reverse=True,
    )
    by_turnover = sorted(
        candidates,
        key=lambda s: _turnover(s) if _turnover(s) is not None else -1.0,
        reverse=True,
    )

    result: list[dict] = []
    for stock in leaders:
        cap = _market_cap(stock) or 0.0
        pct_rank = _rank_of(by_pct, stock)
        turnover_rank = _rank_of(by_turnover, stock)
        rank_prefix = "全线下跌·抗跌第" if fallback else "涨幅第"
        result.append({
            "code": _code(stock),
            "name": _name(stock),
            "change_pct": _pct_chg(stock),
            "turnover_rate": _turnover(stock),
            # 单位：元（V5 统一口径）
            "market_cap": round(cap, 2),
            "market_cap_yi": round(cap / 1e8, 2),
            "type": "sentiment",
            "heat_score": round(_heat(stock), 2),
            "fallback": fallback,
            "rank_text": f"{rank_prefix}{pct_rank} × 换手率第{turnover_rank}",
        })
    return result


# ============================================================
# 综合入口
# ============================================================

def identify_leaders(stocks: list[dict]) -> dict:
    """一次识别两类龙头。

    参数:
        stocks: 板块成分股列表（`sector_members_with_quotes` 的返回值即可）

    返回:
        `{"strength_leaders": [...], "sentiment_leaders": [...]}`
    """
    return {
        "strength_leaders": identify_strength_leaders(stocks),
        "sentiment_leaders": identify_sentiment_leaders(stocks),
    }


# ============================================================
# 板块成分 + 当日行情（不联网）
# ============================================================

async def sector_members_with_quotes(
    sector_code: str,
    trade_date: date,
    db: AsyncSession | None = None,
) -> list[dict]:
    """取板块当日有效成分股 + 当日行情（**只读本地库，不联网**）。

    成分表是 SCD2 慢变化维，必须按"当日有效区间"取：
        `effective_from <= trade_date AND (effective_to IS NULL OR effective_to > trade_date)`
    直接用全量成分会把"后来才纳入"的股票算进历史板块，是典型未来函数。

    参数:
        sector_code: 板块代码
        trade_date: 交易日
        db: 外部共享 session（pipeline）；None 时自建会话

    返回:
        list[dict]，字段：
        code / name / sector_code / sector_name / industry_l1 /
        close / pct_chg / turnover_rate / amount / market_cap / float_cap
        （行情缺失的停牌股也会返回，其行情字段为 None；市值单位：元）
    """
    stmt = (
        select(
            DimSectorMember.stock_code,
            DimSectorMember.sector_name,
            DimStock.name.label("stock_name"),
            DimStock.industry_l1,
            FactStockDaily.close,
            FactStockDaily.pct_chg,
            FactStockDaily.turnover_rate,
            FactStockDaily.amount,
            FactStockDaily.market_cap,
            FactStockDaily.float_cap,
        )
        .select_from(DimSectorMember)
        .outerjoin(DimStock, DimStock.code == DimSectorMember.stock_code)
        .outerjoin(
            FactStockDaily,
            and_(
                FactStockDaily.code == DimSectorMember.stock_code,
                FactStockDaily.trade_date == trade_date,
            ),
        )
        .where(
            DimSectorMember.sector_code == sector_code,
            DimSectorMember.effective_from <= trade_date,
            or_(
                DimSectorMember.effective_to.is_(None),
                DimSectorMember.effective_to > trade_date,
            ),
            DimStock.status == STATUS_NORMAL,
            DimStock.board.in_(POOL_BOARDS),
        )
    )

    async with session_ctx(db) as session:
        rows = (await session.execute(stmt)).all()

    members: list[dict] = []
    for row in rows:
        member = {
            "code": str(row.stock_code),
            "name": str(row.stock_name) if row.stock_name else str(row.stock_code),
            "sector_code": sector_code,
            "sector_name": row.sector_name,
            "industry_l1": row.industry_l1,
            "close": _to_float(row.close),
            "pct_chg": _to_float(row.pct_chg),
            "turnover_rate": _to_float(row.turnover_rate),
            "amount": _to_float(row.amount),
            "market_cap": _to_float(row.market_cap),
            "float_cap": _to_float(row.float_cap),
        }
        members.append(member)

    # 稳定排序：市值降序（无市值的排最后），保证同一数据多次运行结果一致
    members.sort(key=lambda m: (m["market_cap"] is not None, m["market_cap"] or 0.0),
                 reverse=True)
    return members


__all__ = [
    "STRENGTH_MARKET_CAP_POOL",
    "identify_strength_leaders",
    "identify_sentiment_leaders",
    "identify_leaders",
    "sector_members_with_quotes",
]
