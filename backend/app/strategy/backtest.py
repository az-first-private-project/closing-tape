"""
回测引擎（方案 D4 / D5）
=========================
口径（用户定稿，不得自创）：
  - 每 ``rebalance_days``（默认 20）个交易日调仓一次，持仓 ``max_positions``（默认 20）只等权；
  - 基准 ``sh000300``（沪深300）；
  - **T 日收盘出信号 → T+1 开盘成交**；持有到下一次调仓（不做止损/止盈）；
  - 成本：佣金 0.0001（双边，全佣）、最低 5 元/笔；印花税 0.0005（仅卖出）；
    滑点 0.001（买入价 = open×(1+slip)，卖出价 = open×(1-slip)）；
  - 涨停/跌停/停牌不可成交（用 ``fact_stock_daily.is_limit_up/is_limit_down/is_suspended``）：
    买不进就跳过并记录 reason，卖不出就继续持有并在下一次调仓再试；
  - 初始资金 100 万；股数按 100 股整手向下取整，不足 1 手跳过；
  - 用**后复权收益**计算持仓市值：``close × adj_factor`` 归一化；
    adj_factor 缺失时退化为原始价，并在 metrics 里标 ``adjusted=false``。

未来函数纪律（代码层面硬约束）：
  1. 任何持仓决策只使用信号日 T 及之前的数据；
  2. 成交只使用 T+1 的**开盘价**（这是执行假设，不是未来函数）；
  3. 模块内禁止出现负向 shift（``shift(-1)`` 这类回看未来的写法）；
  4. ``assert_no_lookahead()`` 会对本包所有 .py 源文件做静态自检，
     测试里可以人为篡改价格/改写代码验证检测有效。

资金流处理：
  - 组合状态 = 现金 + 若干个持仓（adjusted units，后复权份额）；
  - 买入：现金 -= 成交金额 + 佣金；adj 有效时 units = shares / adj_factor，
    于是市值 = units × close × adj_factor 天然包含分红/送转的复权收益；
  - 卖出：现金 += 成交金额 - 佣金 - 印花税；
  - 每日净值 = 现金 + Σ(units × close × adj_factor)（停牌/缺数据沿用上一有效市值）。
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple, Sequence

import pandas as pd
from sqlalchemy import select, update
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import BACKTEST
from app.core.errors import DataUnavailableError, InvalidParamError
from app.db.models.fact import FactStockDaily, MarketIndex
from app.db.models.meta import TradeCalendar
from app.db.models.strategy import BacktestRun
from app.db.session import AsyncSessionLocal
from app.strategy import runner as stg_runner
from app.strategy import strategies as strat
from app.strategy.metrics import compute_metrics

logger = logging.getLogger(__name__)

#: 负向 shift 的静态扫描标记（拆开拼接，避免本文件被自身检测命中）
_LOOKAHEAD_MARK = ".shi" + "ft(-"

#: 回测金额保留 2 位小数
_MONEY_DIGITS = 2


# =====================================================================
# 未来函数静态检测
# =====================================================================

def detect_lookahead_usage(source_code: str) -> list[str]:
    """扫描源码字符串里的负向 shift（未来函数特征），返回问题列表。

    典型未来函数：用 ``shift(-1)`` 把"明天的价格"挪到今天参与决策。
    本函数只做静态文本扫描，**不执行**被检查的代码。
    """
    if not isinstance(source_code, str):
        raise InvalidParamError(
            f"detect_lookahead_usage 需要源码字符串，收到: {type(source_code).__name__}"
        )
    issues: list[str] = []
    for line_number, line in enumerate(source_code.splitlines(), start=1):
        if _LOOKAHEAD_MARK in line:
            issues.append(
                f"第 {line_number} 行出现负向 shift（疑似未来函数）: {line.strip()[:160]}"
            )
    return issues


def assert_no_lookahead(source_file: str | Path | None = None) -> None:
    """回测模块的静态自检：本包源文件不得出现负向 shift。

    默认扫描 ``app/strategy/*.py``；也可传入单个文件路径（测试用）。
    发现未来函数时抛 ``RuntimeError``（这是开发者错误，不是业务错误）。
    """
    if source_file is None:
        package_dir = Path(__file__).resolve().parent
        paths = sorted(package_dir.glob("*.py"))
    else:
        paths = [Path(source_file)]
    if not paths:
        raise RuntimeError("assert_no_lookahead 未找到可扫描的源文件")

    issues: list[str] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:  # pragma: no cover - 文件系统异常
            raise RuntimeError(f"读取源文件失败: {path} ({exc})") from exc
        for issue in detect_lookahead_usage(text):
            issues.append(f"{path.name}: {issue}")
    if issues:
        raise RuntimeError("检测到未来函数（负向 shift）：\n" + "\n".join(issues))


# =====================================================================
# 回测参数
# =====================================================================

def _to_int(value: Any, *, name: str) -> int:
    """转 int 并校验整数性。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise InvalidParamError(f"回测参数 {name} 必须是整数，收到: {value!r}") from None
    if isinstance(value, float) and not float(value).is_integer():
        raise InvalidParamError(f"回测参数 {name} 必须是整数，收到: {value!r}")
    return number


def _to_float(value: Any, *, name: str) -> float:
    """转 float 并拒绝 NaN/inf。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise InvalidParamError(f"回测参数 {name} 必须是数值，收到: {value!r}") from None
    if number != number or number in (float("inf"), float("-inf")):
        raise InvalidParamError(f"回测参数 {name} 必须是有限数值，收到: {value!r}")
    return number


def _to_bool(value: Any) -> bool:
    """宽松布尔转换（兼容 "false"/"0" 等查询参数）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y", "是"}


@dataclass
class BacktestParams:
    """回测参数（从 `config.BACKTEST` 继承默认值，可被查询参数覆盖）。"""

    rebalance_days: int = 20
    max_positions: int = 20
    benchmark_code: str = "sh000300"
    commission_rate: float = 0.0001
    stamp_tax_rate: float = 0.0005
    slippage_rate: float = 0.001
    min_commission: float = 5.0
    initial_cash: float = 1_000_000.0
    exclude_st: bool = True
    exclude_new_days: int = 60

    @classmethod
    def from_config(cls) -> "BacktestParams":
        """从 `app.core.config.BACKTEST` 取默认值。"""
        return cls(
            rebalance_days=int(BACKTEST.rebalance_days),
            max_positions=int(BACKTEST.max_positions),
            benchmark_code=str(BACKTEST.benchmark_code),
            commission_rate=float(BACKTEST.commission_rate),
            stamp_tax_rate=float(BACKTEST.stamp_tax_rate),
            slippage_rate=float(BACKTEST.slippage_rate),
            min_commission=float(BACKTEST.min_commission),
            initial_cash=float(BACKTEST.initial_cash),
            exclude_st=bool(BACKTEST.exclude_st),
            exclude_new_days=int(BACKTEST.exclude_new_days),
        )

    @classmethod
    def from_dict(cls, overrides: Mapping[str, Any] | None = None) -> "BacktestParams":
        """合并默认值与覆盖项（支持契约 6.1 的短参数名）。"""
        aliases = {
            "benchmark": "benchmark_code",
            "commission": "commission_rate",
            "stamp_tax": "stamp_tax_rate",
            "slippage": "slippage_rate",
        }
        instance = cls.from_config()
        values = asdict(instance)
        extra_ignored = {"position_sizing"}

        for raw_key, raw_value in dict(overrides or {}).items():
            key = str(raw_key).strip()
            target = aliases.get(key, key)
            if target in extra_ignored:
                if str(raw_value).strip().lower() != "equal":
                    raise InvalidParamError(
                        f"目前仅支持等权持仓（position_sizing=equal），收到: {raw_value!r}"
                    )
                continue
            if target not in values:
                raise InvalidParamError(
                    f"未知回测参数: {key}",
                    detail={
                        "allowed": sorted(list(values) + list(aliases)),
                    },
                )
            values[target] = raw_value

        instance = cls(**values)
        instance.validate()
        return instance

    def validate(self) -> None:
        """参数范围校验（非法抛 InvalidParamError）。"""
        self.rebalance_days = _to_int(self.rebalance_days, name="rebalance_days")
        self.max_positions = _to_int(self.max_positions, name="max_positions")
        self.exclude_new_days = _to_int(self.exclude_new_days, name="exclude_new_days")
        self.commission_rate = _to_float(self.commission_rate, name="commission_rate")
        self.stamp_tax_rate = _to_float(self.stamp_tax_rate, name="stamp_tax_rate")
        self.slippage_rate = _to_float(self.slippage_rate, name="slippage_rate")
        self.min_commission = _to_float(self.min_commission, name="min_commission")
        self.initial_cash = _to_float(self.initial_cash, name="initial_cash")
        self.exclude_st = _to_bool(self.exclude_st)
        self.benchmark_code = str(self.benchmark_code or "").strip()
        if not self.benchmark_code:
            raise InvalidParamError("回测参数 benchmark 不能为空")

        ranges = {
            "rebalance_days": (self.rebalance_days, 1, 250),
            "max_positions": (self.max_positions, 1, 200),
            "commission_rate": (self.commission_rate, 0.0, 0.1),
            "stamp_tax_rate": (self.stamp_tax_rate, 0.0, 0.1),
            "slippage_rate": (self.slippage_rate, 0.0, 0.1),
            "min_commission": (self.min_commission, 0.0, 1000.0),
            "initial_cash": (self.initial_cash, 1.0, 1.0e12),
            "exclude_new_days": (self.exclude_new_days, 0, 1000),
        }
        for name, (value, minimum, maximum) in ranges.items():
            if value < minimum or value > maximum:
                raise InvalidParamError(
                    f"回测参数 {name} 超出范围 [{minimum}, {maximum}]，收到: {value}",
                    detail={"param": name, "value": value, "min": minimum, "max": maximum},
                )

    def as_snapshot(self) -> dict:
        """完整参数快照（落库复现用）。"""
        return {
            "rebalance_days": self.rebalance_days,
            "max_positions": self.max_positions,
            "benchmark": self.benchmark_code,
            "commission": self.commission_rate,
            "stamp_tax": self.stamp_tax_rate,
            "slippage": self.slippage_rate,
            "min_commission": self.min_commission,
            "initial_cash": self.initial_cash,
            "exclude_st": self.exclude_st,
            "exclude_new_days": self.exclude_new_days,
        }

    def as_contract_dict(self, strategy_params: Mapping[str, Any] | None = None) -> dict:
        """契约 6.1 的 ``params`` 字段。"""
        return {
            "rebalance_days": self.rebalance_days,
            "max_positions": self.max_positions,
            "benchmark": self.benchmark_code,
            "commission": self.commission_rate,
            "stamp_tax": self.stamp_tax_rate,
            "slippage": self.slippage_rate,
            "initial_cash": self.initial_cash,
            "strategy": dict(strategy_params or {}),
        }


# =====================================================================
# 数据加载（区间数据预取到内存，模拟循环里零查询）
# =====================================================================

class Bar(NamedTuple):
    """某个股某日的回测所需行情字段（原始价 + 复权因子 + 不可成交标记）。"""

    open: float | None
    close: float | None
    adj_factor: float | None
    is_limit_up: bool
    is_limit_down: bool
    is_suspended: bool


def _as_date(value: Any, *, field_name: str) -> date:
    """date/datetime/ISO 字符串 → date。"""
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


def phase_signal_dates(
    phase_days: Sequence[date],
    rebalance_days: int,
    *,
    signal_from: date,
    end: date,
) -> list[date]:
    """按 ``phase_days[::rebalance_days]`` 的绝对相位取信号日。

    纸面组合的增量窗口必须与全量重算使用同一调仓相位，否则同样的策略参数
    会在不同日期成交。``signal_from`` 允许从窗口前一个检查点日开始补算信号
    （其 T+1 成交日正好是窗口第一天）。
    """
    step = max(int(rebalance_days), 1)
    return [
        phase_days[index]
        for index in range(0, len(phase_days), step)
        if signal_from <= phase_days[index] < end
    ]


async def load_trading_days(db: AsyncSession, start: date, end: date) -> tuple[list[date], str]:
    """加载区间交易日序列。

    优先用真实 `trade_calendar`（**不要 weekday()**，否则节假日会跑空）；
    表不存在或区间内无记录时退化为 ``pandas.bdate_range``，调用方需在结果里
    标记 ``calendar_source="fallback"``。
    """
    statement = (
        select(TradeCalendar.cal_date)
        .where(TradeCalendar.is_open.is_(True))
        .where(TradeCalendar.cal_date >= start)
        .where(TradeCalendar.cal_date <= end)
        .order_by(TradeCalendar.cal_date)
    )
    try:
        rows = (await db.execute(statement)).scalars().all()
    except ProgrammingError:
        logger.warning("trade_calendar 不存在，回测日历退化为 bdate_range")
        rows = []
    if rows:
        return [item for item in rows], "db"
    fallback = [timestamp.date() for timestamp in pd.bdate_range(start=start, end=end)]
    logger.warning("trade_calendar 区间无数据，回测日历退化为 bdate_range（days=%d）", len(fallback))
    return fallback, "fallback"


def _num_or_none(value: Any) -> float | None:
    """Decimal/None → float/None。"""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bar_from_row(
    open_: Any,
    close: Any,
    adj_factor: Any,
    is_limit_up: Any,
    is_limit_down: Any,
    is_suspended: Any,
) -> Bar:
    """一行行情 → Bar。"""
    return Bar(
        open=_num_or_none(open_),
        close=_num_or_none(close),
        adj_factor=_num_or_none(adj_factor),
        is_limit_up=bool(is_limit_up),
        is_limit_down=bool(is_limit_down),
        is_suspended=bool(is_suspended),
    )


async def load_price_book(
    db: AsyncSession,
    codes: Sequence[str],
    start: date,
    end: date,
) -> dict[str, dict[date, Bar]]:
    """一次性预取区间内所有相关标的的行情（回测循环内不再查库）。"""
    code_list = [str(code).zfill(6) for code in codes if code]
    if not code_list:
        return {}
    statement = (
        select(
            FactStockDaily.code,
            FactStockDaily.trade_date,
            FactStockDaily.open,
            FactStockDaily.close,
            FactStockDaily.adj_factor,
            FactStockDaily.is_limit_up,
            FactStockDaily.is_limit_down,
            FactStockDaily.is_suspended,
        )
        .where(FactStockDaily.code.in_(code_list))
        .where(FactStockDaily.trade_date >= start)
        .where(FactStockDaily.trade_date <= end)
        .order_by(FactStockDaily.code, FactStockDaily.trade_date)
    )
    rows = (await db.execute(statement)).all()
    book: dict[str, dict[date, Bar]] = {}
    for code, trade_day, open_, close, adj_factor, is_limit_up, is_limit_down, is_suspended in rows:
        book.setdefault(code, {})[_as_date(trade_day, field_name="trade_date")] = _bar_from_row(
            open_, close, adj_factor, is_limit_up, is_limit_down, is_suspended
        )
    logger.info(
        "回测行情预取完成 codes=%d rows=%d", len(book), sum(len(item) for item in book.values())
    )
    return book


async def load_benchmark_closes(
    db: AsyncSession,
    benchmark_code: str,
    start: date,
    end: date,
) -> dict[date, float]:
    """预取基准指数收盘价；数据缺失时返回空 dict（指标里 benchmark 返回 None）。"""
    statement = (
        select(MarketIndex.trade_date, MarketIndex.close)
        .where(MarketIndex.index_code == benchmark_code)
        .where(MarketIndex.trade_date >= start)
        .where(MarketIndex.trade_date <= end)
        .order_by(MarketIndex.trade_date)
    )
    try:
        rows = (await db.execute(statement)).all()
    except ProgrammingError:
        logger.warning("market_indices 表不存在，回测基准缺失")
        return {}
    closes: dict[date, float] = {}
    for trade_day, close in rows:
        value = _num_or_none(close)
        if value is not None and value > 0:
            closes[_as_date(trade_day, field_name="trade_date")] = value
    return closes


# =====================================================================
# 组合模拟（纯函数，便于不依赖 DB 的测试）
# =====================================================================

def _target_list(
    target_items: Sequence[Mapping[str, Any]], max_positions: int
) -> list[tuple[str, str]]:
    """目标持仓去重、补零并截断到 max_positions（与 _rebalance 同规则）。"""
    targets: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in target_items:
        raw_code = item.get("code")
        if not raw_code:
            continue
        code = str(raw_code).zfill(6)
        if code in seen:
            continue
        seen.add(code)
        targets.append((code, str(item.get("name") or code)))
    return targets[:max_positions]


#: 待执行清单使用中文原因，成交记录沿用既有英文 reason（不影响既有读端）
_TRADE_REASONS = {"停牌": "suspended", "一字涨停": "limit_up", "一字跌停": "limit_down"}


def _order_fillable(bar: Bar | None, side: str) -> tuple[bool | None, str | None]:
    """能否成交的唯一判定：回测成交、每日快照、待执行清单共用。"""
    if bar is None:
        return None, None  # T+1 行情尚未产生，无法预判
    if bar.is_suspended:
        return False, "停牌"
    if side == "buy" and bar.is_limit_up:
        return False, "一字涨停"
    if side == "sell" and bar.is_limit_down:
        return False, "一字跌停"
    if bar.open is None or bar.open <= 0:
        return False, "无开盘价"
    return True, None


def build_pending_orders(
    current_holdings: Sequence[Mapping[str, Any]],
    target_items: Sequence[Mapping[str, Any]],
    params: BacktestParams,
    *,
    next_bar: Callable[[str], Bar | None] | None = None,
) -> list[dict]:
    """收盘后生成下一交易日待执行清单。

    ``next_bar`` 为下一交易日行情（历史行可用）；最新一天尚未开盘时传 None，
    此时 ``fillable`` 为 null，由前端提示“待 T+1 开盘校验”。
    """
    targets = _target_list(target_items, params.max_positions)
    target_codes = {code for code, _ in targets}
    orders: list[dict] = []
    sold: set[str] = set()

    for item in current_holdings:
        code = str(item.get("code") or "").zfill(6)
        if not code or code in target_codes:
            continue
        bar = next_bar(code) if next_bar else None
        fillable, block_reason = _order_fillable(bar, "sell")
        orders.append({
            "side": "sell",
            "code": code,
            "name": str(item.get("name") or code),
            "reason": "T+1 开盘",
            "fillable": fillable,
            "block_reason": block_reason,
        })
        if fillable is not False:
            # 已知卖不出（停牌/跌停）时持仓仍占名额，不能据此计划买入
            sold.add(code)

    held = {
        str(item.get("code") or "").zfill(6) for item in current_holdings
    } - sold
    free_slots = max(0, int(params.max_positions) - len(held))
    for code, name in targets:
        if free_slots <= 0:
            break
        if code in held:
            continue
        bar = next_bar(code) if next_bar else None
        fillable, block_reason = _order_fillable(bar, "buy")
        orders.append({
            "side": "buy",
            "code": code,
            "name": name,
            "reason": "T+1 开盘",
            "fillable": fillable,
            "block_reason": block_reason,
        })
        free_slots -= 1
    return orders


def _state_snapshot(
    day: date,
    cash: float,
    positions: Mapping[str, Mapping[str, Any]],
    last_value: Mapping[str, float],
    bench_base: float | None,
    bench_last: float | None,
) -> dict:
    """组合内部状态快照（纸面组合检查点续算用，不进入契约响应）。"""
    return {
        "date": day.isoformat(),
        "cash": round(float(cash), _MONEY_DIGITS),
        "positions": {
            code: {
                "name": position.get("name") or code,
                "shares": int(position.get("shares") or 0),
                "units": float(position.get("units") or 0.0),
                "cost_basis": round(float(position.get("cost_basis") or 0.0), _MONEY_DIGITS),
            }
            for code, position in positions.items()
        },
        "last_value": {code: float(value) for code, value in last_value.items()},
        "benchmark_base": bench_base,
        "benchmark_last": bench_last,
    }


def simulate_portfolio(
    trading_days: Sequence[date],
    executions: Mapping[date, Sequence[Mapping[str, Any]]],
    price_book: Mapping[str, Mapping[date, Bar]],
    benchmark_closes: Mapping[date, float] | None,
    params: BacktestParams,
    *,
    snapshots_out: list[dict] | None = None,
    initial_state: Mapping[str, Any] | None = None,
    capture_state_on: date | None = None,
    state_out: dict | None = None,
) -> tuple[list[dict], list[dict], bool]:
    """按"T+1 开盘成交"模拟组合，返回 ``(equity_curve, trades, adjusted)``。

    这是回测的核心资金流实现：
      - 每个交易日先看是否调仓日（executions 的键是**成交日**，即信号日的下一个交易日）；
      - 收盘后按后复权价计算净值；
      - 不引入任何 T+1 之后的数据决定 T 的持仓。

    ``initial_state`` 用于纸面组合的检查点续算：从上次水位收盘状态（现金、
    后复权份额、基准基数）继续模拟，曲线只覆盖传入的交易日。
    ``capture_state_on`` 指定在哪个交易日收盘落状态（缺省为最后一天），
    通过 ``state_out`` 返回，供下一次续算使用。
    """
    seed = dict(initial_state or {})
    cash: float = round(
        float(seed["cash"]) if "cash" in seed else float(params.initial_cash), _MONEY_DIGITS
    )
    positions: dict[str, dict[str, Any]] = {
        str(code).zfill(6): dict(position)
        for code, position in dict(seed.get("positions") or {}).items()
    }
    last_value: dict[str, float] = {
        str(code).zfill(6): float(value)
        for code, value in dict(seed.get("last_value") or {}).items()
    }
    trades: list[dict] = []
    curve: list[dict] = []
    adjusted_ok = True

    benchmark_data = benchmark_closes or {}
    bench_base: float | None = (
        float(seed["benchmark_base"]) if seed.get("benchmark_base") is not None else None
    )
    bench_last: float | None = (
        float(seed["benchmark_last"]) if seed.get("benchmark_last") is not None else None
    )
    # 有初始状态时基准基数以检查点为准；无状态（全量重算）才用区间首个收盘价
    seed_bench_base = "benchmark_base" in seed
    captured = False

    def _bar(code: str, day: date) -> Bar | None:
        return (price_book.get(code) or {}).get(day)

    def _adj_factor(bar: Bar | None) -> float:
        nonlocal adjusted_ok
        if bar is None or bar.adj_factor is None or bar.adj_factor <= 0:
            adjusted_ok = False
            return 1.0
        return float(bar.adj_factor)

    def _commission(amount: float) -> float:
        return round(max(amount * params.commission_rate, params.min_commission), _MONEY_DIGITS)

    def _sell_fee(amount: float) -> float:
        return round(_commission(amount) + amount * params.stamp_tax_rate, _MONEY_DIGITS)

    def _buy_price(open_price: float) -> float:
        return round(open_price * (1.0 + params.slippage_rate) + 1e-9, 2)

    def _sell_price(open_price: float) -> float:
        return round(open_price * (1.0 - params.slippage_rate) + 1e-9, 2)

    def _append_trade(
        day: date,
        code: str,
        name: str,
        side: str,
        price: float | None,
        shares: int,
        amount: float,
        fee: float,
        reason: str,
    ) -> None:
        trades.append(
            {
                "date": day.isoformat(),
                "code": code,
                "name": name,
                "side": side,
                "price": round(price, 2) if price is not None else None,
                "shares": int(shares),
                "amount": round(amount, _MONEY_DIGITS),
                "fee": round(fee, _MONEY_DIGITS),
                "reason": reason,
            }
        )

    def _rebalance(day: date, target_items: Sequence[Mapping[str, Any]]) -> None:
        nonlocal cash

        # 目标持仓（去重、截断到 max_positions）
        targets = _target_list(target_items, params.max_positions)
        target_codes = {code for code, _ in targets}

        # ---- 1) 卖出不在目标里的持仓（跌停/停牌/无数据卖不出就继续持有，下次再试）----
        for code in list(positions.keys()):
            if code in target_codes:
                continue
            position = positions[code]
            bar = _bar(code, day)
            fillable, block_reason = _order_fillable(bar, "sell")
            if fillable is not True:
                _append_trade(
                    day, code, position["name"], "sell", None, 0, 0.0, 0.0,
                    _TRADE_REASONS.get(block_reason or "", "no_data"),
                )
                continue
            price = _sell_price(float(bar.open))
            shares = int(position["shares"])
            amount = round(price * shares, _MONEY_DIGITS)
            fee = _sell_fee(amount)
            cash = round(cash + amount - fee, _MONEY_DIGITS)
            _append_trade(day, code, position["name"], "sell", price, shares, amount, fee, "rebalance")
            del positions[code]
            last_value.pop(code, None)

        # ---- 2) 开盘时权益（用于等权分配）----
        equity_open = cash
        for code, position in positions.items():
            bar = _bar(code, day)
            if bar is not None and bar.open is not None and bar.open > 0:
                value = position["units"] * float(bar.open) * _adj_factor(bar)
            else:
                value = last_value.get(code, 0.0)
            equity_open += value
        target_value = equity_open / max(params.max_positions, 1)

        # ---- 3) 买入目标中尚未持有的标的（涨停留空：跳过并记录 reason）----
        max_new = max(0, int(params.max_positions) - len(positions))
        new_targets = [(code, name) for code, name in targets if code not in positions][:max_new]
        for index, (code, name) in enumerate(new_targets):
            remaining = len(new_targets) - index
            budget = min(target_value, cash / remaining) if remaining > 0 else 0.0
            bar = _bar(code, day)
            fillable, block_reason = _order_fillable(bar, "buy")
            if fillable is not True:
                _append_trade(
                    day, code, name, "buy", None, 0, 0.0, 0.0,
                    _TRADE_REASONS.get(block_reason or "", "no_data"),
                )
                continue

            price = _buy_price(float(bar.open))
            if price <= 0:
                _append_trade(day, code, name, "buy", None, 0, 0.0, 0.0, "no_data")
                continue
            lots = int(budget // (price * 100.0)) if budget > 0 else 0
            shares = lots * 100
            if shares < 100:
                _append_trade(day, code, name, "buy", price, 0, 0.0, 0.0, "below_one_lot")
                continue
            amount = round(price * shares, _MONEY_DIGITS)
            fee = _commission(amount)
            if amount + fee > cash:  # 现金不足时按可承受整手数缩量
                affordable = int((cash - params.min_commission) // (price * 100.0))
                shares = max(affordable, 0) * 100
                if shares < 100:
                    _append_trade(day, code, name, "buy", price, 0, 0.0, 0.0, "insufficient_cash")
                    continue
                amount = round(price * shares, _MONEY_DIGITS)
                fee = _commission(amount)
                if amount + fee > cash:
                    _append_trade(day, code, name, "buy", price, 0, 0.0, 0.0, "insufficient_cash")
                    continue

            cash = round(cash - amount - fee, _MONEY_DIGITS)
            adj = _adj_factor(bar)
            positions[code] = {
                "name": name,
                "shares": int(shares),
                "units": float(shares) / adj,   # 后复权份额：市值 = units × close × adj
                "cost_basis": round(amount + fee, _MONEY_DIGITS),
            }
            last_value[code] = round(shares * price, _MONEY_DIGITS)
            _append_trade(day, code, name, "buy", price, shares, amount, fee, "rebalance")

    for index, day in enumerate(trading_days):
        target = executions.get(day)
        if target is not None:
            _rebalance(day, target)

        # ---- 收盘净值：现金 + Σ(units × close × adj_factor) ----
        total = cash
        day_values: dict[str, float] = {}
        for code, position in positions.items():
            bar = _bar(code, day)
            if bar is not None and bar.close is not None and bar.close > 0:
                value = position["units"] * float(bar.close) * _adj_factor(bar)
                last_value[code] = value
            else:
                value = last_value.get(code, 0.0)
            day_values[code] = value
            total += value
        nav = (total / params.initial_cash) if params.initial_cash else 0.0

        # ---- 基准净值 ----
        raw_benchmark = benchmark_data.get(day)
        if raw_benchmark is not None and raw_benchmark > 0:
            if bench_base is None and not seed_bench_base:
                bench_base = float(raw_benchmark)
            bench_last = float(raw_benchmark)
        benchmark_nav = None
        if bench_base is not None and bench_last is not None and bench_base > 0:
            benchmark_nav = round(bench_last / bench_base, 6)

        curve.append(
            {
                "date": day.isoformat(),
                "nav": round(nav, 6),
                "benchmark": benchmark_nav,
            }
        )

        if snapshots_out is not None:
            holdings: list[dict] = []
            for code, position in positions.items():
                value = day_values.get(code, 0.0)
                shares = int(position.get("shares") or 0)
                cost_basis = float(position.get("cost_basis") or 0.0)
                holdings.append({
                    "code": code,
                    "name": position.get("name") or code,
                    "shares": shares,
                    "weight": round(value / total, 6) if total else 0.0,
                    "cost": round(cost_basis / shares, 4) if shares else 0.0,
                    "price": round(value / shares, 4) if shares else 0.0,
                    "pnl_pct": round(value / cost_basis - 1.0, 6) if cost_basis else 0.0,
                })
            holdings.sort(key=lambda item: item["weight"], reverse=True)
            next_day = trading_days[index + 1] if index + 1 < len(trading_days) else None
            next_target = executions.get(next_day) if next_day is not None else None
            # 只有下一交易日确为调仓成交日才有"待执行"；否则明天无操作
            pending: list[dict] = []
            if next_target is not None:
                pending = build_pending_orders(
                    holdings,
                    next_target,
                    params,
                    next_bar=lambda code, _day=next_day: _bar(code, _day),
                )
            snapshots_out.append({
                "date": day.isoformat(),
                "holdings": holdings,
                "cash_ratio": round(cash / total, 4) if total else 0.0,
                "pending_orders": pending,
            })

        if state_out is not None and capture_state_on is not None and day == capture_state_on:
            state_out.update(_state_snapshot(
                day, cash, positions, last_value, bench_base, bench_last,
            ))
            captured = True

    if state_out is not None and not captured and curve:
        state_out.update(_state_snapshot(
            trading_days[-1], cash, positions, last_value, bench_base, bench_last,
        ))

    return curve, trades, adjusted_ok


# =====================================================================
# 回测主入口
# =====================================================================

async def run_backtest(
    algo_code: str,
    start: date,
    end: date,
    strategy_params: dict | None = None,
    bt_params: dict | None = None,
    db: AsyncSession | None = None,
    bt_id: str | None = None,
    *,
    snapshots_out: list[dict] | None = None,
    tag: str | None = None,
    initial_state: Mapping[str, Any] | None = None,
    phase_anchor: date | None = None,
    signal_from: date | None = None,
    capture_state_on: date | None = None,
    state_out: dict | None = None,
) -> dict:
    """执行回测并落 ``backtest_run``，返回契约 6.1 结构。

    ``bt_id`` 可复用调用方预建的记录（参数敏感性矩阵的排队格、纸面组合的
    每日重算），用于在同一引擎路径下更新状态，不新增特殊回测分支。
    ``snapshots_out`` 收集每日收盘持仓/现金比/待执行单（T11）；
    ``tag`` 写入 params.source，便于区分纸面组合产生的回测记录。

    检查点续算（纸面组合 P2-3）：
      - ``initial_state``：上一水位收盘的组合内部状态，从该状态继续；
      - ``phase_anchor``：调仓相位的绝对起点（组合建仓日），保证增量窗口与
        全量重算在相同信号日调仓；
      - ``signal_from``：从哪天开始补算信号（通常是检查点日，其 T+1 是窗口首日）；
      - ``capture_state_on`` / ``state_out``：在指定交易日收盘落新状态。
    """
    spec = strat.get_spec(algo_code)
    backtest_params = BacktestParams.from_dict(bt_params)
    normalized = strat._validate_params(spec.code, strategy_params)  # noqa: SLF001
    # 保证信号数足够支撑目标持仓数（默认 limit=20 == max_positions=20 时无变化）
    normalized["limit"] = max(int(normalized["limit"]), int(backtest_params.max_positions))

    start_date = _as_date(start, field_name="start")
    end_date = _as_date(end, field_name="end")
    if start_date > end_date:
        raise InvalidParamError(
            f"回测开始日期不能晚于结束日期: {start_date} > {end_date}"
        )

    if db is not None:
        # 调用方持有事务：只 flush，不 commit（契约与 run_strategy 一致）
        return await _run_backtest_impl(
            db, spec, backtest_params, normalized, start_date, end_date,
            owns_session=False, bt_id=bt_id,
            snapshots_out=snapshots_out, tag=tag,
            initial_state=initial_state, phase_anchor=phase_anchor,
            signal_from=signal_from, capture_state_on=capture_state_on,
            state_out=state_out,
        )
    async with AsyncSessionLocal() as session:
        return await _run_backtest_impl(
            session, spec, backtest_params, normalized, start_date, end_date,
            owns_session=True, bt_id=bt_id,
            snapshots_out=snapshots_out, tag=tag,
            initial_state=initial_state, phase_anchor=phase_anchor,
            signal_from=signal_from, capture_state_on=capture_state_on,
            state_out=state_out,
        )


async def _run_backtest_impl(
    db: AsyncSession,
    spec: strat.StrategySpec,
    backtest_params: BacktestParams,
    strategy_params: dict,
    start_date: date,
    end_date: date,
    *,
    owns_session: bool,
    bt_id: str | None = None,
    snapshots_out: list[dict] | None = None,
    tag: str | None = None,
    initial_state: Mapping[str, Any] | None = None,
    phase_anchor: date | None = None,
    signal_from: date | None = None,
    capture_state_on: date | None = None,
    state_out: dict | None = None,
) -> dict:
    """回测实现：落 running → 调仓信号 → 预取行情 → 模拟 → 绩效 → 落库。

    事务所有权：``db=None``（自建会话）时自行 commit（失败也留记录）；
    调用方传 db 时只 flush，commit/rollback 由调用方负责。
    """
    record: BacktestRun | None = None
    params_snapshot = {
        **backtest_params.as_snapshot(),
        "strategy": dict(strategy_params),
    }
    if tag:
        params_snapshot["source"] = tag
    if bt_id is not None:
        record = await db.get(BacktestRun, bt_id)
        if record is None:
            raise InvalidParamError(
                f"回测记录不存在: {bt_id}", detail={"bt_id": bt_id}
            )
        record.algo_code = spec.code
        record.start_date = start_date
        record.end_date = end_date
        record.params = dict(params_snapshot)
        record.status = "running"
        record.error = None
        record.metrics = None
        record.equity_curve = None
        record.trades = None
        record.elapsed_ms = None
    else:
        bt_id = str(uuid.uuid4())
        record = BacktestRun(
            bt_id=bt_id,
            algo_code=spec.code,
            start_date=start_date,
            end_date=end_date,
            params=dict(params_snapshot),
            status="running",
        )
        db.add(record)
    if owns_session:
        await db.commit()  # 失败也留痕（D4 硬要求）
    else:
        await db.flush()

    started = time.perf_counter()
    try:
        # ---- 1) 交易日序列（不用 weekday，缺日历才退化）----
        trading_days, calendar_source = await load_trading_days(db, start_date, end_date)
        if len(trading_days) < 2:
            raise DataUnavailableError(
                f"{start_date.isoformat()} ~ {end_date.isoformat()} 交易日数量不足，无法回测",
                next_action="POST /api/v1/admin/ingest/calendar",
                detail={"days": len(trading_days)},
            )

        if phase_anchor is not None:
            # 增量续算：调仓相位必须从建仓日起算，而不是窗口首日
            phase_days, _ = await load_trading_days(db, phase_anchor, end_date)
            if not phase_days:
                raise DataUnavailableError(
                    f"调仓相位区间 {phase_anchor.isoformat()} ~ {end_date.isoformat()} 无交易日",
                    next_action="POST /api/v1/admin/ingest/calendar",
                )
            next_trading_day = {
                phase_days[index]: phase_days[index + 1]
                for index in range(len(phase_days) - 1)
            }
            signal_dates = phase_signal_dates(
                phase_days,
                backtest_params.rebalance_days,
                signal_from=signal_from or start_date,
                end=end_date,
            )
        else:
            next_trading_day = {
                trading_days[index]: trading_days[index + 1]
                for index in range(len(trading_days) - 1)
            }
            signal_dates = trading_days[:: backtest_params.rebalance_days]

        # ---- 2) 逐个调仓日取信号（只使用 T 日及之前数据）----
        executions: dict[date, list[dict]] = {}
        # 续算时持仓标的即使不在新信号里，也要预取行情用于估值与卖出
        involved_codes: set[str] = {
            str(code).zfill(6)
            for code in dict((initial_state or {}).get("positions") or {})
        }
        for signal_day in signal_dates:
            execution_day = next_trading_day.get(signal_day)
            if execution_day is None:
                continue  # 最后一个调仓日没有 T+1，跳过
            result = await stg_runner.run_strategy(
                spec.code,
                signal_day,
                strategy_params,
                force=False,
                db=db,
                is_backtest=True,
            )
            signals = list(result.get("data") or [])[: backtest_params.max_positions]
            if not signals:
                # 该调仓日无信号 → 目标持仓为空（T+1 清仓），但仍是一次有效调仓
                logger.info("回测 %s 信号日 %s 无信号，T+1 目标为空仓", spec.code, signal_day)
            executions[execution_day] = signals
            involved_codes.update(
                str(item.get("code")).zfill(6) for item in signals if item.get("code")
            )

        # ---- 3) 区间行情 / 基准一次性预取到内存 ----
        price_book = await load_price_book(
            db, sorted(involved_codes), start_date, end_date
        )
        benchmark_closes = await load_benchmark_closes(
            db, backtest_params.benchmark_code, start_date, end_date
        )

        # ---- 4) 模拟（纯函数）----
        curve, trades, adjusted_ok = simulate_portfolio(
            trading_days,
            executions,
            price_book,
            benchmark_closes,
            backtest_params,
            snapshots_out=snapshots_out,
            initial_state=initial_state,
            capture_state_on=capture_state_on,
            state_out=state_out,
        )

        # ---- 5) 绩效 ----
        nav_series = pd.Series(
            [item["nav"] for item in curve],
            index=pd.DatetimeIndex([pd.Timestamp(item["date"]) for item in curve]),
        )
        benchmark_series = None
        if any(item.get("benchmark") is not None for item in curve):
            benchmark_series = pd.Series(
                [item.get("benchmark") for item in curve],
                index=nav_series.index,
                dtype="float64",
            ).ffill().bfill()
        metrics = compute_metrics(
            nav_series,
            benchmark_series,
            trades,
            len(executions),
            backtest_params.initial_cash,
        )
        metrics["adjusted"] = bool(adjusted_ok)

        elapsed_ms = int((time.perf_counter() - started) * 1000)

        # ---- 6) 落库 ----
        record.params = {
            **backtest_params.as_snapshot(),
            "strategy": dict(strategy_params),
            "calendar_source": calendar_source,
            **({"source": tag} if tag else {}),
        }
        record.metrics = strat.jsonable(metrics)
        record.equity_curve = strat.jsonable(curve)
        record.trades = strat.jsonable(trades)
        record.status = "success"
        record.elapsed_ms = elapsed_ms
        if owns_session:
            await db.commit()
        else:
            await db.flush()

        logger.info(
            "回测完成 algo=%s %s~%s bt_id=%s rebalance=%d trades=%d "
            "total_return=%.2f%% max_dd=%.2f%% elapsed=%dms",
            spec.code,
            start_date,
            end_date,
            bt_id,
            len(executions),
            len([item for item in trades if int(item.get("shares") or 0) > 0]),
            float(metrics.get("total_return") or 0.0) * 100.0,
            float(metrics.get("max_drawdown") or 0.0) * 100.0,
            elapsed_ms,
        )
        return _build_backtest_response(
            record, spec, backtest_params, strategy_params, calendar_source,
            curve, trades, metrics,
        )

    except Exception as exc:  # noqa: BLE001 - 失败也要落 backtest_run 记录
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.exception(
            "回测失败 algo=%s %s~%s bt_id=%s", spec.code, start_date, end_date, bt_id
        )
        if owns_session:
            try:
                await db.rollback()
            except Exception:  # pragma: no cover
                logger.exception("回测失败后回滚出错 bt_id=%s", bt_id)
            try:
                await db.execute(
                    update(BacktestRun)
                    .where(BacktestRun.bt_id == bt_id)
                    .values(status="failed", error=str(exc)[:2000], elapsed_ms=elapsed_ms)
                )
                await db.commit()
            except Exception:  # pragma: no cover
                logger.exception("写入失败回测记录出错 bt_id=%s", bt_id)
                try:
                    await db.rollback()
                except Exception:
                    pass
        else:
            # 外部事务：只 flush 失败标记，回滚/提交由调用方负责
            try:
                await db.execute(
                    update(BacktestRun)
                    .where(BacktestRun.bt_id == bt_id)
                    .values(status="failed", error=str(exc)[:2000], elapsed_ms=elapsed_ms)
                )
                await db.flush()
            except Exception:  # pragma: no cover
                logger.warning("外部事务中写入 failed 回测记录失败（由调用方回滚）bt_id=%s", bt_id)
        raise


def _iso(value: Any) -> str | None:
    """datetime → ISO 字符串。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _build_backtest_response(
    record: BacktestRun,
    spec: strat.StrategySpec,
    backtest_params: BacktestParams,
    strategy_params: dict,
    calendar_source: str,
    curve: list[dict],
    trades: list[dict],
    metrics: dict,
) -> dict:
    """组装契约 6.1 响应。"""
    return {
        "bt_id": record.bt_id,
        "algo": spec.code,
        "start_date": record.start_date.isoformat(),
        "end_date": record.end_date.isoformat(),
        "params": backtest_params.as_contract_dict(strategy_params),
        "metrics": metrics,
        "equity_curve": curve,
        "trades": trades,
        "created_at": _iso(record.created_at),
        "elapsed_ms": record.elapsed_ms,
        "calendar_source": calendar_source,
    }


__all__ = [
    "BacktestParams",
    "Bar",
    "detect_lookahead_usage",
    "assert_no_lookahead",
    "load_trading_days",
    "phase_signal_dates",
    "load_price_book",
    "load_benchmark_closes",
    "build_pending_orders",
    "simulate_portfolio",
    "run_backtest",
]
