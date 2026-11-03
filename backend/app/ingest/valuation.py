"""
历史估值源：PE_TTM / PB / 股息率_TTM（A4 高股息、A3 价值质量因子的数据来源）
====================================================================================

问题
----
真实日线（baostock / 东财 ``stock_zh_a_hist``）**不含估值字段**，所以纯真实数据上
A4（高股息）、A3（价值/质量）两个策略出不了信号。本模块用
「新浪财报指标 + 已有的日线收盘价」把这三种因子补出来，并写回
``fact_stock_daily.pe_ttm / pb / dv_ttm``。

三阶段设计（**重要**）
----------------------
PE = 收盘价 / EPS_TTM，股息率 = 近 12 个月每股分红 / 收盘价：**分子只随财报/分红公告变
（季度/年度），价格每天变**。如果每天为全市场 5000+ 只股票各打一次接口（合规限频
≤2 请求/秒），光财报就要 45 分钟，15:36 的盘后流水线根本跑不完。所以拆成三段：

    阶段 1a sync_financials()  抓财报指标 → ``dim_stock_financial``（慢，有网络，季度跑）
    阶段 1b sync_dividends()   抓现金分红 → ``dim_stock_dividend``（慢，有网络，月度跑）
    阶段 2  compute_valuation() 库里的财报/分红 + 库里的收盘价 → 算 PE/PB/股息率写回日线
            —— **零网络**，全市场单日秒级，可每日/按区间反复跑

:func:`backfill_valuation` / :func:`collect_latest_valuation` 是给 ``app/pipeline.py``
用的便捷入口，内部就是「阶段 1a + 1b + 2」。

口径（写死在这里，全系统唯一）
------------------------------
- ``eps`` / ``bps`` 是新浪财报里的**每股**口径（元），累计值；
- ``eps_ttm``: 报告期 MMDD=1231 时 = 该期值；否则 = 本期累计 + 上年年报 − 上年同期
  （详见 :func:`eps_ttm_series` 的注释）；
- ``pe_ttm = close / eps_ttm``：**允许负值**（亏损股的 PE 为负，由策略层过滤 PE<=0）；
- ``pb = close / bps``：``bps <= 0``（净资产为负的资不抵债股）→ NULL；
- ``dv_ttm(%)``：近 12 个月（``ex_date ∈ (d-365, d]``）每股现金分红合计 / 收盘价 × 100，
  数据源是**巨潮分红明细**（``stock_dividend_cninfo``），按**除权日**做 point-in-time。
  ``payout_ratio``（新浪"股息发放率"）不再用于股息率 —— 它大量为空，且语义是
  "分红/净利润"（分红占利润比例）而不是"分红/股价"，两者不是一回事；该列仍入库作参考。
  完全没有分红记录 → 0.0（真实的"无股息"，不是数据缺失）；没有价格 → NULL。

EPS / BPS 的取值优先级（踩过坑，务必别改回去）
---------------------------------------------
- ``eps`` 优先 ``每股收益_调整后``，缺失再退 ``摊薄每股收益``（实测两者差异小，调整后更可比）；
- ``bps`` **优先 ``每股净资产_调整前``，缺失才退 ``每股净资产_调整后``**。
  原因：``每股净资产_调整后`` 存在脏数据 —— 实测 688981（中芯国际）
  ``调整后=594.8588`` 而 ``调整前=25.7375``（真实 BPS 约 19–26）。用"调整后"算 PB
  会错 20 倍，A1 的价值筛选会直接崩掉。EPS 的"调整后"没有这个问题。

为什么不直接抓现成的 PE/PB/股息率
---------------------------------
联网实测（2026-10）三个常见接口都不可用：``stock_a_indicator_lg``（akshare 1.19.1 已移除）、
``stock_zh_valuation_baidu``（网络不可达）、``stock_value_em``（东财被墙）、
``stock_individual_spot_xq``（雪球要 token）。新浪财报指标接口
``stock_financial_analysis_indicator`` 可用，所以只能「自己算」。

未来函数（point-in-time）防线
-----------------------------
财报与分红都有**披露/实施滞后**，不能用"未来才公开的信息"算"过去的估值"：

- 阶段 1a 落库时写入 ``announced_date = report_date + REPORT_LAG_DAYS``，阶段 2 只用
  ``announced_date <= 交易日`` 的最新报告期（``build_valuation_frame`` 的
  ``merge_asof(direction="backward")`` 做的就是这个）；
- 分红用**除权日**（``ex_date``）作为市场可知日：``d >= ex_date`` 才计入当天的近 12 个月
  分红（见 :func:`dv_ttm_series`）。

``REPORT_LAG_DAYS = 90`` 的理由（法定披露期限）：年报 4 个月内（次年 4/30 前）、
半年报 2 个月内（8/31 前）、一季报/三季报 1 个月内（4/30、10/31 前）。取 90 天是
折中：对季报/半年报**偏保守**（法定 30/62 天，我们等 90 天，绝不会提前用），
对年报则略早于法定 120 天（年报实际披露集中在 2–4 月，90 天基本落在真实披露窗口内）。
需要最严格口径时可以显式传 ``lag_days=120``（各函数都支持）。

注意：新浪财报接口 ``start_year=2019`` 时实测**最多只返回最近 14–15 期**（约 3.5 年），
拿不到更早的年度就如实留空，**绝不做线性外推**（外推出来的 PE 没有可信度）。

其他工程约定
------------
- **不在 import 期联网**：``akshare`` 用 try/except 导入，缺依赖时 ``ak=None`` 全部降级为
  「返回空表 + 记日志」；
- 所有 async DB 函数都接受 ``db: AsyncSession | None``：``None`` 时用
  ``app.db.session.session_scope()``（自己 commit），传入时**只 flush 不 commit**
  （事务所有权归 ``pipeline``）；
- 限频统一走 :func:`app.ingest.base.make_history_limiter`（≤2 请求/秒），在**每个**网络
  请求前 acquire，不在调用方重复节流；
- 写库只 ``UPDATE`` 那三列，**绝不 INSERT / 不覆盖 close、volume、amount 等行情列**
  （用 ``UPDATE ... FROM (VALUES ...)`` 一条语句批量更新，实测比逐行 UPDATE 快一个量级）。
"""
from __future__ import annotations

import asyncio
import math
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, AsyncIterator, Iterable, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

try:  # pragma: no cover —— 依赖是否安装由运行环境决定
    import akshare as ak
except ImportError:  # pragma: no cover
    ak = None  # type: ignore[assignment]

from app.core import config
from app.core.logging import get_logger
from app.ingest.base import FetchResult, chunked, make_history_limiter, upsert_rows
from app.ingest.quality import QualityReport, make_error_report, record_quality

if TYPE_CHECKING:  # pragma: no cover —— 只为类型标注，运行期不 import 数据库栈
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# ============================================================
# 常量
# ============================================================
#: 财报"可被市场获知"的保守滞后（自然日）。理由见模块 docstring。
REPORT_LAG_DAYS = 90

#: meta_data_quality / pipeline.TASK_DATASET 里的数据集名
DATASET = "valuation"

#: 财报抓取默认起始年份（回补 5 年 + TTM 需要的上年数据）
DEFAULT_START_YEAR = 2019

#: 阶段 1：每 N 只股票 upsert 一次（也是断点续传的粒度）
SYNC_BATCH_CODES = 20
#: 阶段 1：每 N 只刷新一次 ingest_job.checkpoint
SYNC_CHECKPOINT_EVERY = 50
#: 阶段 2：每批处理多少只股票（控制内存：一只股票 5 年日线约 1200 行）
COMPUTE_BATCH_CODES = 200
#: 单条 ``UPDATE ... FROM (VALUES ...)`` 的最大行数（PG 单语句参数上限 65535，5 参数/行）
UPDATE_CHUNK_ROWS = 1000

#: 阶段 1b：同一只股票的分红明细多久刷新一次（自然日）
#: 分红是年度/半年度事件，不需要每天抓；30 天窗口 = 每日增量几乎零请求，
#: 新分红最迟 30 天内入库（dv_ttm 用的是 trailing-12 个月的分红，影响可控）。
DIVIDEND_REFRESH_DAYS = 30
#: 股息率回看窗口（自然日）：ex_date ∈ (d-365, d] 的分红计入当天的近 12 个月合计
DV_WINDOW_DAYS = 365

#: 归一化后的财报表列（纯函数输出口径，测试与阶段 1 都依赖）
FIN_COLUMNS: tuple[str, ...] = ("report_date", "eps", "bps", "roe", "payout_ratio")
#: TTM 表列
TTM_COLUMNS: tuple[str, ...] = ("report_date", "eps_ttm", "eps_ttm_basis")
#: 展开到日线的估值表列
VALUATION_COLUMNS: tuple[str, ...] = ("trade_date", "pe_ttm", "pb", "dv_ttm", "report_date")
#: 归一化后的分红表列
DIVIDEND_COLUMNS: tuple[str, ...] = ("ex_date", "announce_date", "dps", "report_period")

# ---- 新浪财报指标列名候选（顺序即优先级：越靠前越精确）----
# 为什么要容错：akshare 版本迭代经常改列名（"每股收益_调整后(元)" → "每股收益_调整后"，
# 甚至去掉单位后缀），硬编码 df["列名"] 一旦改版就 KeyError 崩全链路。
# 策略：先按候选名**精确匹配**，再按正则**前缀/包含匹配**，都没有 → None（记日志，不抛异常）。
FIN_DATE_CANDIDATES = (
    "日期", "报告期", "报告日期", "报表日期",
    "report_date", "trade_date", "date", "REPORT_DATE",
)
FIN_DATE_PATTERNS = (
    r"^日期$", r"^报告期", r"^报告日期", r"^报表日期", r"^(?:report_date|trade_date|date)$",
)
# EPS：调整后优先，缺失再退摊薄/基本（逐行 coalesce，见 normalize_financials）
FIN_EPS_CANDIDATES = (
    "每股收益_调整后(元)", "每股收益_调整后",
    "eps",
)
FIN_EPS_PATTERNS = (r"^每股收益[_\-]?调整后", r"^eps$")
FIN_EPS_FALLBACK_CANDIDATES = (
    "摊薄每股收益(元)", "摊薄每股收益",
    "基本每股收益(元)", "基本每股收益",
    "每股收益(元)", "每股收益",
)
FIN_EPS_FALLBACK_PATTERNS = (
    r"^摊薄每股收益", r"^基本每股收益", r"^每股收益",
)
# BPS：**调整前优先**（"调整后"实测有脏数据，688981 差 20 倍，见模块 docstring），
# 缺失再退调整后（逐行 coalesce）
FIN_BPS_CANDIDATES = (
    "每股净资产_调整前(元)", "每股净资产_调整前",
    "bps",
)
FIN_BPS_PATTERNS = (r"^每股净资产[_\-]?调整前", r"^bps$")
FIN_BPS_FALLBACK_CANDIDATES = (
    "每股净资产_调整后(元)", "每股净资产_调整后",
    "每股净资产(元)", "每股净资产",
)
FIN_BPS_FALLBACK_PATTERNS = (
    r"^每股净资产[_\-]?调整后", r"^每股净资产",
)
FIN_ROE_CANDIDATES = (
    "净资产收益率(%)", "净资产收益率", "加权净资产收益率(%)", "净资产收益率_摊薄(%)", "roe",
)
FIN_ROE_PATTERNS = (r"^净资产收益率", r"^加权净资产收益率", r"^roe$")
FIN_PAYOUT_CANDIDATES = (
    "股息发放率(%)", "股息发放率", "股利支付率(%)", "股利支付率", "分红率(%)", "payout_ratio",
)
FIN_PAYOUT_PATTERNS = (r"股息发放率", r"股利支付率", r"分红率", r"^payout_ratio$")
FIN_REVENUE_CANDIDATES = (
    "营业总收入(元)", "营业收入(元)", "主营业务收入(元)", "营业总收入", "营业收入", "revenue",
)
FIN_REVENUE_PATTERNS = (r"^(?:营业总收入|营业收入|主营业务收入)", r"^revenue$")

# ---- 巨潮分红（stock_dividend_cninfo）列名候选 ----
# 实测列：实施方案公告日期 / 分红类型 / 送股比例 / 转增比例 / 派息比例 / 股权登记日 /
#         除权日 / 派息日 / 股份到账日 / 实施方案分红说明 / 报告时间
# 其中「派息比例」单位是**每 10 股派 X 元**，入库要 /10 换成每股 dps。
DIV_EX_DATE_CANDIDATES = ("除权日", "除息日", "除权除息日", "ex_date", "EX_DATE")
DIV_EX_DATE_PATTERNS = (r"^除权日", r"^除息日", r"^ex_date$")
DIV_PAY_DATE_CANDIDATES = ("派息日", "派息日期", "现金红利发放日", "股息发放日", "pay_date")
DIV_PAY_DATE_PATTERNS = (r"^派息日", r"派息日期", r"^pay_date$")
DIV_ANNOUNCE_CANDIDATES = (
    "实施方案公告日期", "公告日期", "预案公告日", "分红公告日", "announce_date",
)
DIV_ANNOUNCE_PATTERNS = (r"^实施方案公告日期", r"^公告日期", r"^announce_date$")
DIV_DPS_PER_10_CANDIDATES = (
    "派息比例", "派息比例(元)", "每10股派息(元)", "现金分红比例", "分红比例", "dividend_per_10",
)
DIV_DPS_PER_10_PATTERNS = (r"^派息比例", r"每10股", r"^dividend_per_10$")
DIV_REPORT_PERIOD_CANDIDATES = ("报告时间", "报告期", "report_period")
DIV_REPORT_PERIOD_PATTERNS = (r"^报告时间", r"^报告期", r"^report_period$")

