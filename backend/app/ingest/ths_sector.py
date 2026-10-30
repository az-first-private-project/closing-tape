"""
同花顺板块资金流兜底源（东方财富接口不可用时的最后一条腿）
==========================================================
背景（2026-10，本机实测）：

    东方财富系接口（``push2.eastmoney.com`` / ``datacenter-web.eastmoney.com``）在
    本机网络被重置（``RemoteDisconnected``），导致

    1. ``fact_sector_daily`` 无数据 → 前端热力图 / 板块评分全空；
    2. 板块资金流为空 → ``engine.sentiment`` 的资金流子项恒为中性 5 分。

    本模块用**同花顺（10jqka）系**接口补这两块，接口由 Lead 联网实测可用：

    ==========================================  ======  ==================================
    akshare 函数                                 行数    备注
    ==========================================  ======  ==================================
    ``ak.stock_fund_flow_industry("即时")``        90     行业资金流，金额单位**亿元**
    ``ak.stock_fund_flow_concept("即时")``        387     概念资金流，列同上
    ``ak.stock_board_industry_name_ths()``         90     ``name / code``（881xxx 稳定代码）
    ==========================================  ======  ==================================

    **明确不可用、本模块绝不调用**：``stock_fund_flow_individual``（10jqka 个股页不可达）、
    ``stock_board_industry_summary_ths``（q.10jqka 不可达）、所有 ``*_em`` 东财接口。

设计原则（与 ``akshare_provider`` 保持一致，但更"诚实"）：

    1. **列名容错**：所有取值走 :func:`_find_column`（精确名 → 子串模糊），缺列给 NaN /
       None，**绝不 KeyError**。同花顺/akshare 改版频繁，采集"能少不能崩"。
    2. **单位一次换算到位**：同花顺资金流金额是**亿元**，入库前统一 ×1e8 成**元**
       （方案 6.2 硬约束：库内金额一律元）。
    3. **不编造数据**：同花顺只给"公司家数"，不细分涨跌家数 → ``up_count`` /
       ``down_count`` 置 ``None``；不提供成交量/成交额/换手率 → 三个字段置 ``None``，
       绝不拿市值或其它字段冒充（历史 bug #18：拿总市值当成交额）。
    4. **不覆盖东财数据**：:func:`collect_sector_fallback` 写库前先查当日
       ``src='akshare'`` 的 ``sector_code`` 集合并跳过，兜底永远只是"补缺"。
    5. **import 期零副作用**：不联网；akshare 未安装时 ``ak = None``，所有能力降级为
       返回空表 / 抛 ``ProviderError``，CI 与离线开发都能 import。
    6. **限频**：异步抓取前 ``await HISTORY_LIMITER.acquire()``（进程级单例，2 请求/秒，
       与 ``service.py`` 共享同一个节流窗口，避免"兜底源偷偷超速"）。

公开 API（Lead 接线用，签名固定）::

    normalize_sector_fundflow(raw, sector_type) -> pd.DataFrame
    build_sector_daily_rows(fundflow, code_map, trade_date) -> list[dict]
    sector_net_inflow_map(fundflow) -> dict[str, float]
    fetch_sector_fundflow() -> tuple[pd.DataFrame, pd.DataFrame]      # async
    collect_sector_fallback(trade_date, db=None) -> FetchResult        # async
    fetch_net_inflow_map_sync(sector_type=None) -> dict[str, float]    # 同步（engine 用）
"""
from __future__ import annotations

import asyncio
import math
import time
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, AsyncIterator, Iterable, Sequence

import pandas as pd

try:  # pragma: no cover —— 依赖是否安装由运行环境决定
    import akshare as ak
except ImportError:  # pragma: no cover
    ak = None  # type: ignore[assignment]

from app.core import config
from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.ingest.base import HISTORY_LIMITER, FetchResult

if TYPE_CHECKING:  # 只为类型检查；运行期延迟 import，避免 import 期创建 DB 引擎
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# 数据源标识：写进 ``fact_sector_daily.src``（模型该列 String(16)）
THS_SRC = "ths"

# 亿元 → 元（同花顺资金流金额口径，实测单位是亿元）
YI = 100_000_000.0

# 板块类型（与 ``fact_sector_daily.sector_type`` / 前端约定一致）
SECTOR_TYPE_INDUSTRY = "industry"
SECTOR_TYPE_CONCEPT = "concept"

# 归一化表的列（顺序即 DataFrame 列顺序，测试与下游都依赖）
NORMALIZED_COLUMNS = (
    "name",              # 板块中文名（行业/概念名）
    "sector_type",       # industry / concept
    "net_inflow",        # 净额（元）
    "inflow",            # 流入资金（元）
    "outflow",           # 流出资金（元）
    "pct_chg",           # 板块涨跌幅（%）
    "index_value",       # 行业指数点位
    "company_count",     # 公司家数
    "leading_stock",     # 领涨股
    "leading_stock_pct", # 领涨股涨跌幅（%）
)

# 需要按数值处理（并统一转 float / NaN）的列
_NUMERIC_COLUMNS = (
    "net_inflow",
    "inflow",
    "outflow",
    "pct_chg",
    "index_value",
    "company_count",
    "leading_stock_pct",
)

# ============================================================
# 列名候选（实测列名在前，其余是"漂移"兜底）
# ============================================================
# name 的模糊匹配必须排除这些语义列：否则候选 "行业" 会命中 "行业指数"/"行业-涨跌幅"
_NAME_TOKENS = ("涨跌幅", "指数", "净额", "流入", "流出", "家数", "价", "代码", "序号", "净流入")

