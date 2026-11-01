"""纸面组合跟踪（V6 T11）
=========================
一策略一组合，每天把 ``start_date → 最新行情日`` 整段重算并覆盖 ``paper_nav``，
不做增量持仓状态机（增量会累积除权/停牌/名单变更漂移，且无法解释）。

口径与回测完全一致：同一个 ``run_backtest`` 引擎、同一套成交规则；
差异只在于起点是策略首个实盘 run 的交易日。每天的净值行同时保存收盘持仓
与下一交易日待执行清单（``pending_orders``），让收盘后就知道明天做什么。
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import BACKTEST, PAPER_ENABLED_ALGOS
from app.core.errors import DataUnavailableError, NotFoundError
from app.db.models.strategy import BacktestRun, PaperNav, PaperPortfolio, StgRun
from app.db.session import AsyncSessionLocal
from app.pipeline import advisory_unlock, try_advisory_lock
from app.strategy import runner as stg_runner
from app.strategy.backtest import BacktestParams, build_pending_orders, run_backtest
from app.strategy.strategies import SPECS, get_spec

logger = logging.getLogger(__name__)

CALIBER = (
    "后复权估值；佣金/印花税/滑点/整手与回测引擎完全一致；"
    "起点为策略首个实盘 run 交易日，不代表可交易收益。"
)


def enabled_algos() -> list[str]:
    """PAPER_ENABLED_ALGOS 为空 = 全部现存策略；未知代码启动时过滤。"""
    configured = [code for code in PAPER_ENABLED_ALGOS if code in SPECS]
    if PAPER_ENABLED_ALGOS and not configured:
        logger.warning("PAPER_ENABLED_ALGOS 无有效策略: %s", PAPER_ENABLED_ALGOS)
    return configured or sorted(SPECS)


def is_enabled(algo_code: str) -> bool:
    return algo_code in enabled_algos()


async def _first_live_run_date(db: AsyncSession, algo_code: str) -> date | None:
    """策略首个成功实盘 run 的交易日（不含回测 run）。"""
    return (await db.execute(
        select(func.min(StgRun.trade_date))
        .where(StgRun.algo_code == algo_code)
        .where(StgRun.status == "success")
        .where(StgRun.is_backtest.is_(False))
    )).scalar_one_or_none()


async def _get_portfolio(db: AsyncSession, algo_code: str) -> PaperPortfolio | None:
    return (await db.execute(
        select(PaperPortfolio).where(PaperPortfolio.algo_code == algo_code)
    )).scalar_one_or_none()


def _paper_params(spec: Any) -> tuple[dict, BacktestParams]:
    bt = BacktestParams.from_config()
    strategy = dict(spec.defaults)
    strategy["limit"] = max(int(strategy.get("limit") or 0), int(bt.max_positions))
    return {"strategy": strategy, "backtest": bt.as_snapshot()}, bt


async def _latest_signal_pending(
    db: AsyncSession,
    *,
    algo_code: str,
    latest_day: date,
    trading_days: list[date],
    rebalance_days: int,
    strategy_params: dict,
    bt_params: BacktestParams,
    holdings: list[dict],
) -> list[dict]:
    """最新一天恰好是调仓信号日时，用当日信号生成明日待执行清单。

    与回测引擎同一 ``build_pending_orders`` 判定；T+1 行情尚未产生，
    ``fillable`` 返回 null，由前端提示“待开盘校验”。
    """
    if not trading_days or latest_day != trading_days[-1]:
        return []
    if (len(trading_days) - 1) % max(int(rebalance_days), 1) != 0:
        return []
    try:
        result = await stg_runner.run_strategy(
            algo_code, latest_day, strategy_params, force=False, db=db, is_backtest=True,
        )
    except Exception:  # noqa: BLE001 - 明日清单失败不能拖垮整段净值
        logger.exception("纸面组合生成明日信号失败 algo=%s date=%s", algo_code, latest_day)
        return []
    targets = list(result.get("data") or [])[: bt_params.max_positions]
    return build_pending_orders(holdings, targets, bt_params)


#: 人工回测与纸面组合必须完全一致的参数（否则差值混入参数差异，不能归因于执行）
_BT_MATCH_KEYS = (
    "rebalance_days", "max_positions", "benchmark", "commission", "stamp_tax",
    "slippage", "min_commission", "initial_cash", "exclude_st", "exclude_new_days",
)


def _same_params(expected_bt: dict, expected_strategy: dict, row_params: dict | None) -> bool:
    """人工回测的落库参数是否与纸面组合完全一致（数值容忍 JSON 的 int/float 差异）。"""
    if not row_params:
        return False
    for key in _BT_MATCH_KEYS:
        if key not in row_params:
            return False
        left, right = expected_bt.get(key), row_params.get(key)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            if abs(float(left) - float(right)) > 1e-12:
                return False
        elif left != right:
            return False
    return dict(expected_strategy or {}) == dict(row_params.get("strategy") or {})


async def _vs_backtest(
    db: AsyncSession,
    *,
    algo_code: str,
    start: date,
    end: date,
    metrics: dict,
    strategy_params: dict,
    bt_params: BacktestParams,
) -> dict | None:
    """与**同区间、同参数**最新一次人工回测的差值（排除 paper 自己产生的记录）。

    只按区间匹配会把"30 日调仓/5 只持仓"的回测当成纸面策略的回测，
    差值里混入参数差异、无法回答"执行差多少"，因此这里同时比对参数快照。
    """
    candidates = (await db.execute(
        select(BacktestRun)
        .where(BacktestRun.algo_code == algo_code)
        .where(BacktestRun.start_date == start)
        .where(BacktestRun.end_date == end)
        .where(BacktestRun.status == "success")
        .where(or_(
            BacktestRun.params["source"].astext.is_(None),
            BacktestRun.params["source"].astext != "paper",
        ))
        .order_by(BacktestRun.created_at.desc())
        .limit(20)
    )).scalars().all()
    expected_bt = bt_params.as_snapshot()
    manual = next(
        (row for row in candidates
         if _same_params(expected_bt, strategy_params, row.params)),
        None,
    )
    if manual is None:
        return None

    def _diff(paper_value: Any, backtest_value: Any) -> float | None:
        if paper_value is None or backtest_value is None:
            return None
        return round(float(paper_value) - float(backtest_value), 6)

    other = manual.metrics or {}
    return {
        "bt_id": manual.bt_id,
        "total_return": other.get("total_return"),
        "excess_return": other.get("excess_return"),
        "max_drawdown": other.get("max_drawdown"),
        "total_return_diff": _diff(metrics.get("total_return"), other.get("total_return")),
        "excess_return_diff": _diff(metrics.get("excess_return"), other.get("excess_return")),
        "max_drawdown_diff": _diff(metrics.get("max_drawdown"), other.get("max_drawdown")),
        "as_of": manual.end_date.isoformat(),
    }


async def sync_paper_portfolio(db: AsyncSession, algo_code: str) -> dict:
    """整体重算一个策略的纸面组合；调用方负责 commit/rollback。"""
    spec = get_spec(algo_code)
    portfolio = await _get_portfolio(db, algo_code)
    if portfolio is None:
        start = await _first_live_run_date(db, algo_code)
        if start is None:
            raise DataUnavailableError(
                f"策略 {algo_code} 尚无成功实盘 run，无法确定建仓日",
                next_action="POST /api/v1/admin/ingest/algos",
                detail={"algo": algo_code},
            )
        params, bt = _paper_params(spec)
        portfolio = PaperPortfolio(
            portfolio_id=str(uuid.uuid4()),
            algo_code=algo_code,
            name=spec.name,
            start_date=start,
            params=params,
            rebalance_days=bt.rebalance_days,
            max_positions=bt.max_positions,
            initial_capital=bt.initial_cash,
            status="waiting",
        )
        db.add(portfolio)
        await db.flush()
    else:
        params = dict(portfolio.params or {})
        bt = BacktestParams.from_dict(params.get("backtest"))
        start = portfolio.start_date or await _first_live_run_date(db, algo_code)
        if start is None:
            portfolio.status = "failed"
            raise DataUnavailableError(
                f"策略 {algo_code} 尚无成功实盘 run，无法确定建仓日",
                next_action="POST /api/v1/admin/ingest/algos",
                detail={"algo": algo_code},
            )
    strategy_params = dict(params.get("strategy") or spec.defaults)

    end = await stg_runner.latest_trade_date(db)
    if start >= end:
        portfolio.status = "waiting"
        await db.flush()
        logger.info(
            "纸面组合等待下一交易日 algo=%s start=%s latest=%s", algo_code, start, end,
        )
        return {
            "algo": algo_code, "status": "waiting", "start_date": start.isoformat(),
            "end_date": end.isoformat(), "rows": 0, "pending_orders": 0,
        }

    snapshots: list[dict] = []
    result = await run_backtest(
        algo_code=algo_code,
        start=start,
        end=end,
        strategy_params=strategy_params,
        bt_params=params.get("backtest"),
        bt_id=params.get("bt_id"),
        snapshots_out=snapshots,
        tag="paper",
    )
    if params.get("bt_id") != result["bt_id"]:
        params["bt_id"] = result["bt_id"]
        portfolio.params = params

    curve = result.get("equity_curve") or []
    if len(snapshots) != len(curve):
        logger.warning(
            "纸面组合快照与曲线长度不一致 algo=%s snapshots=%d curve=%d",
            algo_code, len(snapshots), len(curve),
        )

    trading_days = [date.fromisoformat(point["date"]) for point in curve]
    latest_holdings = snapshots[-1]["holdings"] if snapshots else []
    pending = await _latest_signal_pending(
        db,
        algo_code=algo_code,
        latest_day=trading_days[-1],
        trading_days=trading_days,
        rebalance_days=int(portfolio.rebalance_days or bt.rebalance_days),
        strategy_params=strategy_params,
        bt_params=bt,
        holdings=latest_holdings,
    )
    if snapshots and pending:
        snapshots[-1]["pending_orders"] = pending

    metrics = result.get("metrics") or {}
    vs_backtest = await _vs_backtest(
        db, algo_code=algo_code, start=start, end=end, metrics=metrics,
        strategy_params=strategy_params, bt_params=bt,
    )

    rows: list[dict] = []
    peak = 0.0
    for index, (snapshot, point) in enumerate(zip(snapshots, curve)):
        nav = float(point.get("nav") or 0.0)
        peak = max(peak, nav)
        rows.append({
            "portfolio_id": portfolio.portfolio_id,
            "trade_date": date.fromisoformat(point["date"]),
            "nav": point.get("nav"),
            "benchmark": point.get("benchmark"),
            "drawdown": round(nav / peak - 1.0, 3) if peak else 0.0,
            "holdings": snapshot.get("holdings") or [],
            "cash_ratio": snapshot.get("cash_ratio"),
            "pending_orders": snapshot.get("pending_orders") or [],
            "vs_backtest": vs_backtest if index == len(snapshots) - 1 else None,
        })

    if rows:
        statement = pg_insert(PaperNav).values(rows)
        await db.execute(statement.on_conflict_do_update(
            index_elements=["portfolio_id", "trade_date"],
            set_={
                "nav": statement.excluded.nav,
                "benchmark": statement.excluded.benchmark,
                "drawdown": statement.excluded.drawdown,
                "holdings": statement.excluded.holdings,
                "cash_ratio": statement.excluded.cash_ratio,
                "pending_orders": statement.excluded.pending_orders,
                "vs_backtest": statement.excluded.vs_backtest,
                "updated_at": func.now(),
            },
        ))
        await db.execute(
            delete(PaperNav)
            .where(PaperNav.portfolio_id == portfolio.portfolio_id)
            .where(PaperNav.trade_date > end)
        )

    portfolio.start_date = start
    portfolio.rebalance_days = int(bt.rebalance_days)
    portfolio.max_positions = int(bt.max_positions)
    portfolio.initial_capital = float(bt.initial_cash)
    portfolio.status = "active"
    await db.flush()
    logger.info(
        "纸面组合同步完成 algo=%s %s~%s rows=%d nav=%.4f excess=%.4f pending=%d",
        algo_code, start, end, len(rows),
        float(rows[-1]["nav"] or 0.0) if rows else 0.0,
        float(metrics.get("excess_return") or 0.0),
        len(rows[-1]["pending_orders"]) if rows else 0,
    )
    return {
        "algo": algo_code,
        "status": "active",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "rows": len(rows),
        "nav": rows[-1]["nav"] if rows else None,
        "excess_return": metrics.get("excess_return"),
        "pending_orders": len(rows[-1]["pending_orders"]) if rows else 0,
        "vs_backtest": vs_backtest,
    }


async def _mark_failed(db: AsyncSession, algo_code: str, error: str) -> None:
    portfolio = await _get_portfolio(db, algo_code)
    if portfolio is not None:
        portfolio.status = "failed"
        await db.flush()
    logger.error("纸面组合同步失败 algo=%s error=%s", algo_code, error[:300])


async def sync_all_paper_portfolios(db: AsyncSession) -> list[dict]:
    """逐组合同步，单组合失败回滚并标记 failed，不影响其余组合。"""
    results: list[dict] = []
    for algo_code in enabled_algos():
        if algo_code not in SPECS:
            continue
        try:
            result = await sync_paper_portfolio(db, algo_code)
            await db.commit()
            results.append(result)
        except Exception as exc:  # noqa: BLE001 - 单组合失败隔离
            await db.rollback()
            logger.exception("纸面组合失败 algo=%s", algo_code)
            try:
                await _mark_failed(db, algo_code, f"{type(exc).__name__}: {exc}")
                await db.commit()
            except Exception:  # pragma: no cover
                await db.rollback()
            results.append({
                "algo": algo_code, "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            })
    return results


async def sync_paper_background(algo_code: str) -> None:
    """API 手动触发：自建会话 + 每组合 advisory lock。"""
    async with AsyncSessionLocal() as db:
        lock = f"gfna:paper:{algo_code}"
        acquired = False
        try:
            acquired = await try_advisory_lock(db, lock)
            if not acquired:
                logger.info("纸面组合同步已在运行，跳过 algo=%s", algo_code)
                return
            result = await sync_paper_portfolio(db, algo_code)
            await db.commit()
            logger.info("手动纸面同步完成 algo=%s status=%s", algo_code, result["status"])
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            logger.exception("手动纸面同步失败 algo=%s", algo_code)
            try:
                await _mark_failed(db, algo_code, f"{type(exc).__name__}: {exc}")
                await db.commit()
            except Exception:  # pragma: no cover
                await db.rollback()
        finally:
            if acquired:
                try:
                    await advisory_unlock(db, lock)
                    await db.commit()
                except Exception:  # pragma: no cover
                    await db.rollback()


def _freshness(latest_nav: date | None, market: date | None, updated_at: datetime | None) -> dict:
    if latest_nav is None:
        return {
            "latest_trade_date": None,
            "market_trade_date": market.isoformat() if market else None,
            "lag_days": None,
            "stale": True,
            "updated_at": updated_at.isoformat() if updated_at else None,
            "ready": False,
        }
    return {
        "latest_trade_date": latest_nav.isoformat(),
        "market_trade_date": market.isoformat() if market else None,
        "lag_days": (market - latest_nav).days if market else None,
        "stale": bool(market and latest_nav < market),
        "updated_at": updated_at.isoformat() if updated_at else None,
        "ready": True,
    }


def _summary(nav: float | None, benchmark: float | None, max_drawdown: float | None) -> dict:
    total = round(nav - 1.0, 6) if nav is not None else None
    bench = round(benchmark - 1.0, 6) if benchmark is not None else None
    return {
        "nav": nav,
        "benchmark_nav": benchmark,
        "total_return": total,
        "benchmark_return": bench,
        "excess_return": round(total - bench, 6) if total is not None and bench is not None else None,
        "max_drawdown": max_drawdown,
    }


async def list_paper_portfolios(db: AsyncSession) -> list[dict]:
    portfolios = (await db.execute(select(PaperPortfolio))).scalars().all()
    try:
        market = await stg_runner.latest_trade_date(db)
    except DataUnavailableError:
        market = None
    portals: list[dict] = []
    for portfolio in portfolios:
        latest = (await db.execute(
            select(PaperNav)
            .where(PaperNav.portfolio_id == portfolio.portfolio_id)
            .order_by(PaperNav.trade_date.desc())
            .limit(1)
        )).scalars().first()
        tracking_days = (await db.execute(
            select(func.count()).select_from(PaperNav)
            .where(PaperNav.portfolio_id == portfolio.portfolio_id)
        )).scalar_one()
        worst = (await db.execute(
            select(func.min(PaperNav.drawdown))
            .where(PaperNav.portfolio_id == portfolio.portfolio_id)
        )).scalar_one_or_none()
        nav = float(latest.nav) if latest and latest.nav is not None else None
        bench = float(latest.benchmark) if latest and latest.benchmark is not None else None
        portals.append({
            "portfolio_id": portfolio.portfolio_id,
            "algo_code": portfolio.algo_code,
            "name": portfolio.name or portfolio.algo_code,
            "status": portfolio.status,
            "start_date": portfolio.start_date.isoformat() if portfolio.start_date else None,
            "tracking_days": int(tracking_days or 0),
            "latest_trade_date": latest.trade_date.isoformat() if latest else None,
            "rebalance_days": portfolio.rebalance_days,
            "max_positions": portfolio.max_positions,
            "initial_capital": float(portfolio.initial_capital or 0.0),
            "vs_backtest": latest.vs_backtest if latest else None,
            "freshness": _freshness(
                latest.trade_date if latest else None, market,
                latest.updated_at if latest else None,
            ),
            **_summary(nav, bench, float(worst) if worst is not None else None),
        })
    existing = {item["algo_code"] for item in portals}
    for code in enabled_algos():
        if code in existing:
            continue
        portals.append({
            "portfolio_id": None,
            "algo_code": code,
            "name": SPECS[code].name,
            "status": "pending",
            "start_date": None,
            "tracking_days": 0,
            "latest_trade_date": None,
            "rebalance_days": BACKTEST.rebalance_days,
            "max_positions": BACKTEST.max_positions,
            "initial_capital": float(BACKTEST.initial_cash),
            "vs_backtest": None,
            "freshness": _freshness(None, market, None),
            **_summary(None, None, None),
        })
    order = {code: spec.sort_order for code, spec in SPECS.items()}
    portals.sort(key=lambda item: order.get(item["algo_code"], 999))
    return portals


async def get_paper_detail(db: AsyncSession, algo_code: str, days: int = 250) -> dict:
    portfolio = await _get_portfolio(db, algo_code)
    if portfolio is None:
        raise NotFoundError(
            f"纸面组合尚未创建: {algo_code}",
            detail={"algo": algo_code},
        )
    rows = (await db.execute(
        select(PaperNav)
        .where(PaperNav.portfolio_id == portfolio.portfolio_id)
        .order_by(PaperNav.trade_date.desc())
        .limit(max(1, min(days, 2000)))
    )).scalars().all()
    rows = list(reversed(rows))
    tracking_days = (await db.execute(
        select(func.count()).select_from(PaperNav)
        .where(PaperNav.portfolio_id == portfolio.portfolio_id)
    )).scalar_one()
    worst = (await db.execute(
        select(func.min(PaperNav.drawdown))
        .where(PaperNav.portfolio_id == portfolio.portfolio_id)
    )).scalar_one_or_none()
    try:
        market = await stg_runner.latest_trade_date(db)
    except DataUnavailableError:
        market = None

    latest = rows[-1] if rows else None
    nav = float(latest.nav) if latest and latest.nav is not None else None
    bench = float(latest.benchmark) if latest and latest.benchmark is not None else None
    params = dict(portfolio.params or {})
    return {
        "portfolio_id": portfolio.portfolio_id,
        "algo_code": portfolio.algo_code,
        "name": portfolio.name or portfolio.algo_code,
        "status": portfolio.status,
        "start_date": portfolio.start_date.isoformat() if portfolio.start_date else None,
        "tracking_days": int(tracking_days or 0),
        "params": {
            "rebalance_days": portfolio.rebalance_days,
            "max_positions": portfolio.max_positions,
            "initial_capital": float(portfolio.initial_capital or 0.0),
            "strategy": params.get("strategy") or {},
        },
        "caliber": CALIBER,
        "series": [
            {
                "date": row.trade_date.isoformat(),
                "nav": float(row.nav) if row.nav is not None else None,
                "benchmark": float(row.benchmark) if row.benchmark is not None else None,
                "drawdown": float(row.drawdown) if row.drawdown is not None else None,
            }
            for row in rows
        ],
        "holdings": latest.holdings if latest else [],
        "cash_ratio": float(latest.cash_ratio) if latest and latest.cash_ratio is not None else None,
        "pending_orders": latest.pending_orders if latest else [],
        "vs_backtest": latest.vs_backtest if latest else None,
        "summary": _summary(
            nav, bench, float(worst) if worst is not None else None,
        ),
        "freshness": _freshness(
            latest.trade_date if latest else None, market,
            latest.updated_at if latest else None,
        ),
    }


__all__ = [
    "CALIBER", "enabled_algos", "is_enabled", "sync_paper_portfolio",
    "sync_all_paper_portfolios", "sync_paper_background",
    "list_paper_portfolios", "get_paper_detail",
]