# ---- 新浪财务摘要（stock_financial_abstract：指标 行 × 报告期 列）----
ABSTRACT_METRIC_CANDIDATES = ("指标", "指标名称", "项目", "metric")
ABSTRACT_METRIC_PATTERNS = (r"^指标", r"^项目$", r"^metric$")
ABSTRACT_NET_PROFIT = ("归母净利润", "归属于母公司股东的净利润", "归属于母公司所有者的净利润", "净利润")
ABSTRACT_NET_PROFIT_PATTERNS = (r"^归母净利润", r"归属于母公司", r"^净利润")
ABSTRACT_REVENUE = ("营业总收入", "营业收入")
ABSTRACT_REVENUE_PATTERNS = (r"^营业总收入", r"^营业收入")
ABSTRACT_ROE = ("净资产收益率", "净资产收益率(%)", "ROE")
ABSTRACT_ROE_PATTERNS = (r"^净资产收益率", r"^roe$")
ABSTRACT_TOTAL_SHARE = ("总股本", "实收资本(或股本)", "实收资本", "股本")
ABSTRACT_TOTAL_SHARE_PATTERNS = (r"^总股本", r"^实收资本", r"^股本")

#: 财报里的空值占位符（新浪/东财都出现过）
_EMPTY_TOKENS = {"", "-", "--", "—", "——", "nan", "NaN", "NONE", "None", "none", "null", "NULL"}

#: 进程级限频器：所有新浪请求共用同一个窗口（≤2 请求/秒，见 base.make_history_limiter）
_LIMITER = make_history_limiter()


# ============================================================
# 通用小工具（纯函数）
# ============================================================
def _resolve_column(
    columns: Iterable[Any],
    candidates: Sequence[str],
    patterns: Sequence[str] = (),
) -> Any | None:
    """在 ``columns`` 里按「候选名精确匹配 → 正则匹配」找到目标列，找不到返回 None。

    Args:
        columns: ``df.columns``（可能是 ``pd.Index``）。
        candidates: 候选列名，顺序即优先级。
        patterns: 正则模式，用于列名漂移（版本升级改名）时的兜底匹配。

    Returns:
        命中的**原始**列标签（可直接 ``df[col]``），没有命中返回 ``None``。

    为什么不用 ``df.get(name)``：新浪接口的列名带单位后缀且随版本变化
    （"每股净资产_调整前(元)" / "每股净资产_调整前"），只做精确匹配等于把可用性
    绑死在某一个 akshare 版本上。
    """
    names = [str(column).strip() for column in columns]
    for candidate in candidates:
        if candidate in names:
            return list(columns)[names.index(candidate)]
    for pattern in patterns:
        try:
            regex = re.compile(pattern)
        except re.error:  # pragma: no cover —— 模式写死在本模块，防御
            continue
        for original, name in zip(columns, names):
            if regex.search(name):
                return original
    return None


def _num(value: Any) -> float | None:
    """任意值 → float；无法转换 / NaN / Inf → None（"--"、""、带 % 与千分位都能处理）。"""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("%", "")
        if text in _EMPTY_TOKENS:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _numeric_column(frame: pd.DataFrame, column: Any | None) -> pd.Series | None:
    """安全取数值列：列不存在返回 None；脏字符串（"--"/"1,234"/"12.5%"）先清洗再 coerce。"""
    if column is None or column not in frame.columns:
        return None
    series = frame[column]
    if series.dtype == object or isinstance(series.dtype, pd.StringDtype):
        text = series.astype("string").str.replace(",", "", regex=False)
        text = text.str.replace("%", "", regex=False).str.strip()
        text = text.mask(text.isin(_EMPTY_TOKENS))
        series = text
    return pd.to_numeric(series, errors="coerce").astype("float64")


def _date_series(values: Any) -> pd.Series:
    """任意日期列（date / Timestamp / "2024-03-31" / "20240331" / int） → ``datetime.date`` 序列。

    无效值变 ``NaT``（调用方自己 dropna）。

    为什么**逐值解析**而不是 ``pd.to_datetime(series, errors="coerce")``：后者会按
    第一个元素**推断统一格式**，遇到 ``"20240331"`` 与 ``"2023-12-31"`` 混在一列时
    会把不符合首个格式的值全部判成 NaT（实测 pandas 2.3 的坑）。本函数只用于财报/分红
    这类**小表**（一只股票最多几十行），逐值解析的开销可以忽略。
    """
    series = values if isinstance(values, pd.Series) else pd.Series(list(values))
    if pd.api.types.is_integer_dtype(series):
        series = series.astype("Int64").astype("string").astype(object)
    parsed: list[date | None] = []
    for value in series.astype(object):
        parsed.append(_parse_period(value))
    return pd.Series(parsed, index=series.index, dtype="object")