COL_NAME = ("行业", "概念", "板块名称", "行业名称", "概念名称", "名称", "板块", "name")
COL_NET = ("净额", "资金净额", "净流入", "主力净额", "主力净流入-净额", "net_inflow")
COL_INFLOW = ("流入资金", "资金流入", "流入", "inflow")
COL_OUTFLOW = ("流出资金", "资金流出", "流出", "outflow")
# 涨跌幅：注意不能命中 "领涨股-涨跌幅"（靠 exclude 里的 "领涨" 兜住）
COL_PCT = ("行业-涨跌幅", "板块涨跌幅", "涨跌幅", "涨幅", "pct_chg", "pctChg")
COL_INDEX_VALUE = ("行业指数", "板块指数", "指数", "index_value")
COL_COMPANY_COUNT = ("公司家数", "家数", "成分股数", "company_count")
COL_LEADER = ("领涨股", "领涨股票", "龙头股", "领涨个股", "leading_stock")
COL_LEADER_PCT = (
    "领涨股-涨跌幅", "领涨股票-涨跌幅", "领涨股涨跌幅", "领涨股涨跌幅(%)",
    "领涨股票涨幅", "leading_stock_pct",
)
# 板块代码表（stock_board_industry_name_ths）
COL_BOARD_CODE = ("code", "代码", "板块代码", "sector_code")
COL_BOARD_NAME = ("name", "名称", "板块名称", "行业名称")

# 候选名里出现这些"单位/格式"字符时清洗掉再找列
_EMPTY_STRINGS = {"", "-", "--", "—", "nan", "none", "null", "none", "None", "NaN"}


# ============================================================
# 通用小工具（列名容错 / 数值清洗 / 空值归一）
# ============================================================
def _norm_header(value: Any) -> str:
    """列名归一化：去首尾空白 / 全角空格 / 内部空格，转小写。

    这样 ``"净额 "``、``"净额"``、``" 净 额"`` 都能等价比较；中文字符不受影响。
    """
    return str(value).replace("\u3000", "").replace(" ", "").strip().lower()


# 归一化后的"空值"集合（预计算，避免每行数据都重新构建集合）
_EMPTY_NORMALIZED = frozenset(_norm_header(item) for item in _EMPTY_STRINGS)


def _is_blank_text(text: Any) -> bool:
    """字符串是否等价于"没有值"（"" / "-" / "--" / "nan" / "none" ...）。"""
    return _norm_header(text) in _EMPTY_NORMALIZED


def _find_column(
    frame: pd.DataFrame | None,
    candidates: Sequence[str],
    *,
    exclude: Sequence[str] = (),
) -> str | None:
    """在 DataFrame 里按候选列名找一列，返回**真实列名**；找不到返回 None。

    匹配策略（两级，缺列绝不抛错）：

    1. **精确匹配**（归一化后相等）：候选顺序即优先级，用于"越靠前越精确"的语义；
    2. **模糊匹配**（候选名是列名的子串）：容忍版本漂移，例如把 ``净额`` 改成
       ``资金净额``、``流入资金`` 改成 ``资金流入``、``行业指数`` 改成 ``行业指数(点)``。

    ``exclude`` 是模糊匹配的排除词：例如 ``name`` 列不允许命中含"指数/涨跌幅/家数"的列，
    否则候选 ``行业`` 会错误命中 ``行业指数``。

    Args:
        frame: 原始表（可以为 None）。
        candidates: 候选列名，按优先级排列。
        exclude: 模糊匹配时排除的"语义干扰"子串（归一化后比较）。

    Returns:
        真实列名（可与候选名不同），或 None。
    """
    if frame is None or not hasattr(frame, "columns"):
        return None
    columns = list(frame.columns)
    if not columns:
        return None

    normalized = {_norm_header(column): column for column in columns}
    for candidate in candidates:
        key = _norm_header(candidate)
        if key and key in normalized:
            return normalized[key]

    excluded = tuple(_norm_header(token) for token in exclude if token)
    for candidate in candidates:
        key = _norm_header(candidate)
        if not key:
            continue
        for column in columns:
            text = _norm_header(column)
            if key in text and not any(token in text for token in excluded):
                return column
    return None


def _to_number(value: Any) -> float | None:
    """把任意值清洗成 float（保留原始量纲）；无法转换 / NaN / Inf 返回 None。

    清洗内容：千分位逗号（中文逗号也算）、百分号、"元"字；并处理 ``亿`` / ``万``
    后缀（换算到"亿"这一量纲，供资金流列再统一 ×1e8）。

    注意：**本函数不做 亿元 → 元 的换算**，那是 :func:`_to_yuan` 的职责。
    """
    if value is None:
        return None
    if isinstance(value, bool):  # bool 是 int 子类，误当 1/0 会污染数据
        return None

    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = (
            value.strip()
            .replace(",", "")
            .replace("，", "")
            .replace("%", "")
            .replace("％", "")
            .replace("元", "")
            .strip()
        )
        if _is_blank_text(text):
            return None
        scale = 1.0
        if text.endswith("亿"):
            text, scale = text[:-1].strip(), 1.0
        elif text.endswith("万"):
            # "35000万" = 3.5 亿 → 换到"亿"量纲
            text, scale = text[:-1].strip(), 1e-4
        try:
            number = float(text) * scale
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