def _parse_period(value: Any) -> date | None:
    """把一个"报告期"值解析成 date（支持 20260630 / "2026-06-30" / date / Timestamp）。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in _EMPTY_TOKENS:
        return None
    if text.isdigit() and len(text) == 8:
        try:
            return datetime.strptime(text, "%Y%m%d").date()
        except ValueError:
            return None
    parsed = pd.to_datetime(text, errors="coerce")
    if parsed is pd.NaT or (not isinstance(parsed, pd.Timestamp) and pd.isna(parsed)):
        return None
    return parsed.date()


def _finite(series: pd.Series) -> pd.Series:
    """把 ±Inf 换成 NaN（``Numeric`` 列不能写 Inf，PG 会直接报错）。"""
    return series.replace([np.inf, -np.inf], np.nan)


def _empty_financials() -> pd.DataFrame:
    """归一化财报表的空表（列齐全，下游不用判断列是否存在）。"""
    return pd.DataFrame(
        {
            "report_date": pd.Series(dtype="object"),
            "eps": pd.Series(dtype="float64"),
            "bps": pd.Series(dtype="float64"),
            "roe": pd.Series(dtype="float64"),
            "payout_ratio": pd.Series(dtype="float64"),
        }
    )


def _empty_ttm() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "report_date": pd.Series(dtype="object"),
            "eps_ttm": pd.Series(dtype="float64"),
            "eps_ttm_basis": pd.Series(dtype="object"),
        }
    )


def _empty_valuation() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": pd.Series(dtype="object"),
            "pe_ttm": pd.Series(dtype="float64"),
            "pb": pd.Series(dtype="float64"),
            "dv_ttm": pd.Series(dtype="float64"),
            "report_date": pd.Series(dtype="object"),
        }
    )


def _empty_financials_ttm() -> pd.DataFrame:
    """``fetch_financials`` 的返回口径（归一化列 + TTM 列 + revenue）。"""
    frame = _empty_financials()
    frame["eps_ttm"] = pd.Series(dtype="float64")
    frame["eps_ttm_basis"] = pd.Series(dtype="object")
    frame["revenue"] = pd.Series(dtype="float64")
    return frame[list(FIN_COLUMNS) + ["eps_ttm", "eps_ttm_basis", "revenue"]]


def _coalesce_numeric(
    frame: pd.DataFrame,
    primary_candidates: Sequence[str],
    primary_patterns: Sequence[str],
    fallback_candidates: Sequence[str],
    fallback_patterns: Sequence[str],
) -> pd.Series | None:
    """两列数值的**逐行**合并：主列有值用主列，主列为空/缺列时用兜底列。

    "逐行"很关键：``每股净资产_调整前`` 在某些行是 ``"--"``（空）但在另一行有值，
    按"整列优先级"选列会把整列都丢掉。两列都不存在时返回 None（调用方补全 NaN 列）。
    """
    primary = _numeric_column(
        frame, _resolve_column(frame.columns, primary_candidates, primary_patterns)
    )
    fallback = _numeric_column(
        frame, _resolve_column(frame.columns, fallback_candidates, fallback_patterns)
    )
    if primary is None:
        return fallback
    if fallback is None:
        return primary
    return primary.where(primary.notna(), fallback)


# ============================================================
# 纯函数 1：归一化新浪财报指标表
# ============================================================
def normalize_financials(raw: pd.DataFrame | None) -> pd.DataFrame:
    """把新浪 ``stock_financial_analysis_indicator`` 的原始表归一化。

    输出列（按 ``report_date`` **升序**）：``[report_date, eps, bps, roe, payout_ratio]``

    取值规则（缺列 → 该列全 NaN，**绝不 KeyError**；主列为空时**逐行**退兜底列）：
        - ``eps``：优先 ``每股收益_调整后``，空则退 ``摊薄每股收益``（再兜底 ``基本每股收益``）；
        - ``bps``：优先 ``每股净资产_调整前``，空则退 ``每股净资产_调整后``。
          **为什么"调整前"优先**：``每股净资产_调整后`` 有脏数据 —— 实测 688981
          （中芯国际）``调整后=594.8588`` vs ``调整前=25.7375``（真实 BPS 约 19–26），
          用"调整后"算 PB 会错 20 倍；
        - ``roe``：``净资产收益率``；
        - ``payout_ratio``：``股息发放率``（%，只作参考入库，**不再用于算股息率** ——
          股息率走巨潮分红明细，见 :func:`dv_ttm_series`）。

    列名匹配是容错的（候选名 + 正则前缀匹配），因为 akshare 改版会改列名/去单位后缀。
    数值列强制 ``pd.to_numeric(errors="coerce")``，``"--"``/``""``/``"12.5%"`` 这类脏值
    → NaN 而不是让后面算 PE 时报错或写进脏字符串。

    Args:
        raw: 原始 DataFrame（可为 None / 空表）。

    Returns:
        归一化后的 DataFrame（空输入返回同列空表）。
    """
    if raw is None or len(raw) == 0:
        return _empty_financials()

    date_column = _resolve_column(raw.columns, FIN_DATE_CANDIDATES, FIN_DATE_PATTERNS)
    if date_column is None:
        logger.warning(
            "财报指标表缺少日期列（现有列：%s），无法归一化",
            [str(c) for c in list(raw.columns)[:12]],
        )
        return _empty_financials()

    eps = _coalesce_numeric(
        raw, FIN_EPS_CANDIDATES, FIN_EPS_PATTERNS,
        FIN_EPS_FALLBACK_CANDIDATES, FIN_EPS_FALLBACK_PATTERNS,
    )
    bps = _coalesce_numeric(
        raw, FIN_BPS_CANDIDATES, FIN_BPS_PATTERNS,
        FIN_BPS_FALLBACK_CANDIDATES, FIN_BPS_FALLBACK_PATTERNS,
    )
    frame = pd.DataFrame(
        {
            "report_date": _date_series(raw[date_column]),
            "eps": eps,
            "bps": bps,
            "roe": _numeric_column(raw, _resolve_column(raw.columns, FIN_ROE_CANDIDATES,
                                                        FIN_ROE_PATTERNS)),
            "payout_ratio": _numeric_column(
                raw, _resolve_column(raw.columns, FIN_PAYOUT_CANDIDATES, FIN_PAYOUT_PATTERNS)
            ),
        }
    )
    for column in ("eps", "bps", "roe", "payout_ratio"):
        if frame[column].isna().all():
            frame[column] = pd.Series(np.nan, index=frame.index, dtype="float64")

    frame = frame.dropna(subset=["report_date"])
    if frame.empty:
        return _empty_financials()

    # 同一报告期多行（新浪偶尔返回重复期）时保留最后一行（最新修订）
    frame = frame.drop_duplicates(subset=["report_date"], keep="last")
    frame = frame.sort_values("report_date", kind="mergesort").reset_index(drop=True)
    return frame[list(FIN_COLUMNS)]


# ============================================================
# 纯函数 2：累计口径 EPS → TTM
# ============================================================
def eps_ttm_series(fins: pd.DataFrame) -> pd.DataFrame:
    """由**季度累计**口径的 EPS 组装 TTM（trailing twelve months）。

    口径推导（A 股财报是"年初至今累计"而不是单季度）::

        报告期 MMDD = 1231（年报）：
            eps_ttm = 该期累计值                      # 12 个月，正好是 TTM

        其他季度（0331 / 0630 / 0930）：
            eps_ttm = 本期累计 + 上年年报累计 − 上年同期累计
                    现年 YTD    + 去年 FY       − 去年 YTD

        例：2024-03-31 的一季报 TTM
            = 2024Q1(0.40) + 2023FY(1.20) − 2023Q1(0.30) = 1.30

    三期里**任一期缺失**就返回 NaN（不做线性外推：外推出来的 PE 在策略里没有任何
    可信度，宁可返回 NULL 让策略层跳过）。1231 特例说明基础期就是完整一年，不需要差分。

    Args:
        fins: 至少包含 ``report_date`` / ``eps`` 两列（``normalize_financials`` 的输出）。

    Returns:
        ``[report_date, eps_ttm, eps_ttm_basis]``，按 ``report_date`` 升序。
        ``eps_ttm_basis`` 记录用了哪三期（如 ``"2024-03-31+2023-12-31-2023-03-31"``），
        便于回溯"这个 PE 是用哪几份财报算的"。
    """
    if fins is None or len(fins) == 0:
        return _empty_ttm()
    frame = pd.DataFrame(fins)
    if "report_date" not in frame.columns:
        logger.warning("eps_ttm_series 缺少 report_date 列，返回空表")
        return _empty_ttm()

    frame = frame.copy()
    frame["report_date"] = _date_series(frame["report_date"])
    frame["eps"] = (
        pd.to_numeric(frame["eps"], errors="coerce")
        if "eps" in frame.columns
        else pd.Series(np.nan, index=frame.index, dtype="float64")
    )
    frame = frame.dropna(subset=["report_date"])
    if frame.empty:
        return _empty_ttm()
    frame = frame.drop_duplicates(subset=["report_date"], keep="last")
    frame = frame.sort_values("report_date", kind="mergesort").reset_index(drop=True)

    # 报告期很稀疏（一只股票 8 年也就 32 行），用 dict 查表比逐行累积更清楚也更快
    eps_by_period: dict[date, float | None] = {}
    for period, value in zip(frame["report_date"], frame["eps"]):
        eps_by_period[period] = None if pd.isna(value) else float(value)

    records: list[dict[str, Any]] = []
    for period in eps_by_period:
        current = eps_by_period[period]
        if period.month == 12 and period.day == 31:
            eps_ttm = current
            basis = f"{period.isoformat()}(annual)"
        else:
            prior_annual = date(period.year - 1, 12, 31)
            prior_same = date(period.year - 1, period.month, period.day)
            base_annual = eps_by_period.get(prior_annual)
            base_same = eps_by_period.get(prior_same)
            if current is None or base_annual is None or base_same is None:
                eps_ttm = None
            else:
                eps_ttm = current + base_annual - base_same
            basis = (
                f"{period.isoformat()}+{prior_annual.isoformat()}"
                f"-{prior_same.isoformat()}"
            )
        records.append(
            {"report_date": period, "eps_ttm": eps_ttm, "eps_ttm_basis": basis}
        )

    result = pd.DataFrame(records, columns=list(TTM_COLUMNS))
    result["eps_ttm"] = pd.to_numeric(result["eps_ttm"], errors="coerce")
    return result


# ============================================================
# 纯函数 3：按报告期 → 按交易日展开（point-in-time）
# ============================================================
def _prepare_financials(fins: pd.DataFrame | None, lag_days: int) -> pd.DataFrame:
    """把财报表整理成"可用日期"维度，供 ``merge_asof`` 使用。

    输入兼容两种形态：
        1. ``normalize_financials`` (+ ``eps_ttm_series``) 的输出（列：eps/bps/roe/payout_ratio/...）；
        2. ``dim_stock_financial`` 的行（列：code/report_date/announced_date/eps_ttm/bps/payout_ratio）。

    point-in-time 可用日期 ``available_date`` 的取值：
        - 有 ``announced_date`` → 用它（阶段 1 落库时算好的 报告期+滞后）；
        - 没有（NaN/None）→ ``report_date + lag_days``（本模块内部推导的兜底口径）。

    防御：``announced_date`` 早于 ``report_date`` 属于脏数据（会让"未来财报"提前可用），
    这种行回落到 ``report_date + lag_days``。

    Returns:
        ``[report_date, eps_ttm, bps, payout_ratio, available_date]``，按 ``available_date`` 升序。
    """
    columns = ["report_date", "eps_ttm", "bps", "payout_ratio", "available_date"]
    if fins is None:
        return pd.DataFrame({column: pd.Series(dtype="object") for column in columns})
    frame = pd.DataFrame(fins)
    if len(frame) == 0 or "report_date" not in frame.columns:
        return pd.DataFrame({column: pd.Series(dtype="object") for column in columns})

    frame = frame.copy()
    frame["report_date"] = _date_series(frame["report_date"])
    frame = frame.dropna(subset=["report_date"])
    if frame.empty:
        return pd.DataFrame({column: pd.Series(dtype="object") for column in columns})

    # --- eps_ttm：优先用已有列；缺失列（或整列全 NaN）时由 eps 现场推导 ---
    if "eps_ttm" in frame.columns:
        frame["eps_ttm"] = pd.to_numeric(frame["eps_ttm"], errors="coerce")
    else:
        frame["eps_ttm"] = np.nan
    if frame["eps_ttm"].isna().all() and "eps" in frame.columns:
        frame["eps"] = pd.to_numeric(frame["eps"], errors="coerce")
        if frame["eps"].notna().any():
            ttm = eps_ttm_series(frame)
            frame = frame.drop(columns=[c for c in ("eps_ttm_basis",) if c in frame.columns])
            frame = frame.drop(columns=["eps_ttm"]).merge(
                ttm[["report_date", "eps_ttm"]], on="report_date", how="left"
            )

    for column in ("bps", "payout_ratio"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        else:
            frame[column] = np.nan

    frame = frame.sort_values("report_date", kind="mergesort")
    frame = frame.drop_duplicates(subset=["report_date"], keep="last")

    # --- point-in-time 可用日期 ---
    period_dt = pd.to_datetime(frame["report_date"])
    fallback = period_dt + pd.Timedelta(days=int(lag_days))
    if "announced_date" in frame.columns:
        announced = pd.to_datetime(frame["announced_date"].astype(object), errors="coerce")
        # 缺失（NULL）→ 回落到 report_date + lag；脏数据（早于报告期）也不可信，同样回落
        available = announced.fillna(fallback)
        invalid = announced.notna() & (announced < period_dt)
        available = available.where(~invalid, fallback)
    else:
        available = fallback
    frame["available_date"] = available

    frame = frame.dropna(subset=["available_date"])
    frame = frame.sort_values(["available_date", "report_date"], kind="mergesort")
    frame = frame.drop_duplicates(subset=["available_date"], keep="last")
    return frame[columns].reset_index(drop=True)


def build_valuation_frame(
    prices: pd.DataFrame,
    fins: pd.DataFrame,
    lag_days: int = REPORT_LAG_DAYS,
) -> pd.DataFrame:
    """把"按报告期"的估值因子展开到"按交易日"（point-in-time，禁止未来函数）。

    对每个交易日 ``d``，取 ``available_date <= d`` 的**最新**报告期（``available_date`` =
    ``announced_date``，没有则 ``report_date + lag_days``）。核心是
    ``pd.merge_asof(direction="backward")``：向量化、不逐行 apply，5 年全市场也就秒级。

    计算口径::

        pe_ttm    = close / eps_ttm          # eps_ttm 为 NaN/0 → NaN；**允许负值**
        pb        = close / bps              # bps <= 0（资不抵债）→ NaN
        dv_ttm(%) = payout_ratio/100 * eps_ttm / close * 100   # payout 缺失 → NaN

    注意：这里的 ``dv_ttm`` 是**旧口径**（分红发放率 × EPS / 股价），只保留给纯函数/单测
    与"没有分红明细时的应急口径"。生产链路（:func:`compute_valuation`）会用
    :func:`dv_ttm_series` 的**巨潮分红明细口径**覆盖它 —— 因为新浪"股息发放率"大量为空，
    且语义是"分红/净利润"而不是股息率。

    Args:
        prices: ``[trade_date, close]``（升序；可多一列 ``code`` 见下）。``trade_date``
            可以是 date / Timestamp / 字符串。
        fins: 见 :func:`_prepare_financials` 的两种输入形态；多报告期、乱序都可以。
        lag_days: 没有 ``announced_date`` 时的披露滞后（自然日），默认 90。

    Returns:
        ``[trade_date, pe_ttm, pb, dv_ttm, report_date]``，**行序与 ``prices`` 一致**
        （内部排序只为 merge_asof 服务，输出会还原）。``trade_date`` / ``report_date``
        是 ``datetime.date``；缺失的因子是 ``NaN``（写库时统一转 NULL）。

    多只股票一起算：``prices`` 与 ``fins`` **都带 ``code`` 列**时按 code 分组递归计算，
    输出会在最前面多一列 ``code``（当日全市场一次算完用得上）。否则按单只股票处理
    （``fins`` 里的 ``code`` 列会被忽略，因为价格序列不带代码）。
    """
    if prices is None or len(prices) == 0:
        return _empty_valuation()

    price_frame = pd.DataFrame(prices).copy()
    if "trade_date" not in price_frame.columns or "close" not in price_frame.columns:
        logger.error(
            "build_valuation_frame 的 prices 缺少 trade_date/close 列（现有：%s），返回空表",
            [str(c) for c in price_frame.columns],
        )
        return _empty_valuation()

    fins_frame = pd.DataFrame(fins) if fins is not None else None
    if (
        "code" in price_frame.columns
        and fins_frame is not None
        and "code" in fins_frame.columns
    ):
        pieces: list[pd.DataFrame] = []
        for code, price_group in price_frame.groupby("code", sort=False):
            fin_group = fins_frame[fins_frame["code"] == code]
            piece = build_valuation_frame(
                price_group.drop(columns=["code"]), fin_group, lag_days=lag_days
            )
            piece.insert(0, "code", code)
            pieces.append(piece)
        if not pieces:
            return _empty_valuation()
        return pd.concat(pieces, ignore_index=True)

    price_frame["trade_date"] = pd.to_datetime(price_frame["trade_date"], errors="coerce")
    price_frame["close"] = pd.to_numeric(price_frame["close"], errors="coerce")
    price_frame = price_frame.dropna(subset=["trade_date"]).reset_index(drop=True)
    if price_frame.empty:
        return _empty_valuation()
    # 输出要还原调用方行序，先记下位置（外部看到的就是"输入什么顺序输出什么顺序"）
    price_frame["_order"] = np.arange(len(price_frame))
    left = price_frame.sort_values("trade_date", kind="mergesort")

    prepared = _prepare_financials(fins_frame, lag_days)
    if prepared.empty:
        merged = left.copy()
        merged["report_date"] = pd.NaT
        merged["eps_ttm"] = np.nan
        merged["bps"] = np.nan
        merged["payout_ratio"] = np.nan
    else:
        right = prepared.sort_values("available_date", kind="mergesort")
        merged = pd.merge_asof(
            left,
            right,
            left_on="trade_date",
            right_on="available_date",
            direction="backward",
            allow_exact_matches=True,
        )

    close = pd.to_numeric(merged["close"], errors="coerce")
    eps = pd.to_numeric(merged["eps_ttm"], errors="coerce")
    bps = pd.to_numeric(merged["bps"], errors="coerce")
    payout = pd.to_numeric(merged["payout_ratio"], errors="coerce")

    pe = close / eps
    pe = _finite(pe.where(close.notna() & eps.notna() & (eps != 0)))
    pb = close / bps
    pb = _finite(pb.where(close.notna() & bps.notna() & (bps > 0)))
    dv = (payout / 100.0) * eps / close * 100.0
    dv = _finite(
        dv.where(close.notna() & (close > 0) & eps.notna() & (eps != 0) & payout.notna())
    )

    result = pd.DataFrame(
        {
            "trade_date": merged["trade_date"].dt.date,
            "pe_ttm": pe,
            "pb": pb,
            "dv_ttm": dv,
            "report_date": merged["report_date"],
            "_order": merged["_order"],
        }
    )
    # report_date 统一成 date / None（DatetimeNaT → None，方便写日志和测试断言）
    periods = pd.to_datetime(result["report_date"], errors="coerce")
    result["report_date"] = periods.dt.date.astype(object).where(periods.notna(), None)

    result = result.sort_values("_order", kind="mergesort").reset_index(drop=True)
    return result[list(VALUATION_COLUMNS)]


# ============================================================
# 纯函数 4：分红归一化 + 股息率（近 12 个月分红 / 股价）
# ============================================================
def _empty_dividends() -> pd.DataFrame:
    """归一化分红表的空表。"""
    return pd.DataFrame(
        {
            "ex_date": pd.Series(dtype="object"),
            "announce_date": pd.Series(dtype="object"),
            "dps": pd.Series(dtype="float64"),
            "report_period": pd.Series(dtype="object"),
        }
    )


def normalize_dividends(raw: pd.DataFrame | None) -> pd.DataFrame:
    """把巨潮 ``stock_dividend_cninfo`` 的原始表归一化。

    输出列（按 ``ex_date`` 升序）：``[ex_date, announce_date, dps, report_period]``

    换算口径（**关键**）：
        - ``派息比例`` 的单位是**每 10 股派 X 元**（实测 600519 的方案是 6.0 元/10 股），
          所以 ``dps = 派息比例 / 10``（元/股）。``6.0 → 0.6``；
        - ``ex_date`` 优先「除权日」；为空时退「派息日」；再为空退「实施方案公告日期」
          （这两种兜底会让 point-in-time 略微提前/滞后，所以记 warning 便于抽查）；
        - 只保留**现金分红**行：``派息比例`` 为空/<=0（纯送股、纯转增方案）直接跳过。

    同一 ``ex_date`` 出现多行（例如年度方案 + 特别分红）时 ``dps`` 求和、公告日取最新。

    Args:
        raw: 巨潮分红原始表（可为 None / 空表）。

    Returns:
        归一化后的 DataFrame（空输入返回同列空表）。
    """
    if raw is None or len(raw) == 0:
        return _empty_dividends()

    dps_per_10_column = _resolve_column(
        raw.columns, DIV_DPS_PER_10_CANDIDATES, DIV_DPS_PER_10_PATTERNS
    )
    if dps_per_10_column is None:
        logger.warning(
            "分红表缺少「派息比例」列（现有列：%s），无法归一化",
            [str(c) for c in list(raw.columns)[:12]],
        )
        return _empty_dividends()

    ex_column = _resolve_column(raw.columns, DIV_EX_DATE_CANDIDATES, DIV_EX_DATE_PATTERNS)
    pay_column = _resolve_column(raw.columns, DIV_PAY_DATE_CANDIDATES, DIV_PAY_DATE_PATTERNS)
    announce_column = _resolve_column(
        raw.columns, DIV_ANNOUNCE_CANDIDATES, DIV_ANNOUNCE_PATTERNS
    )
    period_column = _resolve_column(
        raw.columns, DIV_REPORT_PERIOD_CANDIDATES, DIV_REPORT_PERIOD_PATTERNS
    )

    ex_dates = _date_series(raw[ex_column]) if ex_column is not None else pd.Series(
        pd.NaT, index=raw.index
    )
    pay_dates = _date_series(raw[pay_column]) if pay_column is not None else pd.Series(
        pd.NaT, index=raw.index
    )
    announce_dates = _date_series(raw[announce_column]) if announce_column is not None else (
        pd.Series(pd.NaT, index=raw.index)
    )
    dps = _numeric_column(raw, dps_per_10_column)
    assert dps is not None  # 上面已确认列存在
    dps = dps / 10.0  # 每 10 股派 X 元 → 每股

    frame = pd.DataFrame(
        {
            "ex_date": ex_dates,
            "announce_date": announce_dates,
            "dps": dps,
            "report_period": (
                raw[period_column].astype("string").str.slice(0, 16)
                if period_column is not None
                else pd.Series(pd.NA, index=raw.index, dtype="string")
            ),
        }
    )
    # ex_date 兜底链：除权日 → 派息日 → 公告日
    fallback_mask = frame["ex_date"].isna() & pay_dates.notna()
    if fallback_mask.any():
        frame.loc[fallback_mask, "ex_date"] = pay_dates[fallback_mask]
    second_mask = frame["ex_date"].isna() & announce_dates.notna()
    if second_mask.any():
        frame.loc[second_mask, "ex_date"] = announce_dates[second_mask]
        logger.warning(
            "分红缺少除权日/派息日，%d 行回落到公告日（point-in-time 可能不严谨）",
            int(second_mask.sum()),
        )
    if fallback_mask.any():
        logger.debug("分红缺少除权日，%d 行回落到派息日", int(fallback_mask.sum()))
    # 兜底赋值可能引入 Timestamp 等类型，统一再收敛一次成 datetime.date
    frame["ex_date"] = _date_series(frame["ex_date"])

    frame = frame.dropna(subset=["ex_date"])
    # 只留现金分红（纯送股/转增方案的派息比例是 0 或空）
    frame = frame[frame["dps"].notna() & (frame["dps"] > 0)]
    if frame.empty:
        return _empty_dividends()

    # 同一除权日多方案 → dps 求和（都是真实现金分红），公告日/report_period 取最新
    grouped = (
        frame.groupby("ex_date", as_index=False)
        .agg(
            announce_date=("announce_date", "max"),
            dps=("dps", "sum"),
            report_period=("report_period", "last"),
        )
        .sort_values("ex_date", kind="mergesort")
        .reset_index(drop=True)
    )
    grouped["dps"] = pd.to_numeric(grouped["dps"], errors="coerce").astype("float64")
    return grouped[list(DIVIDEND_COLUMNS)]


def _prepare_dividends(dividends: pd.DataFrame | None) -> pd.DataFrame:
    """把分红表整理成 ``[ex_date, dps]``（去无效、合并同除权日、升序）。"""
    if dividends is None or len(dividends) == 0:
        return _empty_dividends()[["ex_date", "dps"]]
    frame = pd.DataFrame(dividends).copy()
    if "ex_date" not in frame.columns:
        if "date" in frame.columns:  # 宽容：别的表可能叫 date
            frame = frame.rename(columns={"date": "ex_date"})
        else:
            return _empty_dividends()[["ex_date", "dps"]]
    if "dps" not in frame.columns:
        return _empty_dividends()[["ex_date", "dps"]]
    frame["ex_date"] = _date_series(frame["ex_date"])
    frame["dps"] = pd.to_numeric(frame["dps"], errors="coerce")
    frame = frame.dropna(subset=["ex_date"])
    frame = frame[frame["dps"].notna() & (frame["dps"] > 0)]
    if frame.empty:
        return _empty_dividends()[["ex_date", "dps"]]
    frame = (
        frame.groupby("ex_date", as_index=False)["dps"]
        .sum()
        .sort_values("ex_date", kind="mergesort")
        .reset_index(drop=True)
    )
    return frame[["ex_date", "dps"]]


def dv_ttm_series(
    prices: pd.DataFrame,
    dividends: pd.DataFrame,
    window_days: int = DV_WINDOW_DAYS,
) -> pd.DataFrame:
    """按交易日算近 12 个月股息率（%）。

    口径::

        dv_ttm(d) = Σ{ dps | ex_date ∈ (d − window_days, d] } / close(d) × 100

    - **除权日对齐**：分红在除权日收盘后生效，所以 ``d >= ex_date`` 的交易日才开始计入
      （窗口左开右闭）；提前入库但还没除权的方案不会污染历史（point-in-time）；
    - 完全没有分红记录 → **0.0**（真实的"无股息"，不是数据缺失；A4 用 ``dv_ttm >= 4``
      过滤，0 不会误入）；
    - 没有有效收盘价（NaN/<=0）→ **NaN**；
    - **向量化**：把每笔分红转换成一个"开始计入 / 停止计入"的位置差分数组
      （``np.searchsorted`` + ``np.cumsum``），复杂度 O((n+m) log n)，不逐日 apply。

    Args:
        prices: ``[trade_date, close]``（可多一列 ``code``，见下）。
        dividends: ``[ex_date, dps]``（``normalize_dividends`` 或 ``dim_stock_dividend`` 的行）。
        window_days: 回看窗口（自然日），默认 365。

    Returns:
        ``[trade_date, dv_ttm]``，**行序与 ``prices`` 一致**。``trade_date`` 是
        ``datetime.date``。

    多只股票一起算：``prices`` 与 ``dividends`` **都带 ``code`` 列**时按 code 分组递归计算，
    输出最前面多一列 ``code``。
    """
    columns = ["trade_date", "dv_ttm"]
    if prices is None or len(prices) == 0:
        return pd.DataFrame({column: pd.Series(dtype="object") for column in columns})
    price_frame = pd.DataFrame(prices).copy()
    if "trade_date" not in price_frame.columns or "close" not in price_frame.columns:
        logger.error(
            "dv_ttm_series 的 prices 缺少 trade_date/close 列（现有：%s），返回空表",
            [str(c) for c in price_frame.columns],
        )
        return pd.DataFrame({column: pd.Series(dtype="object") for column in columns})

    dividend_frame = pd.DataFrame(dividends) if dividends is not None else None
    if (
        "code" in price_frame.columns
        and dividend_frame is not None
        and "code" in dividend_frame.columns
    ):
        pieces: list[pd.DataFrame] = []
        for code, price_group in price_frame.groupby("code", sort=False):
            dividend_group = dividend_frame[dividend_frame["code"] == code]
            piece = dv_ttm_series(
                price_group.drop(columns=["code"]), dividend_group, window_days=window_days
            )
            piece.insert(0, "code", code)
            pieces.append(piece)
        if not pieces:
            return pd.DataFrame({column: pd.Series(dtype="object") for column in columns})
        return pd.concat(pieces, ignore_index=True)

    price_frame["trade_date"] = pd.to_datetime(price_frame["trade_date"], errors="coerce")
    price_frame["close"] = pd.to_numeric(price_frame["close"], errors="coerce")
    price_frame = price_frame.dropna(subset=["trade_date"]).reset_index(drop=True)
    if price_frame.empty:
        return pd.DataFrame({column: pd.Series(dtype="object") for column in columns})
    price_frame["_order"] = np.arange(len(price_frame))
    ordered = price_frame.sort_values("trade_date", kind="mergesort").reset_index(drop=True)
    trade_days = ordered["trade_date"].to_numpy()
    count = len(ordered)

    # 差分数组：diff[start] += dps（从该交易日开始计入），diff[end] -= dps（窗口滑出）
    diff = np.zeros(count + 1, dtype="float64")
    prepared = _prepare_dividends(dividend_frame)
    if not prepared.empty:
        ex_days = pd.to_datetime(prepared["ex_date"]).to_numpy()
        dps = pd.to_numeric(prepared["dps"], errors="coerce").fillna(0.0).to_numpy()
        starts = np.searchsorted(trade_days, ex_days, side="left")
        ends = np.searchsorted(
            trade_days, ex_days + np.timedelta64(int(window_days), "D"), side="left"
        )
        # 有效事件：有现金分红，且除权日之后至少还有一个交易日
        valid = (dps != 0) & (starts < count)
        if valid.any():
            np.add.at(diff, starts[valid], dps[valid])
            np.add.at(diff, np.clip(ends[valid], 0, count), -dps[valid])
    rolling = np.cumsum(diff)[:count]

    close = pd.to_numeric(ordered["close"], errors="coerce").to_numpy(dtype="float64")
    with np.errstate(divide="ignore", invalid="ignore"):
        dv = np.where(close > 0, rolling / close * 100.0, np.nan)
    dv = np.where(np.isfinite(dv), dv, np.nan)

    result = pd.DataFrame(
        {
            "trade_date": ordered["trade_date"].dt.date,
            "dv_ttm": dv,
            "_order": ordered["_order"],
        }
    )
    result = result.sort_values("_order", kind="mergesort").reset_index(drop=True)
    return result[columns]


# ============================================================
# 网络层：新浪财报（只被阶段 1 使用；阶段 2 绝不联网）
# ============================================================
def _network_enabled() -> bool:
    """是否允许联网（离线开发/CI 用 ``INGEST_NETWORK_ENABLED=0`` 全链路降级）。"""
    return bool(getattr(config, "INGEST_NETWORK_ENABLED", True))


async def _ak_call(func_name: str, **kwargs: Any) -> pd.DataFrame | None:
    """调用一个 akshare 函数：线程池执行 + 限频 + 异常收敛（任何失败都返回 None，不抛）。

    这是本模块**唯一**的联网出口。阶段 2（:func:`compute_valuation`）完全不经过这里，
    所以它的"零网络"性质可以直接用"把这个函数换成会抛异常的桩"来测（见测试）。
    """
    if ak is None:
        logger.warning("akshare 未安装，跳过财报抓取（%s）", func_name)
        return None
    if not _network_enabled():
        logger.info("INGEST_NETWORK_ENABLED=0，跳过联网抓取（%s）", func_name)
        return None
    func = getattr(ak, func_name, None)
    if func is None:
        # 版本差异：接口被移除/改名（例如 1.19.1 已经没有 stock_a_indicator_lg）
        logger.warning("akshare 缺少函数 %s（版本差异），跳过", func_name)
        return None
    await _LIMITER.acquire()
    try:
        result = await asyncio.to_thread(func, **kwargs)
    except NotImplementedError:
        raise
    except Exception as exc:  # noqa: BLE001 —— 外部库异常类型不可控，统一收敛成"无数据"
        logger.warning("akshare 调用失败 %s: %s: %s", func_name, type(exc).__name__, exc)
        return None
    if not isinstance(result, pd.DataFrame) or result.empty:
        return None
    return result


def _normalize_code(code: Any) -> str:
    """统一 6 位股票代码（去后缀/补零）。"""
    digits = "".join(ch for ch in str(code) if ch.isdigit())
    return digits.zfill(6)[-6:] if digits else str(code)


def _sina_symbol(code: str) -> str:
    """6 位代码 → 新浪接口的带交易所前缀代码（sh600519 / sz000001 / bj830799）。"""
    digits = _normalize_code(code)
    if digits.startswith(("6", "9")):
        return f"sh{digits}"
    if digits.startswith(("0", "2", "3")):
        return f"sz{digits}"
    if digits.startswith(("4", "8")):
        return f"bj{digits}"
    return f"sh{digits}"


def _extract_revenue_map(raw: pd.DataFrame | None) -> dict[date, float]:
    """从财报指标原始表里抽 ``report_date -> 营业总收入``（列不存在就返回空 dict）。"""
    if raw is None or len(raw) == 0:
        return {}
    date_column = _resolve_column(raw.columns, FIN_DATE_CANDIDATES, FIN_DATE_PATTERNS)
    revenue_column = _resolve_column(raw.columns, FIN_REVENUE_CANDIDATES, FIN_REVENUE_PATTERNS)
    if date_column is None or revenue_column is None:
        return {}
    periods = _date_series(raw[date_column])
    values = _numeric_column(raw, revenue_column)
    if values is None:
        return {}
    mapping: dict[date, float] = {}
    for period, value in zip(periods, values):
        if pd.isna(period) or pd.isna(value):
            continue
        mapping[period] = float(value)
    return mapping


def normalize_abstract_metrics(raw: pd.DataFrame | None) -> pd.DataFrame:
    """把新浪 ``stock_financial_abstract``（``指标`` 行 × ``报告期`` 列）转成宽表。

    输出列：``[report_date, net_profit, revenue, roe, total_share]``（按报告期升序）。

    为什么需要它：``stock_financial_analysis_indicator`` 偶尔缺 EPS 列（版本差异），
    财务摘要里的「归母净利润」是最后一道兜底：EPS = 归母净利润 / 股本（近似，见
    :func:`abstract_eps_frame` 的注释）。
    """
    columns = ["report_date", "net_profit", "revenue", "roe", "total_share"]
    if raw is None or len(raw) == 0:
        return pd.DataFrame({column: pd.Series(dtype="object") for column in columns})

    metric_column = _resolve_column(
        raw.columns, ABSTRACT_METRIC_CANDIDATES, ABSTRACT_METRIC_PATTERNS
    )
    if metric_column is None:
        logger.warning("财务摘要缺少「指标」列，无法解析")
        return pd.DataFrame({column: pd.Series(dtype="object") for column in columns})

    # 除「指标」列外，列名就是报告期（"20260630"）
    periods: list[tuple[Any, date]] = []
    for column in raw.columns:
        if column == metric_column:
            continue
        parsed = _parse_period(column)
        if parsed is not None:
            periods.append((column, parsed))
    if not periods:
        logger.warning("财务摘要未识别出任何报告期列（列名样例：%s）",
                       [str(c) for c in list(raw.columns)[:8]])
        return pd.DataFrame({column: pd.Series(dtype="object") for column in columns})

    targets = {
        "net_profit": (ABSTRACT_NET_PROFIT, ABSTRACT_NET_PROFIT_PATTERNS),
        "revenue": (ABSTRACT_REVENUE, ABSTRACT_REVENUE_PATTERNS),
        "roe": (ABSTRACT_ROE, ABSTRACT_ROE_PATTERNS),
        "total_share": (ABSTRACT_TOTAL_SHARE, ABSTRACT_TOTAL_SHARE_PATTERNS),
    }
    row_of: dict[str, Any] = {}
    for _, row in raw.iterrows():
        name = str(row[metric_column]).strip()
        for target, (candidates, patterns) in targets.items():
            if target in row_of:
                continue
            if name in candidates or any(
                re.search(pattern, name) for pattern in patterns
            ):
                row_of[target] = row
                break

    if "net_profit" not in row_of:
        return pd.DataFrame({column: pd.Series(dtype="object") for column in columns})

    records: list[dict[str, Any]] = []
    for column, period in periods:
        item: dict[str, Any] = {"report_date": period}
        for target in targets:
            row = row_of.get(target)
            item[target] = _num(row[column]) if row is not None else None
        records.append(item)
    frame = pd.DataFrame(records, columns=columns)
    frame = frame.sort_values("report_date", kind="mergesort").reset_index(drop=True)
    return frame


def abstract_eps_frame(
    metrics: pd.DataFrame,
    fallback_share: float | None = None,
) -> pd.DataFrame:
    """由财务摘要的「归母净利润 / 股本」反推 EPS（**近似兜底**，仅在指标表缺 EPS 时用）。

    口径说明（务必了解其局限）：
        - 有「总股本」行 → 用当期股本，口径接近真实摊薄 EPS；
        - 没有股本 → 用 ``fallback_share``（新浪日线的 ``outstanding_share``，**流通股本**），
          流通股本 < 总股本时 EPS 会被高估、PE 被低估。这属于"有数总比没有强"的兜底，
          日志会明确标记，便于事后核对。

    返回 ``[report_date, eps, roe, revenue]``；股本完全拿不到时返回空表（宁缺勿错）。
    """
    columns = ["report_date", "eps", "roe", "revenue"]
    if metrics is None or len(metrics) == 0:
        return pd.DataFrame({column: pd.Series(dtype="object") for column in columns})
    frame = metrics.copy()
    if "net_profit" not in frame.columns or "report_date" not in frame.columns:
        return pd.DataFrame({column: pd.Series(dtype="object") for column in columns})

    net_profit = pd.to_numeric(frame["net_profit"], errors="coerce")
    share = (
        pd.to_numeric(frame["total_share"], errors="coerce")
        if "total_share" in frame.columns
        else pd.Series(np.nan, index=frame.index, dtype="float64")
    )
    if fallback_share is not None and fallback_share > 0:
        share = share.where(share.notna() & (share > 0), float(fallback_share))
    eps = net_profit / share
    eps = _finite(eps.where(net_profit.notna() & share.notna() & (share > 0)))

    result = pd.DataFrame(
        {
            "report_date": _date_series(frame["report_date"]),
            "eps": eps,
            "roe": pd.to_numeric(frame["roe"], errors="coerce")
            if "roe" in frame.columns
            else np.nan,
            "revenue": pd.to_numeric(frame["revenue"], errors="coerce")
            if "revenue" in frame.columns
            else np.nan,
        }
    )
    result = result.dropna(subset=["report_date"])
    if result.empty:
        return pd.DataFrame({column: pd.Series(dtype="object") for column in columns})
    return result.sort_values("report_date", kind="mergesort").reset_index(drop=True)


async def _fetch_total_share(code: str) -> float | None:
    """兜底股本：新浪日线 ``stock_zh_a_daily`` 的 ``outstanding_share``（流通股本，股）。

    只在指标表缺 EPS、财务摘要又缺「总股本」时才会被调用（多一次请求，日记级别日志）。
    """
    raw = await _ak_call("stock_zh_a_daily", symbol=_sina_symbol(code))
    if raw is None:
        return None
    column = _resolve_column(
        raw.columns, ("outstanding_share", "流通股本"), (r"^outstanding_share$", r"流通股本")
    )
    if column is None:
        return None
    values = pd.to_numeric(raw[column], errors="coerce").dropna()
    if len(values) == 0:
        return None
    return float(values.iloc[-1])


def _merge_abstract_eps(fins: pd.DataFrame, abstract: pd.DataFrame) -> pd.DataFrame:
    """把兜底 EPS/ROE 合并进主表：**只填补缺失，不覆盖**新浪指标表已有值。"""
    if abstract is None or len(abstract) == 0:
        return fins
    if fins is None or len(fins) == 0:
        frame = abstract.copy()
        for column in ("bps", "payout_ratio"):
            frame[column] = np.nan
        return frame[list(FIN_COLUMNS)].sort_values("report_date").reset_index(drop=True)

    frame = fins.merge(
        abstract.rename(columns={"eps": "eps_abstract", "roe": "roe_abstract"}),
        on="report_date",
        how="outer",
    )
    frame["eps"] = frame["eps"].where(frame["eps"].notna(), frame["eps_abstract"])
    frame["roe"] = frame["roe"].where(frame["roe"].notna(), frame["roe_abstract"])
    frame = frame.drop(columns=["eps_abstract", "roe_abstract"])
    for column in ("bps", "payout_ratio"):
        if column not in frame.columns:
            frame[column] = np.nan
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame[list(FIN_COLUMNS)].sort_values("report_date").reset_index(drop=True)


async def fetch_financials(code: str, start_year: int = DEFAULT_START_YEAR) -> pd.DataFrame:
    """联网抓单只股票的财报指标（新浪），返回归一化 + TTM 后的表。

    数据源与降级：
        1. 主源 ``ak.stock_financial_analysis_indicator(symbol, start_year)`` ——
           每个报告期一行，含 EPS/BPS/ROE/股息发放率；
        2. 兜底 ``ak.stock_financial_abstract(symbol)`` —— 当主源缺 EPS 时，
           用「归母净利润 / 股本」反推 EPS（见 :func:`abstract_eps_frame` 的局限性说明）。
        3. 股本兜底 ``ak.stock_zh_a_daily`` 的 ``outstanding_share``（流通股本近似）。

    明确**不用**的接口（2026-10 实测不可用）：``stock_a_indicator_lg``（akshare 1.19.1
    已移除）、``stock_zh_valuation_baidu``（网络不可达）、``stock_value_em``（东财被墙）、
    ``stock_individual_spot_xq``（雪球要 token）。

    Args:
        code: 股票代码（600519 / sh600519 都可以）。
        start_year: 起始年份（默认 2019，覆盖近 5 年回补 + TTM 需要的上年数据）。

    Returns:
        ``[report_date, eps, bps, roe, payout_ratio, eps_ttm, eps_ttm_basis, revenue]``，
        按报告期升序。**任何失败都返回空表并记日志**，不抛异常（单只失败不影响批量任务）。
    """
    normalized = _normalize_code(code)
    raw = await _ak_call(
        "stock_financial_analysis_indicator",
        symbol=normalized,
        start_year=str(int(start_year)),
    )
    fins = normalize_financials(raw)
    revenue_map = _extract_revenue_map(raw)

    if fins.empty or fins["eps"].isna().all():
        logger.info("财报指标表缺 EPS，尝试财务摘要兜底 code=%s", normalized)
        metrics_raw = await _ak_call("stock_financial_abstract", symbol=normalized)
        metrics = normalize_abstract_metrics(metrics_raw)
        if not metrics.empty:
            has_period_share = False
            if "total_share" in metrics.columns:
                shares = pd.to_numeric(metrics["total_share"], errors="coerce")
                has_period_share = bool(shares.notna().any())
            # 摘要里已有当期股本就不用再打日线接口；否则用流通股本近似（多一次请求）
            fallback_share: float | None = (
                None if has_period_share else await _fetch_total_share(normalized)
            )
            abstract_frame = abstract_eps_frame(metrics, fallback_share)
            fins = _merge_abstract_eps(fins, abstract_frame)
            for period, value in zip(metrics["report_date"], metrics["revenue"]):
                if pd.notna(period) and pd.notna(value):
                    revenue_map.setdefault(period, float(value))

    if fins.empty:
        return _empty_financials_ttm()

    ttm = eps_ttm_series(fins)
    result = fins.merge(
        ttm[["report_date", "eps_ttm", "eps_ttm_basis"]], on="report_date", how="left"
    )
    result["revenue"] = result["report_date"].map(revenue_map)
    return result[list(FIN_COLUMNS) + ["eps_ttm", "eps_ttm_basis", "revenue"]]


# ============================================================
# DB 基础设施（懒 import：纯函数测试不需要数据库栈）
# ============================================================
def _today_cn() -> date:
    """A 股所在的"今天"（Asia/Shanghai）。"""
    try:
        return datetime.now(ZoneInfo(config.TIMEZONE)).date()
    except Exception:  # pragma: no cover —— Windows 缺 tzdata 时兜底固定 +8
        return (datetime.now(timezone.utc) + timedelta(hours=8)).date()


def _as_date(value: Any) -> date:
    """date/datetime/字符串 → ``datetime.date``（无效值抛 ValueError）。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = pd.to_datetime(value, errors="coerce")
    if parsed is pd.NaT or pd.isna(parsed):
        raise ValueError(f"无法解析日期: {value!r}")
    return parsed.date()