def _to_yuan(value: Any) -> float | None:
    """亿元 → 元（同花顺资金流金额列专用）。"""
    number = _to_number(value)
    return None if number is None else number * YI


def _to_int(value: Any) -> int | None:
    """转 int（公司家数等计数字段）；失败返回 None。"""
    number = _to_number(value)
    if number is None:
        return None
    try:
        return int(round(number))
    except (OverflowError, ValueError):
        return None


def _clean_str(value: Any) -> str | None:
    """清洗成非空字符串；空串 / "-" / "--" / NaN / "nan" 一律返回 None。"""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip()
    return None if _is_blank_text(text) else text


def _none_if_na(value: Any) -> Any:
    """DataFrame 空值（NaN / pd.NA / NaT）统一成 None，写库时才变 DB NULL。"""
    if value is None:
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):  # 列表/数组类值：不是空值
        pass
    return value


def _to_date(value: Any) -> date | None:
    """把 date / datetime / Timestamp / 常见日期字符串统一成 ``datetime.date``。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, pd.Timestamp):
        return value.date()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


# 同花顺"即时"资金流只反映**当前盘口**，不能回溯历史日期。
# 允许的最大滞后天数：覆盖周末 + 长假（如春节 9 天休市）的正常补跑窗口。
# 超过该窗口的 trade_date 一律拒绝写入——用"今天的数据"填历史日期等于制造未来函数，
# 比"没数据"危险得多。
_REALTIME_MAX_LAG_DAYS = 10


def _today_cn() -> date:
    """当前中国时区日期（容器默认 UTC，直接用 date.today() 会差一天）。"""
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(str(getattr(config, "TIMEZONE", "Asia/Shanghai")))).date()


def _normalize_kind(sector_type: Any) -> str:
    """板块类型归一化：industry / concept / all（None 或无法识别 → all）。"""
    text = str(sector_type or "").strip().lower()
    if text in ("industry", "行业", "行业资金流", "hy"):
        return SECTOR_TYPE_INDUSTRY
    if text in ("concept", "概念", "概念资金流", "gn", "板块"):
        return SECTOR_TYPE_CONCEPT
    return "all"


def _sector_type_value(value: Any) -> str:
    """归一化成入库用的 sector_type（只有 industry / concept 两种，默认 concept）。"""
    if _normalize_kind(value) == SECTOR_TYPE_INDUSTRY:
        return SECTOR_TYPE_INDUSTRY
    return SECTOR_TYPE_CONCEPT


def _empty_normalized() -> pd.DataFrame:
    """空但**列齐全**的归一化表（缺列容忍的最后一步：下游永远不用判空列）。"""
    frame = pd.DataFrame({column: pd.Series(dtype="float64") for column in _NUMERIC_COLUMNS})
    frame["name"] = pd.Series(dtype="object")
    frame["sector_type"] = pd.Series(dtype="object")
    frame["leading_stock"] = pd.Series(dtype="object")
    return frame[list(NORMALIZED_COLUMNS)]


# ============================================================
# 1) 原始表 → 归一化表（纯函数，不联网）
# ============================================================
def normalize_sector_fundflow(raw: pd.DataFrame, sector_type: str) -> pd.DataFrame:
    """把同花顺资金流原始表归一化成统一口径的 DataFrame。

    为什么需要归一化：同花顺两张表（行业 90 行 / 概念 387 行）列名相同但
    "行业"列在概念表里装的是**概念名**，且金额单位是亿元、数值可能是字符串
    （``"2.35%"``、``"1,234.5"``）。集中在这里换算，保证入库口径唯一：

    - 金额（净额/流入/流出）：亿元 → **元**（×1e8）；
    - ``pct_chg`` / ``leading_stock_pct``：百分数原样保留（2.35 表示 2.35%）；
    - ``index_value`` / ``company_count``：数值；
    - 列名走 :func:`_find_column` 容错，**缺列给 NaN，绝不 KeyError**。

    Args:
        raw: 原始表（``ak.stock_fund_flow_industry`` / ``stock_fund_flow_concept``）。
        sector_type: ``industry`` / ``concept``（也接受中文"行业"/"概念"）。

    Returns:
        DataFrame，列见 :data:`NORMALIZED_COLUMNS`；无数据时返回**同列空表**。
        数值列已 ``to_numeric(errors="coerce")``，缺失为 NaN（转库行时才变 None）。
    """
    normalized_type = _sector_type_value(sector_type)
    if raw is None or not isinstance(raw, pd.DataFrame) or raw.empty:
        return _empty_normalized()

    name_col = _find_column(raw, COL_NAME, exclude=_NAME_TOKENS)
    if name_col is None:
        logger.warning(
            "同花顺%s资金流列名不识别（缺板块名列）：%s",
            normalized_type, [str(column) for column in raw.columns],
        )
        return _empty_normalized()

    net_col = _find_column(
        raw, COL_NET, exclude=("家数", "指数", "涨跌幅", "价", "率", "占比")
    )
    inflow_col = _find_column(
        raw, COL_INFLOW, exclude=("净额", "净流入", "流出", "家数", "率", "占比")
    )
    outflow_col = _find_column(
        raw, COL_OUTFLOW, exclude=("净额", "净流入", "流入", "家数", "率", "占比")
    )
    pct_col = _find_column(raw, COL_PCT, exclude=("领涨", "家数", "指数", "率"))
    index_col = _find_column(
        raw, COL_INDEX_VALUE, exclude=("涨跌幅", "家数", "净额", "资金", "股", "价")
    )
    company_col = _find_column(raw, COL_COMPANY_COUNT, exclude=("净额", "涨跌幅", "指数", "资金"))
    leader_col = _find_column(raw, COL_LEADER, exclude=("涨跌幅", "家数", "指数", "净额", "资金"))
    leader_pct_col = _find_column(raw, COL_LEADER_PCT, exclude=("行业", "家数", "指数", "净额", "资金"))

    records: list[dict[str, Any]] = []
    for row in raw.to_dict("records"):
        name = _clean_str(row.get(name_col))
        if not name:  # 没有板块名就无法做 code / 情绪映射，跳过（不编造）
            continue
        records.append(
            {
                "name": name,
                "sector_type": normalized_type,
                # 亿元 → 元
                "net_inflow": _to_yuan(row.get(net_col)) if net_col else None,
                "inflow": _to_yuan(row.get(inflow_col)) if inflow_col else None,
                "outflow": _to_yuan(row.get(outflow_col)) if outflow_col else None,
                # 百分数原样（2.35 = 2.35%）
                "pct_chg": _to_number(row.get(pct_col)) if pct_col else None,
                "index_value": _to_number(row.get(index_col)) if index_col else None,
                "company_count": _to_int(row.get(company_col)) if company_col else None,
                "leading_stock": _clean_str(row.get(leader_col)) if leader_col else None,
                "leading_stock_pct": (
                    _to_number(row.get(leader_pct_col)) if leader_pct_col else None
                ),
            }
        )

    if not records:
        return _empty_normalized()

    frame = pd.DataFrame(records, columns=list(NORMALIZED_COLUMNS))
    for column in _NUMERIC_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


# ============================================================
# 2) 归一化表 → fact_sector_daily 行（纯函数，不联网）
# ============================================================
def build_sector_daily_rows(
    fundflow: pd.DataFrame,
    code_map: dict[str, str],
    trade_date: date,
) -> list[dict]:
    """把归一化资金流表转成 ``fact_sector_daily`` 形状的行（可直接 upsert）。

    字段映射与**诚实边界**：

    - ``sector_code``：**一律使用确定性编码** ``f"THS_{板块名}"``。
      不再优先用 ``code_map`` 里的 ``881xxx``：同花顺代码表依赖 ``q.10jqka.com.cn``，
      该域名时通时不通；一旦某次可用、某次不可用，同一板块会以 ``881121`` 与
      ``THS_半导体`` 两个主键各存一份，导致行数虚高、评分重复。
      参数 ``code_map`` 仅为向后兼容保留（当前忽略）。
    - ``close`` = ``index_value``（同花顺"行业指数"点位；不是价格，语义上就是板块指数收盘）；
    - ``pct_chg`` = ``行业-涨跌幅``；
    - ``up_count`` / ``down_count``：**恒为 None**。同花顺只给"公司家数"，不给涨跌家数；
      用家数的一半或随便拆一个比例都是编造，宁可留空让前端显示"暂无"；
    - ``volume`` / ``amount`` / ``turnover_rate``：**恒为 None**。该源不提供成交量/成交额/
      换手率，绝不能拿市值或其它字段兜底（历史 bug #18：拿总市值冒充成交额）；
    - ``open/high/low/pre_close/amplitude``：该源只给一个点位与涨跌幅，同样置 None；
    - ``src`` = ``"ths"``，方便与东财（``akshare``）区分、也方便前端标注"兜底数据"。

    同一 ``sector_code`` 只保留最后一行：PostgreSQL 的
    ``ON CONFLICT DO UPDATE`` 不允许同一条语句内重复命中同一冲突键
    （``upsert_rows`` 自身也会去重，这里提前合并保证写入行数可预期）。

    Args:
        fundflow: :func:`normalize_sector_fundflow` 的输出。
        code_map: ``{板块名: 同花顺板块代码}``（来自
            ``stock_board_industry_name_ths``）；可为 None / 空 dict。
        trade_date: 交易日；接受 ``date`` / ``datetime`` / ``Timestamp`` / ``"2026-10-21"``。

    Returns:
        行字典列表（键与 ``FactSectorDaily`` 列名一致，可直接 ``upsert_rows``）。

    Raises:
        ValueError: ``trade_date`` 无法解析成日期。**宁可直接报错**，也不要写错日期
            （错一行交易日就会污染板块时间序列与回测）。
    """
    day = _to_date(trade_date)
    if day is None:
        raise ValueError(f"trade_date 无法解析为交易日：{trade_date!r}")

    if fundflow is None or not isinstance(fundflow, pd.DataFrame) or fundflow.empty:
        return []

    cleaned_code_map: dict[str, str] = {}
    for raw_name, raw_code in (code_map or {}).items():
        clean_name, clean_code = _clean_str(raw_name), _clean_str(raw_code)
        if clean_name and clean_code:
            cleaned_code_map[clean_name] = clean_code

    rows: list[dict[str, Any]] = []
    for record in fundflow.to_dict("records"):
        name = _clean_str(record.get("name"))
        if not name:
            continue

        # 代码稳定性优先（见 docstring）：一律 THS_ 前缀，避免代码表可用性波动造成重复主键
        sector_code = f"THS_{name}"
        close = _none_if_na(record.get("index_value"))
        pct_chg = _none_if_na(record.get("pct_chg"))
        leading_stock = _clean_str(record.get("leading_stock"))
        leading_stock_pct = _none_if_na(record.get("leading_stock_pct"))
        sector_type = _sector_type_value(record.get("sector_type"))

        rows.append(
            {
                "sector_code": str(sector_code).strip()[:24],
                "trade_date": day,
                "sector_name": name[:64],
                "sector_type": sector_type,
                # 同花顺不提供 OHLC / 前收 / 振幅 → 全部 None（不拿其它字段凑）
                "open": None,
                "high": None,
                "low": None,
                # 同花顺"行业指数"就是板块指数收盘点位
                "close": close,
                "pre_close": None,
                "pct_chg": pct_chg,
                "amplitude": None,
                # 该源不提供成交量 / 成交额 / 换手率 —— 恒为 None，写死防止后人"好心"填市值
                "volume": None,
                "amount": None,
                "turnover_rate": None,
                # 只给公司家数，不编造涨跌家数
                "up_count": None,
                "down_count": None,
                "leading_stock": leading_stock[:64] if leading_stock else None,
                "leading_stock_pct": leading_stock_pct,
                "src": THS_SRC,
            }
        )

    # 同 sector_code 去重（后者覆盖前者），保证 upsert 不重复命中同一冲突键
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        unique[row["sector_code"]] = row
    return list(unique.values())


# ============================================================
# 3) 归一化表 → 板块资金流映射（纯函数，engine 情绪子项用）
# ============================================================
def sector_net_inflow_map(fundflow: pd.DataFrame) -> dict[str, float]:
    """``{板块中文名: 净额(元)}``，供板块情绪（资金流子项）使用。

    处理约定：

    - **同名去重求和**：概念表里同名板块偶有重复行（不同来源合并），累加而不是互相覆盖；
    - 净额缺失（缺列 / 脏数据）的板块直接跳过：没有数据就不参与情绪加权，
      让 ``combine_sentiment`` 按"缺项权重再分配"处理，而不是给 0 元假装中性；
    - 名字做去空白处理，避免 ``"半导体 "`` 与 ``"半导体"`` 出现两个键。

    Args:
        fundflow: :func:`normalize_sector_fundflow` 的输出。

    Returns:
        ``{名称: 净额(元)}``；无数据返回 ``{}``。
    """
    mapping: dict[str, float] = {}
    if fundflow is None or not isinstance(fundflow, pd.DataFrame) or fundflow.empty:
        return mapping
    for record in fundflow.to_dict("records"):
        name = _clean_str(record.get("name"))
        if not name:
            continue
        value = _none_if_na(record.get("net_inflow"))
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isnan(numeric) or math.isinf(numeric):
            continue
        mapping[name] = mapping.get(name, 0.0) + numeric
    return mapping


def _parse_industry_code_map(raw: pd.DataFrame | None) -> dict[str, str]:
    """``stock_board_industry_name_ths`` 原始表 → ``{行业名: 881xxx}``。

    列名容错（name/code、名称/代码）；代码统一成字符串（``881121`` 不能变 int，
    否则 "881121.0" 之类的脏值会污染主键）；同名只留第一条。
    """
    if raw is None or not isinstance(raw, pd.DataFrame) or raw.empty:
        return {}
    name_col = _find_column(raw, COL_BOARD_NAME)
    code_col = _find_column(raw, COL_BOARD_CODE)
    if name_col is None or code_col is None:
        logger.warning(
            "同花顺行业代码表列名不识别：%s", [str(column) for column in raw.columns]
        )
        return {}
    mapping: dict[str, str] = {}
    for row in raw.to_dict("records"):
        name = _clean_str(row.get(name_col))
        code = _clean_str(row.get(code_col))
        if not name or not code:
            continue
        # 代码统一去空白；881121 这种整数若被 pandas 读成 int，str() 后仍是 "881121"
        mapping.setdefault(name, code.split(".")[0].strip())
    return mapping


# ============================================================
# 4) 联网抓取（异步）
# ============================================================
def _fundflow_func(sector_type: str) -> tuple[Any, str]:
    """按板块类型取 akshare 函数对象与函数名（``ak`` 可能被测试替换）。"""
    name = (
        "stock_fund_flow_industry"
        if sector_type == SECTOR_TYPE_INDUSTRY
        else "stock_fund_flow_concept"
    )
    return getattr(ak, name, None), name


def _network_blocked() -> str | None:
    """返回"禁止联网"的原因文本；可以联网时返回 None。"""
    if ak is None:
        return "akshare 未安装（ImportError 降级）"
    if not bool(getattr(config, "INGEST_NETWORK_ENABLED", True)):
        return "INGEST_NETWORK_ENABLED=0，禁止联网采集"
    return None


async def fetch_sector_fundflow() -> tuple[pd.DataFrame, pd.DataFrame]:
    """联网抓取（行业, 概念）两张**归一化**资金流表。

    - 每次请求前 ``await HISTORY_LIMITER.acquire()``：进程级单例限频 2 请求/秒，
      与 ``service.py`` 共用节流窗口，兜底源不会绕过合规边界；
    - 单张表失败 → 记 warning、该表返回空表（另一张仍然可用）；
    - **两张都失败** → 抛 :class:`ProviderError`（"兜底源也不可用"必须让上层知道，
      否则会被误判成"今天板块资金流就是 0"）；
    - 同步 akshare 调用放到线程池，不阻塞事件循环。

    Returns:
        ``(行业表, 概念表)``，均带 :data:`NORMALIZED_COLUMNS` 全部列。

    Raises:
        ProviderError: akshare 不可用 / 禁止联网 / 两张表都抓不到数据。
    """
    blocked = _network_blocked()
    if blocked is not None:
        logger.warning("同花顺板块资金流兜底不可用：%s", blocked)
        raise ProviderError(
            f"同花顺板块资金流兜底不可用：{blocked}",
            detail={"source": THS_SRC, "reason": blocked},
        )

    frames: dict[str, pd.DataFrame] = {}
    errors: dict[str, str] = {}
    for sector_type in (SECTOR_TYPE_INDUSTRY, SECTOR_TYPE_CONCEPT):
        func, func_name = _fundflow_func(sector_type)
        if func is None:  # 理论上不会发生，防御 akshare 版本删函数
            errors[sector_type] = f"{func_name} 不存在"
            frames[sector_type] = _empty_normalized()
            continue
        await HISTORY_LIMITER.acquire()  # ≤2 请求/秒
        try:
            raw = await asyncio.to_thread(func, symbol="即时")
            frame = normalize_sector_fundflow(raw, sector_type)
            if frame.empty:
                errors[sector_type] = "空表"
            frames[sector_type] = frame
        except Exception as exc:  # noqa: BLE001 —— 外部数据源失败不能打断主采集
            logger.warning("同花顺 %s 资金流获取失败：%s: %s", func_name, type(exc).__name__, exc)
            errors[sector_type] = f"{type(exc).__name__}: {exc}"
            frames[sector_type] = _empty_normalized()

    industry = frames[SECTOR_TYPE_INDUSTRY]
    concept = frames[SECTOR_TYPE_CONCEPT]
    if industry.empty and concept.empty:
        raise ProviderError(
            "同花顺板块资金流（行业 + 概念）双双失败",
            detail={"source": THS_SRC, "errors": errors},
        )
    return industry, concept


async def fetch_industry_code_map() -> dict[str, str]:
    """联网抓同花顺行业代码表 ``{行业名: 881xxx}``；失败返回 ``{}``（不抛异常）。

    为什么单独一个函数：代码表只用于给**行业**板块配稳定 ``sector_code``，
    抓不到就退化为 ``THS_行业名``（确定性兜底），不该拖垮整条兜底链路。
    """
    blocked = _network_blocked()
    if blocked is not None:
        logger.warning("同花顺行业代码表不可用：%s", blocked)
        return {}
    func = getattr(ak, "stock_board_industry_name_ths", None)
    if func is None:
        logger.warning("akshare 无 stock_board_industry_name_ths，行业代码退化为 THS_ 前缀")
        return {}
    await HISTORY_LIMITER.acquire()
    try:
        raw = await asyncio.to_thread(func)
        mapping = _parse_industry_code_map(raw)
        if not mapping:
            logger.warning("同花顺行业代码表为空，行业代码退化为 THS_ 前缀")
        return mapping
    except Exception as exc:  # noqa: BLE001
        logger.warning("同花顺行业代码表获取失败：%s: %s", type(exc).__name__, exc)
        return {}


# ============================================================
# 5) 同步版本（engine.sector_analysis 在 async 上下文里同步调用）
# ============================================================
# 进程内缓存：{kind: (自然日, 失效时刻 monotonic, 映射)}
# - 成功：当日一直有效（expire=inf），避免 86 个板块各请求一次；
# - 失败：只缓存 300s，网络抖一下不会把一整天都钉死成空值。
_SYNC_FAILURE_TTL_SEC = 300.0
_SYNC_CACHE: dict[str, tuple[date, float, dict[str, float]]] = {}


def _sync_cache_lookup(kind: str) -> dict[str, float] | None:
    """读同步缓存；过期 / 跨日 / 无缓存返回 None。"""
    entry = _SYNC_CACHE.get(kind)
    if entry is None:
        return None
    cached_day, expire_at, mapping = entry
    if cached_day != _today_cn():
        return None
    if expire_at != float("inf") and time.monotonic() > expire_at:
        return None
    return mapping


def _sync_cache_store(kind: str, mapping: dict[str, float], ok: bool) -> None:
    """写同步缓存：成功当日有效，失败 300s 后允许重试。"""
    expire_at = float("inf") if ok and mapping else time.monotonic() + _SYNC_FAILURE_TTL_SEC
    _SYNC_CACHE[kind] = (_today_cn(), expire_at, mapping)


def clear_net_inflow_cache() -> None:
    """清空同步资金流缓存（测试 / 运维强制刷新用）。"""
    _SYNC_CACHE.clear()


def _fetch_fundflow_sync(sector_type: str) -> pd.DataFrame:
    """同步调用一次同花顺资金流接口（原始表）；失败抛异常，由调用方决定降级。"""
    func, func_name = _fundflow_func(sector_type)
    if func is None:
        raise ProviderError(f"akshare 无 {func_name}", detail={"source": THS_SRC})
    return func(symbol="即时")


def fetch_net_inflow_map_sync(sector_type: str | None = None) -> dict[str, float]:
    """**同步**获取 ``{板块中文名: 净额(元)}``（给 ``engine.sector_analysis`` 兜底用）。

    为什么需要同步版本：``sector_analysis._calc_moneyflow_sentiment`` 是 async 函数但
    内部的 akshare 降级是**同步**调用（放线程池），没法 ``await`` 一个 async 抓取函数；
    而它会被 86 个板块各调用一次，所以必须带**进程内当日缓存**（命中缓存是 0 秒，
    不产生任何网络请求）。

    行为约定（**绝不抛异常**，失败返回 ``{}``，由调用方给中性分）：

    - ``sector_type``：``industry`` / ``concept`` / ``None``；``None`` 表示两张表合并，
      **同名以行业优先**（行业分类更稳定，概念表大量重复交叉）；
    - 首次调用约 0.4–2 秒（2 次 HTTP）；之后命中缓存立即返回；
    - 失败按 300s TTL 缓存空结果，避免短时间内 86 次重复失败拖慢评分；
    - ``INGEST_NETWORK_ENABLED=0`` 或 akshare 未安装 → 直接 ``{}``（不联网）。

    Args:
        sector_type: ``"industry"`` / ``"concept"`` / ``None``（也容忍中文"行业"/"概念"）。

    Returns:
        ``{板块中文名: 净额(元)}``；失败或无数据返回 ``{}``。
    """
    kind = _normalize_kind(sector_type)
    cached = _sync_cache_lookup(kind)
    if cached is not None:
        return dict(cached)

    blocked = _network_blocked()
    if blocked is not None:
        logger.warning("同花顺同步板块资金流不可用：%s", blocked)
        _sync_cache_store(kind, {}, False)
        return {}

    kinds: tuple[str, ...] = (
        (SECTOR_TYPE_INDUSTRY, SECTOR_TYPE_CONCEPT) if kind == "all" else (kind,)
    )
    mapping: dict[str, float] = {}
    any_ok = False

    for item in kinds:
        try:
            raw = _fetch_fundflow_sync(item)
            part = sector_net_inflow_map(normalize_sector_fundflow(raw, item))
        except Exception as exc:  # noqa: BLE001 —— 兜底源失败绝不能打断评分
            logger.warning(
                "同花顺同步板块资金流失败（%s）：%s: %s", item, type(exc).__name__, exc
            )
            continue
        if not part:
            logger.warning("同花顺同步板块资金流为空（%s）", item)
            continue
        any_ok = True
        if not mapping:
            mapping.update(part)
        else:
            # 合并：同名以先到的（行业）优先
            for name, value in part.items():
                mapping.setdefault(name, value)

    _sync_cache_store(kind, mapping, any_ok)
    return dict(mapping)


# ============================================================
# 6) 落库（不覆盖东财数据）
# ============================================================
def _skip_protected(
    rows: Sequence[dict[str, Any]], protected_names: Iterable[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """剔除板块名已被"受保护源"占用的行。

    为什么必须做：兜底源只有涨跌幅 + 指数点位，没有成交量/成交额/涨跌家数；
    如果它覆盖了东财的完整行，等于**用残缺数据替换完整数据**（静默数据降级）。
    所以当日已存在 ``src='akshare'`` 的板块一律跳过。

    按**板块名**而不是 ``sector_code`` 判断：兜底行一律 ``THS_名称``，
    东财行是 ``881xxx``，按代码比对永远匹配不上，等于保护失效。

    Returns:
        ``(保留行, 被跳过的板块名列表)``。
    """
    protected = {str(name).strip() for name in protected_names if name}
    kept: list[dict[str, Any]] = []
    skipped: list[str] = []
    for row in rows:
        name = str(row.get("sector_name") or "").strip()
        if name in protected:
            skipped.append(name)
            continue
        kept.append(row)
    return kept, skipped


@asynccontextmanager
async def _db_scope(db: AsyncSession | None) -> AsyncIterator[AsyncSession]:
    """会话作用域：传入 ``db`` 时沿用调用方事务（只 flush 不 commit），否则自建并提交。"""
    if db is not None:
        yield db
        return
    from app.db.session import session_scope  # 延迟 import：import 期不创建 DB 引擎

    async with session_scope() as session:
        yield session


async def _protected_sector_names(session: Any, trade_date: date) -> set[str]:
    """查当日 ``src='akshare'`` 的板块名集合（兜底源必须避开它们）。"""
    from sqlalchemy import select

    from app.db.models.fact import FactSectorDaily

    stmt = select(FactSectorDaily.sector_name).where(
        FactSectorDaily.trade_date == trade_date,
        FactSectorDaily.src == "akshare",
    )
    result = await session.execute(stmt)
    return {str(name).strip() for (name,) in result.all() if name}


async def collect_sector_fallback(
    trade_date: date, db: AsyncSession | None = None
) -> FetchResult:
    """把同花顺板块资金流写成 ``fact_sector_daily`` 行（幂等 upsert，**绝不覆盖东财**）。

    写入规则（幂等 + 不降级）：

    1. 抓取（行业 + 概念）两张归一化表（行业代码表仍会尝试抓取，仅用于观测）；
    2. 生成 ``fact_sector_daily`` 行（``src='ths'``，代码一律 ``THS_名称``）；
    3. 查当日 ``src='akshare'`` 的**板块名**集合，**跳过**同名板块
       —— 东财数据完整，兜底只补空缺；
    4. ``upsert_rows``（冲突键 ``sector_code + trade_date``），重跑不产生重复行；
    5. ``db`` 为 None 时自建会话并提交；传入时**只 flush 不 commit**（事务归调用方）。

    Args:
        trade_date: 交易日（date / 字符串均可）。
        db: 可选 ``AsyncSession``，用于复用调用方事务。

    Returns:
        :class:`FetchResult`：
            - ``rows``   = 实际写入行数（已去重、已跳过东财板块）；
            - ``total``  = 本次抓到的总行数（写入前的基数，含被跳过的）；
            - ``failed`` = 失败源数（行业 / 概念各算一个）；
            - ``detail`` = ``{source, industry, concept, code_map, skipped_akshare,
              skipped_names, error?}``，可直接 JSON 序列化。

    Raises:
        ValueError: ``trade_date`` 无法解析（宁可直接失败，也不要写错日期）。
        DB 异常原样抛出（由调用方 job tracker 记失败，不在这里吞掉）。

    日期约束（重要）：数据源只有**即时**快照，没有历史。因此 ``trade_date``
    必须是"今天"或最近 ``_REALTIME_MAX_LAG_DAYS`` 天内的日期（容忍周末/长假补跑）；
    未来日期 → ``skipped="future_date"``，更早的历史日期 →
    ``skipped="not_realtime_date"``，两者都**不写库**。用今天的快照填历史日期
    等于给回测注入未来函数，比"没数据"危险得多。
    """
    started = time.perf_counter()
    day = _to_date(trade_date)
    if day is None:
        raise ValueError(f"trade_date 无法解析为交易日：{trade_date!r}")

    # 硬约束：同花顺只提供"即时"快照，不能回溯。历史日期必须走东财历史接口，
    # 这里直接跳过而不是把今天的数据写进历史日期（后者 = 未来函数，回测会被污染）。
    today = _today_cn()
    if day > today:
        logger.warning("同花顺板块兜底跳过未来日期：%s（今天 %s）", day, today)
        return FetchResult(
            rows=0,
            total=0,
            failed=0,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            detail={
                "source": THS_SRC,
                "skipped": "future_date",
                "trade_date": day.isoformat(),
                "realtime_only": True,
            },
        )
    if (today - day).days > _REALTIME_MAX_LAG_DAYS:
        logger.warning(
            "同花顺板块兜底跳过历史日期：%s（今天 %s，仅支持即时快照）", day, today
        )
        return FetchResult(
            rows=0,
            total=0,
            failed=0,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            detail={
                "source": THS_SRC,
                "skipped": "not_realtime_date",
                "trade_date": day.isoformat(),
                "realtime_only": True,
            },
        )

    try:
        industry_df, concept_df = await fetch_sector_fundflow()
    except Exception as exc:  # noqa: BLE001 —— 兜底源失败必须"降级为空"而不是 raise
        elapsed = int((time.perf_counter() - started) * 1000)
        logger.warning("同花顺板块日线兜底失败（%s）：%s: %s", day, type(exc).__name__, exc)
        return FetchResult(
            rows=0,
            total=0,
            failed=2,
            elapsed_ms=elapsed,
            detail={
                "source": THS_SRC,
                "error": f"{type(exc).__name__}: {exc}",
                "skipped": "fetch_failed",
            },
        )

    code_map = await fetch_industry_code_map()
    # 行业表：用同花顺 881xxx 代码表配稳定代码；
    # 概念表**不套行业代码表**——概念名常与行业名同名（如"半导体"），
    # 若把行业代码套到概念上，两者会撞同一个主键、互相覆盖。
    industry_rows = build_sector_daily_rows(industry_df, code_map, day)
    concept_rows = build_sector_daily_rows(concept_df, {}, day)
    # 跨表同 code 去重（先到先得：行业优先），保证 upsert 不会重复命中同一冲突键
    merged: dict[str, dict[str, Any]] = {}
    for row in industry_rows + concept_rows:
        merged.setdefault(row["sector_code"], row)
    all_rows = list(merged.values())
    total = len(all_rows)
    failed = int(industry_df.empty) + int(concept_df.empty)

    written = 0
    skipped: list[str] = []
    if all_rows:
        from app.db.models.fact import FactSectorDaily  # 延迟 import：import 期不连库
        from app.ingest.base import upsert_rows

        async with _db_scope(db) as session:
            protected = await _protected_sector_names(session, day)
            rows, skipped = _skip_protected(all_rows, protected)
            if rows:
                written = await upsert_rows(
                    session, FactSectorDaily, rows, ["sector_code", "trade_date"]
                )
            if db is not None:
                await session.flush()  # 只 flush，事务提交权归调用方

    elapsed = int((time.perf_counter() - started) * 1000)
    detail: dict[str, Any] = {
        "source": THS_SRC,
        "industry": len(industry_rows),
        "concept": len(concept_rows),
        "code_map": len(code_map),
        "skipped_akshare": len(skipped),
        "skipped_names": sorted(skipped)[:50],
    }
    logger.info(
        "同花顺板块日线兜底完成（%s）：抓取 %d 行，写入 %d 行，跳过东财 %d 个板块",
        day, total, written, len(skipped),
    )
    return FetchResult(
        rows=written,
        total=total,
        failed=failed,
        elapsed_ms=elapsed,
        detail=detail,
    )


__all__ = [
    "THS_SRC",
    "NORMALIZED_COLUMNS",
    "normalize_sector_fundflow",
    "build_sector_daily_rows",
    "sector_net_inflow_map",
    "fetch_sector_fundflow",
    "fetch_industry_code_map",
    "fetch_net_inflow_map_sync",
    "clear_net_inflow_cache",
    "collect_sector_fallback",
]