@asynccontextmanager
async def _db_scope(db: "AsyncSession | None") -> AsyncIterator["AsyncSession"]:
    """会话作用域：外部传入 → 复用（只 flush）；否则自建 ``session_scope()``（自动 commit）。

    这是"事务所有权"约定的唯一入口：``db=None`` 时本模块自己提交（CLI / 后台任务），
    传入时把提交时机交给 ``app/pipeline.py``（一个事务里可回滚）。
    """
    if db is not None:
        try:
            yield db
        finally:
            await db.flush()
        return
    from app.db.session import session_scope

    async with session_scope() as session:
        yield session


def _as_db_value(value: Any, digits: int | None = None) -> float | None:
    """DataFrame 里的值 → 可写库的 float（NaN/Inf/None → None，按列精度四舍五入）。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return round(number, digits) if digits is not None else number


def _normalize_codes(codes: Iterable[Any] | None) -> list[str]:
    """代码清单归一化（去重、保序、统一 6 位）。"""
    cleaned: dict[str, None] = {}
    for code in codes or []:
        digits = "".join(ch for ch in str(code) if ch.isdigit())
        if digits:
            cleaned[digits.zfill(6)[-6:]] = None
    return list(cleaned)


def _announced_date(report_date: date, lag_days: int = REPORT_LAG_DAYS) -> date:
    """报告期 → 市场可知日期（point-in-time 的时间锚）。"""
    return report_date + timedelta(days=int(lag_days))


_QUARTER_END_DAYS = {12: 31, 9: 30, 6: 30, 3: 31}


def latest_knowable_report_period(
    today: date, lag_days: int = REPORT_LAG_DAYS
) -> date | None:
    """按"报告期 + 滞后"的保守口径，算出今天**最多**能知道到哪个报告期。

    例：``today=2026-10-22``、``lag_days=90`` →
        2026-09-30 + 90d = 2026-12-29（还不可知）→ 回退到 2026-06-30（2026-09-28 可知）。

    用途：``sync_financials(only_missing=True)`` 用它判断"这只股票的财报已经是最新可得的
    了吗"，是的话跳过网络请求 —— 这就是"每日增量几乎零请求"的关键。
    """
    for year in range(today.year, today.year - 3, -1):
        for month in (12, 9, 6, 3):
            period = date(year, month, _QUARTER_END_DAYS[month])
            if _announced_date(period, lag_days) <= today:
                return period
    return None


def _json_safe(value: Any) -> Any:
    """checkpoint 里可能有 date / numpy 标量，转成 JSONB 能接受的值。"""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return _as_db_value(value)
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


# ============================================================
# 阶段 1：抓财报 → dim_stock_financial
# ============================================================
def _financial_rows_from_frame(
    code: str, frame: pd.DataFrame, src: str = "sina"
) -> list[dict[str, Any]]:
    """``fetch_financials`` 的返回表 → ``dim_stock_financial`` 行（精度按模型列定义）。

    跳过"只有报告期、没有任何财务数"的行：那种行对估值没有任何贡献，写进去只会
    让 only_missing 判断误以为"已有报告"。
    """
    if frame is None or len(frame) == 0:
        return []
    rows: list[dict[str, Any]] = []
    for record in frame.to_dict("records"):
        report_date = record.get("report_date")
        if not isinstance(report_date, date):
            continue
        eps = _as_db_value(record.get("eps"), 6)
        bps = _as_db_value(record.get("bps"), 6)
        eps_ttm = _as_db_value(record.get("eps_ttm"), 6)
        if eps is None and bps is None and eps_ttm is None:
            continue
        rows.append(
            {
                "code": code,
                "report_date": report_date,
                "announced_date": _announced_date(report_date),
                "eps": eps,
                "bps": bps,
                "roe": _as_db_value(record.get("roe"), 4),
                "payout_ratio": _as_db_value(record.get("payout_ratio"), 4),
                "eps_ttm": eps_ttm,
                "revenue": _as_db_value(record.get("revenue"), 2),
                "src": src,
            }
        )
    return rows


async def _resolve_market_codes(db: "AsyncSession | None") -> list[str]:
    """``codes=None`` 时的股票池：``dim_stock`` 里 ``status=1``（在市）的标的。"""
    from sqlalchemy import select

    from app.db.models.dim import DimStock

    stmt = select(DimStock.code).where(DimStock.status == 1).order_by(DimStock.code)
    async with _db_scope(db) as session:
        rows = (await session.execute(stmt)).scalars().all()
    return [str(code) for code in rows]


async def _load_latest_financial_dates(
    db: "AsyncSession | None", codes: Sequence[str] | None = None
) -> dict[str, date]:
    """每个股票**已入库的最新报告期**（only_missing 判断用）。"""
    from sqlalchemy import func, select

    from app.db.models.dim import DimStockFinancial

    stmt = select(
        DimStockFinancial.code, func.max(DimStockFinancial.report_date)
    ).group_by(DimStockFinancial.code)
    target = _normalize_codes(codes) if codes is not None else []
    if target:
        stmt = stmt.where(DimStockFinancial.code.in_(target))
    async with _db_scope(db) as session:
        rows = (await session.execute(stmt)).all()
    return {str(code): period for code, period in rows if period is not None}


async def _upsert_financial_rows(
    db: "AsyncSession | None", rows: Sequence[dict[str, Any]]
) -> int:
    """幂等写入财报缓存（``ON CONFLICT (code, report_date) DO UPDATE``）。"""
    if not rows:
        return 0
    from app.db.models.dim import DimStockFinancial

    async with _db_scope(db) as session:
        return await upsert_rows(
            session,
            DimStockFinancial,
            list(rows),
            ["code", "report_date"],
            update_columns=[
                "announced_date", "eps", "bps", "roe", "payout_ratio",
                "eps_ttm", "revenue", "src",
            ],
        )


# ---- 分红缓存（dim_stock_dividend）----
def _dividend_rows_from_frame(
    code: str, frame: pd.DataFrame, src: str = "cninfo"
) -> list[dict[str, Any]]:
    """``normalize_dividends`` 的返回表 → ``dim_stock_dividend`` 行。"""
    if frame is None or len(frame) == 0:
        return []
    rows: list[dict[str, Any]] = []
    for record in frame.to_dict("records"):
        ex_date = record.get("ex_date")
        dps = _as_db_value(record.get("dps"), 6)
        if not isinstance(ex_date, date) or dps is None or dps <= 0:
            continue
        announce_date = record.get("announce_date")
        report_period = record.get("report_period")
        rows.append(
            {
                "code": code,
                "ex_date": ex_date,
                "dps": dps,
                "announce_date": announce_date if isinstance(announce_date, date) else None,
                "report_period": (
                    str(report_period)[:16] if report_period is not None and pd.notna(
                        report_period
                    ) else None
                ),
                "src": src,
            }
        )
    return rows


async def _upsert_dividend_rows(
    db: "AsyncSession | None", rows: Sequence[dict[str, Any]]
) -> int:
    """幂等写入分红缓存（``ON CONFLICT (code, ex_date) DO UPDATE``）。"""
    if not rows:
        return 0
    from app.db.models.dim import DimStockDividend

    async with _db_scope(db) as session:
        return await upsert_rows(
            session,
            DimStockDividend,
            list(rows),
            ["code", "ex_date"],
            update_columns=["dps", "announce_date", "report_period", "src"],
        )


async def _load_dividend_updated(
    db: "AsyncSession | None", codes: Sequence[str]
) -> dict[str, datetime]:
    """每个股票分红记录的**最后刷新时间**（``only_missing`` 判断用）。

    注意：没有任何分红记录的股票不会出现在这个 map 里（"查过但没有分红"要靠
    ``ingest_job.checkpoint.checked_codes`` 兜住，见 :func:`sync_dividends`）。
    """
    from sqlalchemy import func, select

    from app.db.models.dim import DimStockDividend

    target = _normalize_codes(codes)
    if not target:
        return {}
    stmt = (
        select(DimStockDividend.code, func.max(DimStockDividend.updated_at))
        .where(DimStockDividend.code.in_(target))
        .group_by(DimStockDividend.code)
    )
    async with _db_scope(db) as session:
        rows = (await session.execute(stmt)).all()
    result: dict[str, datetime] = {}
    for code, updated_at in rows:
        if updated_at is not None:
            result[str(code)] = updated_at
    return result


async def _load_dividends_for_codes(
    db: "AsyncSession | None", codes: Sequence[str]
) -> pd.DataFrame:
    """读一批股票的分红明细（阶段 2 的输入，**零网络**）。"""
    columns = ["code", "ex_date", "dps"]
    target = _normalize_codes(codes)
    if not target:
        return pd.DataFrame({column: pd.Series(dtype="object") for column in columns})
    from sqlalchemy import select

    from app.db.models.dim import DimStockDividend

    stmt = (
        select(DimStockDividend.code, DimStockDividend.ex_date, DimStockDividend.dps)
        .where(DimStockDividend.code.in_(target))
        .order_by(DimStockDividend.code, DimStockDividend.ex_date)
    )
    async with _db_scope(db) as session:
        rows = (await session.execute(stmt)).all()
    if not rows:
        return pd.DataFrame({column: pd.Series(dtype="object") for column in columns})
    return pd.DataFrame(rows, columns=columns)


# ---- ingest_job（断点续传）----
async def _job_begin(
    db: "AsyncSession | None",
    task: str,
    dataset: str,
    params: dict[str, Any],
    total: int | None,
) -> str | None:
    """写一条 ``ingest_job``（status=running）。失败只记日志，不影响采集。"""
    from app.db.models.meta import IngestJob

    job_id = str(uuid.uuid4())
    row = {
        "job_id": job_id,
        "task": task,
        "dataset": dataset,
        "trade_date": None,
        "params": _json_safe(params),
        "status": "running",
        "total": total,
        "done": 0,
        "failed": 0,
        "rows_written": 0,
        "started_at": datetime.now(timezone.utc),
        "finished_at": None,
        "elapsed_ms": None,
        "error": None,
        "checkpoint": None,
    }
    try:
        async with _db_scope(db) as session:
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
    except Exception:  # noqa: BLE001 —— 监控写失败不该拖垮采集
        logger.exception("写入 ingest_job 失败 task=%s", task)
        return None
    logger.info("估值任务开始 job=%s task=%s total=%s", job_id, task, total)
    return job_id


async def _job_update(
    db: "AsyncSession | None",
    job_id: str | None,
    *,
    done: int | None = None,
    failed: int | None = None,
    rows_written: int | None = None,
    checkpoint: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """更新进度与 checkpoint（断点续传的载体）。"""
    if job_id is None:
        return
    from sqlalchemy import update

    from app.db.models.meta import IngestJob

    fields: dict[str, Any] = {}
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
        async with _db_scope(db) as session:
            await session.execute(
                update(IngestJob).where(IngestJob.job_id == job_id).values(**fields)
            )
    except Exception:  # noqa: BLE001
        logger.exception("更新 ingest_job 失败 job=%s", job_id)


async def _job_finish(
    db: "AsyncSession | None",
    job_id: str | None,
    status: str,
    *,
    started_at: float,
    done: int | None = None,
    failed: int | None = None,
    rows_written: int | None = None,
    checkpoint: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """写任务终态与耗时。"""
    if job_id is None:
        return
    from sqlalchemy import update

    from app.db.models.meta import IngestJob

    fields: dict[str, Any] = {
        "status": status,
        "finished_at": datetime.now(timezone.utc),
        "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
    }
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
    try:
        async with _db_scope(db) as session:
            await session.execute(
                update(IngestJob).where(IngestJob.job_id == job_id).values(**fields)
            )
    except Exception:  # noqa: BLE001
        logger.exception("写入 ingest_job 终态失败 job=%s", job_id)
    logger.info(
        "估值任务结束 job=%s status=%s rows=%s failed=%s elapsed_ms=%s",
        job_id, status, rows_written, failed, fields["elapsed_ms"],
    )


async def _load_checkpoint(
    db: "AsyncSession | None", task: str, dataset: str, params: dict[str, Any]
) -> dict[str, Any]:
    """读最近一次同 task/同参数的 checkpoint（参数不一致就不用，避免串数据）。"""
    from sqlalchemy import select

    from app.db.models.meta import IngestJob

    try:
        async with _db_scope(db) as session:
            stmt = (
                select(IngestJob)
                .where(IngestJob.task == task, IngestJob.dataset == dataset)
                .order_by(IngestJob.started_at.desc())
                .limit(1)
            )
            job = (await session.execute(stmt)).scalars().first()
            if job is None or not isinstance(job.checkpoint, dict):
                return {}
            if job.checkpoint.get("params") == _json_safe(params):
                return job.checkpoint
    except Exception:  # noqa: BLE001
        logger.exception("读取断点失败 task=%s", task)
    return {}


async def _record_quality(
    db: "AsyncSession | None",
    report: QualityReport,
    trade_date: date | None,
    status: str,
) -> None:
    """写 ``meta_data_quality``（前端新鲜度徽标的唯一来源）。失败不影响主流程。"""
    try:
        async with _db_scope(db) as session:
            await record_quality(session, report.dataset, report, trade_date, status)
    except Exception:  # noqa: BLE001
        logger.exception("写入估值质量记录失败 dataset=%s", report.dataset)


# ============================================================
# 阶段 2 的读取 / 更新
# ============================================================
async def _codes_with_prices(
    db: "AsyncSession | None",
    start: date,
    end: date,
    codes: Sequence[str] | None = None,
) -> list[str]:
    """区间内**有日线**的股票代码（区间内没行情就没必要算估值）。"""
    from sqlalchemy import select

    from app.db.models.fact import FactStockDaily

    stmt = (
        select(FactStockDaily.code)
        .where(
            FactStockDaily.trade_date >= start,
            FactStockDaily.trade_date <= end,
        )
        .distinct()
        .order_by(FactStockDaily.code)
    )
    target = _normalize_codes(codes) if codes is not None else []
    if target:
        stmt = stmt.where(FactStockDaily.code.in_(target))
    async with _db_scope(db) as session:
        rows = (await session.execute(stmt)).scalars().all()
    return [str(code) for code in rows]


async def _load_financials_for_codes(
    db: "AsyncSession | None", codes: Sequence[str]
) -> pd.DataFrame:
    """从财报缓存表读一批股票的财报行（阶段 2 的输入，**零网络**）。

    列与 ``DimStockFinancial`` 一致（``build_valuation_frame`` 直接可用的形态）。
    """
    columns = [
        "code", "report_date", "announced_date", "eps_ttm", "bps", "payout_ratio",
        "eps", "roe",
    ]
    empty = pd.DataFrame({column: pd.Series(dtype="object") for column in columns})
    target = _normalize_codes(codes)
    if not target:
        return empty

    from sqlalchemy import select

    from app.db.models.dim import DimStockFinancial

    stmt = (
        select(
            DimStockFinancial.code,
            DimStockFinancial.report_date,
            DimStockFinancial.announced_date,
            DimStockFinancial.eps_ttm,
            DimStockFinancial.bps,
            DimStockFinancial.payout_ratio,
            DimStockFinancial.eps,
            DimStockFinancial.roe,
        )
        .where(DimStockFinancial.code.in_(target))
        .order_by(DimStockFinancial.code, DimStockFinancial.report_date)
    )
    async with _db_scope(db) as session:
        rows = (await session.execute(stmt)).all()
    if not rows:
        return empty
    return pd.DataFrame(rows, columns=columns)


async def _load_price_rows(
    db: "AsyncSession | None", codes: Sequence[str], start: date, end: date
) -> pd.DataFrame:
    """读一批股票在 ``[start, end]`` 的收盘价（只要 code/trade_date/close 三列）。"""
    columns = ["code", "trade_date", "close"]
    target = _normalize_codes(codes)
    if not target:
        return pd.DataFrame({column: pd.Series(dtype="object") for column in columns})

    from sqlalchemy import select

    from app.db.models.fact import FactStockDaily

    stmt = (
        select(FactStockDaily.code, FactStockDaily.trade_date, FactStockDaily.close)
        .where(
            FactStockDaily.trade_date >= start,
            FactStockDaily.trade_date <= end,
            FactStockDaily.code.in_(target),
        )
        .order_by(FactStockDaily.code, FactStockDaily.trade_date)
    )
    async with _db_scope(db) as session:
        rows = (await session.execute(stmt)).all()
    if not rows:
        return pd.DataFrame({column: pd.Series(dtype="object") for column in columns})
    return pd.DataFrame(rows, columns=columns)


def _valuation_update_rows(code: str, frame: pd.DataFrame) -> list[dict[str, Any]]:
    """估值展开表 → UPDATE 用的行（只保留三列全空之外的行）。"""
    if frame is None or len(frame) == 0:
        return []
    rows: list[dict[str, Any]] = []
    for record in frame[["trade_date", "pe_ttm", "pb", "dv_ttm"]].to_dict("records"):
        trade_date = record["trade_date"]
        if not isinstance(trade_date, date):
            continue
        pe = _as_db_value(record.get("pe_ttm"), 4)
        pb = _as_db_value(record.get("pb"), 4)
        dv = _as_db_value(record.get("dv_ttm"), 4)
        if pe is None and pb is None and dv is None:
            continue
        rows.append(
            {"code": code, "trade_date": trade_date, "pe_ttm": pe, "pb": pb, "dv_ttm": dv}
        )
    return rows


def _valuation_update_statement(rows: Sequence[dict[str, Any]]) -> Any:
    """构造 ``UPDATE fact_stock_daily SET pe_ttm/pb/dv_ttm FROM (VALUES ...)`` 语句。

    **只更新这三列**：不 INSERT（绝不凭空造出行情行）、不碰 close/volume/amount/adj_factor。
    用 ``UPDATE ... FROM (VALUES ...)`` 一条语句更新上千行，比逐行 UPDATE 快得多。
    """
    from sqlalchemy import Date, Numeric, String, column, update, values

    from app.db.models.fact import FactStockDaily

    table = FactStockDaily.__table__
    value_rows = [
        (
            row["code"],
            row["trade_date"],
            row.get("pe_ttm"),
            row.get("pb"),
            row.get("dv_ttm"),
        )
        for row in rows
    ]
    payload = values(
        column("code", String(6)),
        column("trade_date", Date()),
        column("pe_ttm", Numeric(16, 4)),
        column("pb", Numeric(16, 4)),
        column("dv_ttm", Numeric(10, 4)),
        name="v",
    ).data(value_rows)
    return (
        update(table)
        .where(table.c.code == payload.c.code, table.c.trade_date == payload.c.trade_date)
        .values(
            pe_ttm=payload.c.pe_ttm,
            pb=payload.c.pb,
            dv_ttm=payload.c.dv_ttm,
            # 坑：FactStockDaily.updated_at 有 onupdate，SQLAlchemy 会给任何 UPDATE 自动
            # 补上 SET updated_at = now()。本函数的契约是"只更新估值三列、不动其他列"，
            # 所以这里显式把它设成自引用（= 保持原值），把 onupdate 的隐式写入压掉。
            updated_at=table.c.updated_at,
        )
    )


async def _bulk_update_valuation(
    db: "AsyncSession | None", rows: Sequence[dict[str, Any]]
) -> int:
    """批量更新日线的估值三列，返回实际更新行数（``rowcount``）。"""
    if not rows:
        return 0
    total = 0
    async with _db_scope(db) as session:
        for chunk in chunked(list(rows), UPDATE_CHUNK_ROWS):
            statement = _valuation_update_statement(chunk)
            result = await session.execute(statement)
            affected = getattr(result, "rowcount", None)
            if affected is None or affected < 0:
                affected = len(chunk)
            total += int(affected)
    return total


# ============================================================
# 阶段 1 入口
# ============================================================
async def sync_financials(
    codes: list[str] | None = None,
    start_year: int = DEFAULT_START_YEAR,
    db: "AsyncSession | None" = None,
    only_missing: bool = True,
    resume: bool = True,
) -> FetchResult:
    """阶段 1：抓财报指标 → 落库 ``dim_stock_financial``（慢，带网络，断点续传）。

    为什么单独一个阶段：财报是季度数据，每天为全市场重抓一次要 45 分钟（合规限频
    ≤2 请求/秒）。落库缓存后，每日任务只需 ``compute_valuation``（零网络、秒级）。

    Args:
        codes: 指定股票代码；``None`` = ``dim_stock`` 里 ``status=1``（在市）的全部标的。
        start_year: 起始年份（默认 2019；``backfill_valuation`` 会按回补起点自动往前推）。
        db: 外部 ``AsyncSession``（传入只 flush 不 commit，事务归 pipeline）；
            ``None`` 时用 ``session_scope()`` 自建事务。
        only_missing: ``True``（默认）时跳过"已有最新可得报告期"的股票 —— 每日增量几乎
            零请求。注意：**显式传 codes 时同样生效**，要强制刷新请传 ``only_missing=False``。
        resume: 是否读 ``ingest_job.checkpoint`` 跳过已完成代码（长任务中断后重跑用）。

    Returns:
        ``FetchResult``：``rows`` = upsert 的财报表行数，``total`` = 处理的股票数，
        ``failed`` = 抓取失败的股票数。``detail`` 里带 ``pending``/``skipped_fresh``/
        ``failed_codes`` 等排查信息。

    口径说明：``announced_date = report_date + REPORT_LAG_DAYS``（point-in-time 的时间锚），
    由 :func:`_financial_rows_from_frame` 统一写入。
    """
    started = time.perf_counter()
    task = "sync_financials"
    today = _today_cn()

    code_list = _normalize_codes(codes) if codes is not None else await _resolve_market_codes(db)
    if not code_list:
        logger.warning("财报同步：股票池为空（codes=%s），跳过", codes)
        return FetchResult(
            rows=0, total=0, elapsed_ms=int((time.perf_counter() - started) * 1000),
            detail={"skipped": "no_codes", "task": task},
        )

    scope = (
        "all" if codes is None
        else "codes:" + ",".join(code_list[:5]) + f"..({len(code_list)})"
    )
    params = {"start_year": int(start_year), "scope": scope}

    done: set[str] = set()
    failed: dict[str, str] = {}
    written = 0
    skipped_fresh = 0

    # only_missing：已有"最新可得报告期"的股票不用再联网
    pending = list(code_list)
    if only_missing:
        required = latest_knowable_report_period(today)
        try:
            latest = await _load_latest_financial_dates(db, code_list)
        except Exception:  # noqa: BLE001 —— 读不到的代码按"需要抓取"处理更安全
            logger.exception("读取已入库报告期失败，本次全量抓取")
            latest = {}
        pending = []
        for code in code_list:
            period = latest.get(code)
            if required is not None and period is not None and period >= required:
                done.add(code)
                skipped_fresh += 1
            else:
                pending.append(code)
    logger.info(
        "财报同步：共 %d 只，待抓 %d 只，跳过（已有最新报告）%d 只",
        len(code_list), len(pending), skipped_fresh,
    )

    if resume:
        checkpoint = await _load_checkpoint(db, task, DATASET, params)
        for code in checkpoint.get("completed_codes", []) or []:
            done.add(str(code))
        for code, reason in (checkpoint.get("failed_codes") or {}).items():
            failed[str(code)] = str(reason)
        if checkpoint:
            pending = [code for code in pending if code not in done]
            logger.info("财报同步断点续传：跳过已完成 %d 只", len(done))

    job_id = await _job_begin(db, task, DATASET, params, total=len(code_list))
    batch_rows: list[dict[str, Any]] = []
    batch_codes: list[str] = []

    async def _flush_batch() -> None:
        nonlocal written
        if not batch_rows:
            batch_codes.clear()
            return
        written += await _upsert_financial_rows(db, batch_rows)
        done.update(batch_codes)
        batch_rows.clear()
        batch_codes.clear()

    try:
        for index, code in enumerate(pending, start=1):
            try:
                frame = await fetch_financials(code, start_year=start_year)
                if frame.empty:
                    failed[code] = "financials_empty"
                else:
                    rows = _financial_rows_from_frame(code, frame)
                    if not rows:
                        failed[code] = "no_valid_report_period"
                    else:
                        batch_rows.extend(rows)
                        batch_codes.append(code)
            except Exception as exc:  # noqa: BLE001 —— 单只失败不影响整体
                failed[code] = f"{type(exc).__name__}: {exc}"
                logger.warning("财报抓取失败 code=%s：%s", code, exc)

            if len(batch_codes) >= SYNC_BATCH_CODES:
                await _flush_batch()
            if index % SYNC_CHECKPOINT_EVERY == 0 or index == len(pending):
                await _flush_batch()
                logger.info(
                    "财报同步进度 %d/%d（完成 %d，失败 %d，写入 %d 行）",
                    index, len(pending), len(done), len(failed), written,
                )
                await _job_update(
                    db,
                    job_id,
                    done=len(done),
                    failed=len(failed),
                    rows_written=written,
                    checkpoint={
                        "params": params,
                        "completed_codes": sorted(done),
                        "failed_codes": dict(sorted(failed.items())[:500]),
                    },
                )

        await _flush_batch()
        elapsed = int((time.perf_counter() - started) * 1000)

        coverage = len(done) / len(code_list) if code_list else 0.0
        report = QualityReport(
            dataset=DATASET,
            rows=written,
            expected=len(code_list),
            coverage=round(coverage, 4),
        )
        if failed:
            report.add(
                "financials_fetch_failed",
                count=len(failed),
                severity="warning",
                message=f"{len(failed)} 只标的财报抓取失败（未写库，重跑可补）",
                samples=sorted(failed)[:5],
            )
        status = "ok" if written > 0 or not pending else "error"
        if status == "error":
            await _record_quality(
                db,
                make_error_report(DATASET, "全部标的财报抓取失败"),
                today,
                "error",
            )
        else:
            await _record_quality(db, report, today, status)

        final_checkpoint = {
            "params": params,
            "completed_codes": sorted(done),
            "failed_codes": dict(sorted(failed.items())[:500]),
        }
        await _job_finish(
            db,
            job_id,
            "success" if not failed else "partial",
            started_at=started,
            done=len(done),
            failed=len(failed),
            rows_written=written,
            checkpoint=final_checkpoint,
        )
        return FetchResult(
            rows=written,
            total=len(code_list),
            failed=len(failed),
            elapsed_ms=elapsed,
            detail={
                "task": task,
                "completed": len(done),
                "skipped_fresh": skipped_fresh,
                "pending": len(pending),
                "failed_codes": sorted(failed)[:100],
                "start_year": int(start_year),
            },
        )
    except Exception as exc:
        await _job_finish(
            db,
            job_id,
            "failed",
            started_at=started,
            done=len(done),
            failed=len(failed),
            rows_written=written,
            checkpoint={
                "params": params,
                "completed_codes": sorted(done),
                "failed_codes": dict(sorted(failed.items())[:500]),
            },
            error=f"{type(exc).__name__}: {exc}",
        )
        raise


# ============================================================
# 阶段 1b 入口：巨潮现金分红 → dim_stock_dividend
# ============================================================
async def sync_dividends(
    codes: list[str] | None = None,
    db: "AsyncSession | None" = None,
    only_missing: bool = True,
    resume: bool = True,
) -> FetchResult:
    """阶段 1b：抓现金分红明细 → 落库 ``dim_stock_dividend``（慢，带网络，月度跑）。

    为什么要这一层：新浪财报的 ``股息发放率(%)`` 大量为空，而且它的语义是
    "分红 / 净利润"（分红占利润的比例），**不是**股息率。股息率 =
    近 12 个月每股现金分红 / 股价，必须用分红明细（巨潮 ``stock_dividend_cninfo``，
    实测 600519 返回 31 行）按**除权日**落库，才能做 point-in-time。

    Args:
        codes: 指定股票代码；``None`` = ``dim_stock`` 里 ``status=1`` 的全部标的。
        db: 外部 ``AsyncSession``（只 flush）；``None`` 时 ``session_scope()`` 自建事务。
        only_missing: ``True``（默认）时跳过近期已刷新过的股票：
            - 有分红记录的股票看 ``dim_stock_dividend.updated_at``
              （``DIVIDEND_REFRESH_DAYS`` = 30 天）；
            - **没有分红记录**的股票靠上一次任务 checkpoint 里的 ``checked_codes``
              + ``checked_at`` 兜住（否则"从没分红"的股票会被每天重抓）。
        resume: 是否复用上次 checkpoint 跳过已完成代码。

    Returns:
        ``FetchResult``：``rows`` = upsert 的分红行数，``total`` = 处理的股票数，
        ``failed`` = 抓取失败的股票数。

    说明：``ex_date`` 用「除权日」；没有除权日时退「派息日」/「公告日」并记 warning
    （见 :func:`normalize_dividends`）。完全没分红的股票 = 查询成功但没有行，
    仍算"已检查"，写进 checkpoint 的 ``checked_codes``，下次直接跳过。
    """
    started = time.perf_counter()
    task = "sync_dividends"
    today = _today_cn()

    code_list = _normalize_codes(codes) if codes is not None else await _resolve_market_codes(db)
    if not code_list:
        logger.warning("分红同步：股票池为空（codes=%s），跳过", codes)
        return FetchResult(
            rows=0, total=0, elapsed_ms=int((time.perf_counter() - started) * 1000),
            detail={"skipped": "no_codes", "task": task},
        )

    scope = (
        "all" if codes is None
        else "codes:" + ",".join(code_list[:5]) + f"..({len(code_list)})"
    )
    params = {"scope": scope}

    done: set[str] = set()
    failed: dict[str, str] = {}
    written = 0
    skipped_fresh = 0
    pending = list(code_list)

    if only_missing:
        # 1) 有分红记录、且最近 DIVIDEND_REFRESH_DAYS 内刷新过的 → 跳过
        recent: set[str] = set()
        try:
            updated = await _load_dividend_updated(db, code_list)
        except Exception:  # noqa: BLE001 —— 读不到就全量重抓（安全侧）
            logger.exception("读取分红刷新时间失败，本次全量抓取")
            updated = {}
        now = datetime.now(timezone.utc)
        for code, updated_at in updated.items():
            last = updated_at
            if last.tzinfo is None:  # 防御：DB 返回 naive datetime
                last = last.replace(tzinfo=timezone.utc)
            if (now - last) <= timedelta(days=DIVIDEND_REFRESH_DAYS):
                recent.add(code)

        # 2) "查过但没有分红"的股票：靠上次 checkpoint 的 checked_at/checked_codes 跳过
        try:
            checkpoint = await _load_checkpoint(db, task, DATASET, params)
        except Exception:  # noqa: BLE001
            logger.exception("读取分红断点失败")
            checkpoint = {}
        if checkpoint:
            checked_at = checkpoint.get("checked_at")
            if checked_at:
                try:
                    checked_date = _as_date(checked_at)
                except ValueError:
                    checked_date = None
                if (
                    checked_date is not None
                    and (today - checked_date).days < DIVIDEND_REFRESH_DAYS
                ):
                    recent.update(
                        str(code) for code in checkpoint.get("checked_codes") or []
                    )
            for code in checkpoint.get("completed_codes") or []:
                done.add(str(code))
        skipped_fresh = len([code for code in code_list if code in recent])
        done.update(recent)
        pending = [code for code in code_list if code not in recent]

    if resume:
        checkpoint = await _load_checkpoint(db, task, DATASET, params)
        for code in checkpoint.get("completed_codes") or []:
            done.add(str(code))
        for code, reason in (checkpoint.get("failed_codes") or {}).items():
            failed[str(code)] = str(reason)
        if checkpoint:
            pending = [code for code in pending if code not in done]
            logger.info("分红同步断点续传：跳过已完成 %d 只", len(done))

    logger.info(
        "分红同步：共 %d 只，待抓 %d 只，跳过（近期已刷新）%d 只",
        len(code_list), len(pending), skipped_fresh,
    )

    job_id = await _job_begin(db, task, DATASET, params, total=len(code_list))
    batch_rows: list[dict[str, Any]] = []
    batch_codes: list[str] = []

    async def _flush_batch() -> None:
        nonlocal written
        if not batch_rows:
            batch_codes.clear()
            return
        written += await _upsert_dividend_rows(db, batch_rows)
        done.update(batch_codes)
        batch_rows.clear()
        batch_codes.clear()

    try:
        for index, code in enumerate(pending, start=1):
            try:
                raw = await _ak_call("stock_dividend_cninfo", symbol=code)
                if raw is None:
                    # 查询成功但没有数据 → "无分红"，查过即完成（不写库）
                    done.add(code)
                else:
                    rows = _dividend_rows_from_frame(code, normalize_dividends(raw))
                    if rows:
                        batch_rows.extend(rows)
                        batch_codes.append(code)  # 落库成功后才标记完成（见 _flush_batch）
                    else:
                        done.add(code)
            except Exception as exc:  # noqa: BLE001 —— 单只失败不影响整体
                failed[code] = f"{type(exc).__name__}: {exc}"
                logger.warning("分红抓取失败 code=%s：%s", code, exc)

            if len(batch_codes) >= SYNC_BATCH_CODES:
                await _flush_batch()
            if index % SYNC_CHECKPOINT_EVERY == 0 or index == len(pending):
                await _flush_batch()
                logger.info(
                    "分红同步进度 %d/%d（检查 %d，失败 %d，写入 %d 行）",
                    index, len(pending), len(done), len(failed), written,
                )
                await _job_update(
                    db,
                    job_id,
                    done=len(done),
                    failed=len(failed),
                    rows_written=written,
                    checkpoint={
                        "params": params,
                        "completed_codes": sorted(done),
                        "checked_codes": sorted(done),
                        "checked_at": today.isoformat(),
                        "failed_codes": dict(sorted(failed.items())[:500]),
                    },
                )

        await _flush_batch()
        elapsed = int((time.perf_counter() - started) * 1000)

        coverage = len(done) / len(code_list) if code_list else 0.0
        report = QualityReport(
            dataset=DATASET,
            rows=written,
            expected=len(code_list),
            coverage=round(coverage, 4),
        )
        if failed:
            report.add(
                "dividends_fetch_failed",
                count=len(failed),
                severity="warning",
                message=f"{len(failed)} 只标的分红抓取失败（未写库，重跑可补）",
                samples=sorted(failed)[:5],
            )
        status = "ok" if written > 0 or not pending else "error"
        if status == "error":
            await _record_quality(
                db, make_error_report(DATASET, "全部标的分红抓取失败"), today, "error"
            )
        else:
            await _record_quality(db, report, today, status)

        final_checkpoint = {
            "params": params,
            "completed_codes": sorted(done),
            "checked_codes": sorted(done),
            "checked_at": today.isoformat(),
            "failed_codes": dict(sorted(failed.items())[:500]),
        }
        await _job_finish(
            db,
            job_id,
            "success" if not failed else "partial",
            started_at=started,
            done=len(done),
            failed=len(failed),
            rows_written=written,
            checkpoint=final_checkpoint,
        )
        return FetchResult(
            rows=written,
            total=len(code_list),
            failed=len(failed),
            elapsed_ms=elapsed,
            detail={
                "task": task,
                "completed": len(done),
                "skipped_fresh": skipped_fresh,
                "pending": len(pending),
                "failed_codes": sorted(failed)[:100],
            },
        )
    except Exception as exc:
        await _job_finish(
            db,
            job_id,
            "failed",
            started_at=started,
            done=len(done),
            failed=len(failed),
            rows_written=written,
            checkpoint={
                "params": params,
                "completed_codes": sorted(done),
                "checked_codes": sorted(done),
                "checked_at": today.isoformat(),
                "failed_codes": dict(sorted(failed.items())[:500]),
            },
            error=f"{type(exc).__name__}: {exc}",
        )
        raise


# ============================================================
# 阶段 2 入口
# ============================================================
async def compute_valuation(
    start: date,
    end: date | None = None,
    codes: list[str] | None = None,
    db: "AsyncSession | None" = None,
) -> FetchResult:
    """阶段 2：用库里的财报/分红 + 库里的收盘价算估值，``UPDATE`` 回 ``fact_stock_daily``。

    **零网络**（不 import、不调用任何 akshare 接口）：输入全部来自
    ``dim_stock_financial``（阶段 1a）、``dim_stock_dividend``（阶段 1b）和
    ``fact_stock_daily.close``，所以可以每日跑、按区间反复跑，全市场单日秒级完成。

    因子口径：
        - ``pe_ttm = close / eps_ttm``、``pb = close / bps``：由
          :func:`build_valuation_frame` 按 ``announced_date`` 做 point-in-time 展开；
        - ``dv_ttm``：由 :func:`dv_ttm_series` 用**近 12 个月现金分红 / 收盘价**算
          （不再用 ``payout_ratio``，它语义是"分红/净利润"而不是股息率）。

    关键口径（point-in-time，防未来函数）：对每个交易日 ``d``，财报只用
    ``announced_date <= d`` 的最新报告期，分红只计 ``ex_date <= d`` 的方案。

    Args:
        start: 起始交易日（含）。
        end: 结束交易日（含）；``None`` = 只算 ``start`` 一天。
        codes: 指定代码；``None`` = 区间内有日线的全部股票。
        db: 外部 ``AsyncSession``（只 flush）；``None`` 时 ``session_scope()`` 自建事务。

    Returns:
        ``FetchResult``：``rows`` = 实际更新的日线行数，``total`` = 处理股票数，
        ``failed`` = 单只计算异常数。``detail`` 含 ``covered``/``no_financials`` 等。
    """
    started = time.perf_counter()
    start_date = _as_date(start)
    end_date = _as_date(end) if end is not None else start_date
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    code_list = await _codes_with_prices(db, start_date, end_date, codes)
    if not code_list:
        logger.info(
            "估值计算：%s~%s 区间内没有日线数据，跳过", start_date, end_date
        )
        return FetchResult(
            rows=0, total=0, elapsed_ms=int((time.perf_counter() - started) * 1000),
            detail={
                "skipped": "no_price_rows",
                "range": [start_date.isoformat(), end_date.isoformat()],
            },
        )

    written = 0
    failed: dict[str, str] = {}
    no_financials: list[str] = []
    covered: set[str] = set()
    for batch in chunked(code_list, COMPUTE_BATCH_CODES):
        batch_codes = list(batch)
        financials = await _load_financials_for_codes(db, batch_codes)
        dividends = await _load_dividends_for_codes(db, batch_codes)
        prices = await _load_price_rows(db, batch_codes, start_date, end_date)
        if prices.empty:
            no_financials.extend(batch_codes)
            continue
        update_rows: list[dict[str, Any]] = []
        for code, price_group in prices.groupby("code", sort=False):
            code = str(code)
            code_financials = (
                financials[financials["code"] == code]
                if not financials.empty
                else financials
            )
            try:
                if code_financials.empty:
                    no_financials.append(code)
                    continue
                price_frame = price_group.drop(columns=["code"]).reset_index(drop=True)
                valuation = build_valuation_frame(price_frame, code_financials)
                # 股息率必须用"近 12 个月现金分红 / 股价"，覆盖 payout_ratio 口径
                code_dividends = (
                    dividends[dividends["code"] == code]
                    if not dividends.empty
                    else dividends
                )
                dv_frame = dv_ttm_series(price_frame, code_dividends)
                valuation = valuation.reset_index(drop=True)
                valuation["dv_ttm"] = dv_frame["dv_ttm"].to_numpy()
                code_rows = _valuation_update_rows(code, valuation)
            except Exception as exc:  # noqa: BLE001 —— 单只失败不影响整体
                failed[code] = f"{type(exc).__name__}: {exc}"
                logger.warning("估值计算失败 code=%s：%s", code, exc)
                continue
            if not code_rows:
                no_financials.append(code)
                continue
            update_rows.extend(code_rows)
            covered.add(code)
        if update_rows:
            written += await _bulk_update_valuation(db, update_rows)

    elapsed = int((time.perf_counter() - started) * 1000)
    coverage = len(covered) / len(code_list) if code_list else 0.0
    report = QualityReport(
        dataset=DATASET,
        rows=written,
        expected=len(code_list),
        coverage=round(coverage, 4),
    )
    if failed:
        report.add(
            "valuation_compute_failed",
            count=len(failed),
            severity="warning",
            message=f"{len(failed)} 只标的估值计算失败（未写库）",
            samples=sorted(failed)[:5],
        )
    if no_financials:
        report.add(
            "valuation_missing_financials",
            count=len(no_financials),
            severity="warning",
            message=(
                f"{len(no_financials)} 只标的没有可用财报（先跑 sync_financials 阶段）"
            ),
            samples=sorted(set(no_financials))[:5],
        )
    if coverage < config.QUALITY_MIN_VALUATION_COVERAGE:
        report.add(
            "valuation_coverage_below_threshold",
            count=len(no_financials),
            severity="error",
            message=(
                f"估值覆盖率 {coverage:.2%} < "
                f"{config.QUALITY_MIN_VALUATION_COVERAGE:.0%}（财报缓存缺失，先补 sync_financials）"
            ),
        )
    if covered:
        # 覆盖率不达标时记 stale，不能记 ok：否则监控看不到 PE/PB 大面积缺失
        await _record_quality(db, report, end_date, "ok" if report.ok else "stale")
    else:
        # 一只都没算出来（例如财报缓存是空的）→ 记 error，前端能立刻看到原因
        await _record_quality(
            db,
            make_error_report(
                DATASET,
                f"{len(code_list)} 只标的都没有可用财报/价格数据（先跑 sync_financials）",
                rows=written,
            ),
            end_date,
            "error",
        )

    return FetchResult(
        rows=written,
        total=len(code_list),
        failed=len(failed),
        elapsed_ms=elapsed,
        detail={
            "task": "compute_valuation",
            "covered": len(covered),
            "no_financials": len(no_financials),
            "failed_codes": sorted(failed)[:100],
            "range": [start_date.isoformat(), end_date.isoformat()],
        },
        quality_status="ok" if report.ok else "stale",
    )


# ============================================================
# 便捷入口（app/pipeline.py 直接用这两个）
# ============================================================
async def backfill_valuation(
    codes: list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
    resume: bool = True,
    db: "AsyncSession | None" = None,
) -> FetchResult:
    """历史估值回补 = 阶段 1a（抓财报）+ 1b（抓分红）+ 阶段 2（算估值写回日线）。

    Args:
        codes: 指定代码；``None`` = 在市全部标的。
        start: 回补起始日期（默认 ``config.HISTORY_START``）。
        end: 结束日期（默认今天）。
        resume: 阶段 1a/1b 是否断点续传。
        db: 外部会话（传入只 flush）。注意：``app/pipeline.py`` 的 backfill 分支
            不传 db，即各阶段各自提交短事务，避免一个事务跨几十分钟。

    Returns:
        ``FetchResult``：``rows`` = 更新的日线行数，``total`` = 处理股票数，
        ``failed`` = 三个阶段的失败股票数之和。``detail`` 带各阶段摘要。
    """
    started = time.perf_counter()
    start_date = _as_date(start) if start is not None else config.HISTORY_START
    end_date = _as_date(end) if end is not None else _today_cn()
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    # TTM 需要"上年年报 + 上年同期"，回补起点往前留两年；同时不低于默认起始年
    start_year = min(DEFAULT_START_YEAR, start_date.year - 2)

    sync_result = await sync_financials(
        codes=codes,
        start_year=start_year,
        db=db,
        only_missing=True,
        resume=resume,
    )
    dividend_result = await sync_dividends(
        codes=codes, db=db, only_missing=True, resume=resume
    )
    compute_result = await compute_valuation(
        start_date, end_date, codes, db=db
    )
    elapsed = int((time.perf_counter() - started) * 1000)
    total = (
        compute_result.total
        if (compute_result.total or 0) > 0
        else sync_result.total
    )
    return FetchResult(
        rows=compute_result.rows,
        total=total,
        failed=sync_result.failed + dividend_result.failed + compute_result.failed,
        elapsed_ms=elapsed,
        detail={
            "task": "backfill_valuation",
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            "start_year": start_year,
            "sync": sync_result.summary(),
            "dividends": dividend_result.summary(),
            "compute": compute_result.summary(),
        },
        quality_status=compute_result.quality_status,
    )


async def collect_latest_valuation(
    trade_date: date, db: "AsyncSession | None" = None
) -> FetchResult:
    """每日增量：只更新 ``trade_date`` 这一天 = 阶段 1a + 1b（``only_missing``）+ 阶段 2（单日）。

    正常情况下阶段 1a/1b 几乎零请求（财报已是最新可得报告期、分红 30 天窗口内不重抓），
    所以每日成本主要是阶段 2 的纯数据库计算，秒级完成。

    Args:
        trade_date: 目标交易日。
        db: 外部会话（``pipeline`` 传入，只 flush 不 commit）。

    Returns:
        ``FetchResult``：``rows`` = 更新的日线行数，``total`` = 当日有行情的股票数。
    """
    started = time.perf_counter()
    target = _as_date(trade_date)
    sync_result = await sync_financials(
        codes=None, start_year=DEFAULT_START_YEAR, db=db, only_missing=True, resume=True
    )
    dividend_result = await sync_dividends(
        codes=None, db=db, only_missing=True, resume=True
    )
    compute_result = await compute_valuation(target, target, None, db=db)
    elapsed = int((time.perf_counter() - started) * 1000)
    total = (
        compute_result.total
        if (compute_result.total or 0) > 0
        else sync_result.total
    )
    return FetchResult(
        rows=compute_result.rows,
        total=total,
        failed=sync_result.failed + dividend_result.failed + compute_result.failed,
        elapsed_ms=elapsed,
        detail={
            "task": "collect_latest_valuation",
            "trade_date": target.isoformat(),
            "sync": sync_result.summary(),
            "dividends": dividend_result.summary(),
            "compute": compute_result.summary(),
        },
        quality_status=compute_result.quality_status,
    )


__all__ = [
    "REPORT_LAG_DAYS",
    "DIVIDEND_REFRESH_DAYS",
    "DV_WINDOW_DAYS",
    "DATASET",
    "FIN_COLUMNS",
    "TTM_COLUMNS",
    "VALUATION_COLUMNS",
    "DIVIDEND_COLUMNS",
    "normalize_financials",
    "eps_ttm_series",
    "build_valuation_frame",
    "normalize_dividends",
    "dv_ttm_series",
    "normalize_abstract_metrics",
    "abstract_eps_frame",
    "fetch_financials",
    "sync_financials",
    "sync_dividends",
    "compute_valuation",
    "backfill_valuation",
    "collect_latest_valuation",
    "latest_knowable_report_period",
]
