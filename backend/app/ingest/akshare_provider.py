"""
AKShare 主数据源实现（方案 5：AKShare 主采集 + Baostock 兜底校验）
====================================================================
合规边界（写进代码，避免后来者"顺手"加反爬）：

    1. AKShare 本质是对公开页面的二次封装，不是交易所授权接口；
    2. 必须限频：全市场快照 ≤ 1 次/分钟、历史回补 ≤ 2 请求/秒（由
       ``service.py`` 的 ``SNAPSHOT_LIMITER`` / ``HISTORY_LIMITER`` 统一节流）；
    3. 固定 UA（``config.HTTP_USER_AGENT``），**不实现任何反爬绕过**（无代理池/验证码）；
    4. 数据仅个人研究，不得对外分发或商用。

列名兼容策略（AKShare 接口文档与实际返回经常不一致）：

    所有取值都走 :func:`_pick`（候选列名逐个试）→ 缺列返回 None，**绝不 KeyError**。
    原始数值统一经 :func:`_num` 转 float（"--"、""、NaN 都变 None），不会把脏字符串写进库。

单位口径（入库前在这里一次性统一，硬约束 3）：

    - 成交量：东财/新浪的"手"→ **股**（×100）；
    - 成交额 / 市值 / 资金流：已是 **元**，不再换算；
    - 两融：上交所是元（/1e8 → 亿元）、深交所已是亿元；
    - 北向"当日成交净买额"：亿元。

akshare 未安装时本模块仍可 import（``ak = None``），``available()`` 返回 False，
方便 CI 与离线开发。
"""
from __future__ import annotations

import math
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

try:  # pragma: no cover —— 依赖是否安装由运行环境决定
    import akshare as ak
except ImportError:  # pragma: no cover
    ak = None  # type: ignore[assignment]

from app.core import config
from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.ingest.base import BaseProvider, FetchResult
from app.ingest.em_board import CONCEPT_ALIASES

# 东财接口 TLS 指纹补丁：必须在调用任何 akshare 东财接口之前生效（见 em_tls_patch.py）
from app.ingest import em_tls_patch  # noqa: F401  (import 即生效)

logger = get_logger(__name__)

# ---- 单位换算 ----
# 东财/新浪个股行情的成交量单位是"手"，1 手 = 100 股（A 股）
SHARES_PER_LOT = 100
# 新浪指数日线 volume 单位是**股**（实测 2026-10-21 上证指数 volume=37,828,638,900，
# 与 baostock 同日"股"口径完全一致；若按"手"则等于 3.78 万亿股，不可能）。
# 所以这里**不做** ×100；保留开关便于未来源口径变化时切换。
INDEX_VOLUME_IN_LOTS = False
# 上交所两融金额单位是元，模型里统一存亿元
YI = 100_000_000.0

# ---- 列名候选（顺序重要：越靠前越精确）----
COL_CODE = ("代码", "证券代码", "股票代码", "code", "symbol")
COL_NAME = ("名称", "证券简称", "股票简称", "name")
COL_DATE = ("日期", "trade_date", "date", "时间")
COL_OPEN = ("开盘", "今开", "open")
COL_CLOSE = ("收盘", "最新价", "现价", "close")
COL_HIGH = ("最高", "high")
COL_LOW = ("最低", "low")
COL_PRE_CLOSE = ("昨收", "昨收盘", "前收盘", "pre_close")
COL_PCT = ("涨跌幅", "涨跌幅(%)", "pct_chg", "pctChg")
COL_AMPLITUDE = ("振幅", "振幅(%)", "amplitude")
COL_VOLUME = ("成交量", "成交量(手)", "volume")
COL_AMOUNT = ("成交额", "成交额(元)", "amount")
COL_TURNOVER = ("换手率", "换手率(%)", "turnover_rate")
COL_PE = ("市盈率-动态", "市盈率(动态)", "市盈率", "pe_ttm")
COL_PB = ("市净率", "pb")
COL_PS = ("市销率", "ps_ttm")
COL_DV = ("股息率", "股息率(%)", "dv_ttm")
COL_MARKET_CAP = ("总市值", "market_cap")
COL_FLOAT_CAP = ("流通市值", "float_cap")

# 板块
SECTOR_CODE = ("板块代码", "代码", "sector_code", "板块编号")
SECTOR_NAME = ("板块名称", "名称", "sector_name")
SECTOR_UP_COUNT = ("上涨家数", "up_count")
SECTOR_DOWN_COUNT = ("下跌家数", "down_count")
SECTOR_LEADER = ("领涨股票", "leading_stock")
SECTOR_LEADER_PCT = ("领涨股票-涨跌幅", "领涨股票涨跌幅", "leading_stock_pct")

# 资金流
MF_MAIN_T0 = ("今日主力净流入-净额", "主力净流入-净额", "主力净流入", "main_net_t0")
MF_MAIN_5D = ("5日主力净流入-净额", "5日主力净流入", "main_net_5d")

# 东财 clist 直连参数（个股资金流分页抓取用）
_EM_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
_EM_UT = "b2884a393a59ad64002292a3e90d46a5"
# 沪深京 A 股（不含 B 股）：深主板/创业板/沪主板/科创板/北交所。
# 北交所必须显式加入（m:0+t:81+s:2048），否则日更资金流永远缺 ~360 只。
_EM_MF_FS = ("m:0+t:6+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,"
             "m:1+t:23+f:!2,m:0+t:81+s:2048+f:!2")
_EM_MF_SPEC = {
    # indicator -> (fid, fields, 源字段, 归一化列名)
    "今日": ("f62", "f12,f14,f62", "f62", "今日主力净流入-净额"),
    "5日": ("f164", "f12,f14,f164", "f164", "5日主力净流入-净额"),
}
# 当日页缓存：provider 级重试时复用已成功页，只重抓失败页
# （key=(indicator, 日期, 页码)，只缓存"整页且与 total 自洽"的页）
_EM_MF_PAGE_CACHE: dict[tuple[str, str, int], list[dict]] = {}
# 总行数缓存 key=(indicator, 日期)：缓存命中时必须同时拿到 total，
# 否则下一次调用只在第 1 页就结束、只写回 ~100 行（2026-11 残页事故根因）
_EM_MF_TOTAL_CACHE: dict[tuple[str, str], int] = {}
_EM_MF_PAGE_SIZE = 100
# 全市场资金流允许的缺行容差（榜单在翻页期间会有少量增删，不能因此判失败）
_EM_MF_TOLERANCE_RATIO = 0.005
_EM_MF_MIN_TOLERANCE = 5
# 板块成分分页：东财 clist 的 pz 上限为 100；成分残缺会误关 SCD2 区间，
# 因此零容差——短页一律重试，重试后仍不完整则整板失败（不改动已有区间）。
_EM_BOARD_CONS_PAGE_SIZE = 100
_EM_BOARD_CONS_RETRIES = 4


def _prune_mf_cache(date_key: str) -> None:
    """丢弃非当日缓存，避免常驻进程按天累积（每天约 1 万个 dict）。"""
    for cache in (_EM_MF_PAGE_CACHE, _EM_MF_TOTAL_CACHE):
        stale = [key for key in cache if key[1] != date_key]
        for key in stale:
            cache.pop(key, None)


def _parse_cn_amount(value: Any) -> float | None:
    """解析中文数量字符串（"16.74亿" / "-810.40万" / "1234"）→ 元；无效返回 None。"""
    if value is None:
        return None
    s = str(value).strip().replace(",", "")
    if s in ("", "-", "--"):
        return None
    mult = 1.0
    if s.endswith("亿"):
        mult, s = 1e8, s[:-1]
    elif s.endswith("万"):
        mult, s = 1e4, s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None

# 两融
MARGIN_DATE = ("信用交易日期", "交易日期", "日期", "date")
MARGIN_BALANCE = ("融资余额", "融资余额(元)", "融资余额(亿元)")
SHORT_BALANCE = ("融券余量金额", "融券余额", "融券余额(元)", "融券总额")
NORTH_DATE = ("日期", "date")
NORTH_NET = ("当日成交净买额", "当日净买额", "北向资金净流入", "northward_net")
# 两融 T+1 披露；最新已披露日距请求日超过该天数视为"未披露/源异常"，不得落库。
# 15 天覆盖春节/国庆长假，同时挡住 akshare 无参调用返回多年前历史数据的坑。
MARGIN_MAX_PUBLISHED_LAG_DAYS = 15

# 股票池补充
DELIST_CODE = ("公司代码", "证券代码", "股票代码", "代码")
DELIST_NAME = ("公司简称", "证券简称", "股票简称", "名称")
DELIST_DATE = ("终止上市日期", "退市日期", "终止上市日", "暂停上市日期")
LIST_DATE = ("上市日期", "上市时间", "上市日")


# ============================================================
# 基础取值工具（列名兼容 + 数值清洗）
# ============================================================
_EMPTY_STRINGS = {"", "-", "--", "—", "nan", "none", "null", "NaN", "None", "NoneType"}


def _pick(row: Any, candidates: Sequence[str], default: Any = None) -> Any:
    """从一行数据里按候选列名依次尝试取值，全部缺失返回 ``default``。

    Args:
        row: pandas Series / dict。
        candidates: 候选列名（按优先级）。
        default: 都没取到时的返回值。

    为什么需要它：AKShare 不同版本/不同接口的列名不稳定（"涨跌幅" vs "涨跌幅(%)"，
    "最新价" vs "收盘价"），硬编码 df["列名"] 一旦改版就 KeyError 崩全链路。
    优先级写死在调用处，缺列给 None，保证采集"能少不能崩"。
    """
    for column in candidates:
        try:
            value = row[column]  # type: ignore[index]
        except (KeyError, IndexError, TypeError):
            continue
        if value is None:
            continue
        if isinstance(value, float) and math.isnan(value):
            continue
        if isinstance(value, str) and value.strip() in _EMPTY_STRINGS:
            continue
        return value
    return default


def _num(value: Any) -> float | None:
    """把任意值转成 float；无法转换/NaN/Inf 返回 None。"""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("%", "")
        if text in _EMPTY_STRINGS:
            return None
        if text.endswith("亿"):
            try:
                return float(text[:-1]) * YI
            except ValueError:
                return None
        if text.endswith("万"):
            try:
                return float(text[:-1]) * 10_000.0
            except ValueError:
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


def _to_int(value: Any) -> int | None:
    number = _num(value)
    if number is None:
        return None
    try:
        return int(round(number))
    except (OverflowError, ValueError):
        return None


def _to_date(value: Any) -> date | None:
    """把日期字符串/Timestamp/datetime 统一成 ``datetime.date``。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, pd.Timestamp):
        return value.date()
    text = str(value).strip()
    if not text or text in _EMPTY_STRINGS:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        parsed = pd.to_datetime(text, errors="coerce")
        if parsed is not pd.NaT and not pd.isna(parsed):
            return parsed.date()
    except (TypeError, ValueError):
        pass
    return None


def _stock_code(value: Any) -> str | None:
    """统一 6 位代码（去空格、补零、剔除非数字）。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in _EMPTY_STRINGS:
        return None
    # 处理 "SH600519" / "600519.SH" 这类带后缀格式
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    return digits.zfill(6)[-6:]


def _today_cn() -> date:
    """A 股所在的"今天"（Asia/Shanghai）。"""
    try:
        return datetime.now(ZoneInfo(config.TIMEZONE)).date()
    except Exception:  # pragma: no cover —— Windows 缺 tzdata 时兜底固定 +8
        return (datetime.now(timezone.utc) + pd.Timedelta(hours=8)).date()


def _empty_frame(columns: Sequence[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=list(columns))


# ============================================================
# 归一化：纯函数（不联网，单测可直接调用）
# ============================================================
SPOT_COLUMNS: tuple[str, ...] = (
    "code",
    "name",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "pct_chg",
    "amplitude",
    "volume",
    "amount",
    "turnover_rate",
    "market_cap",
    "float_cap",
    "pe_ttm",
    "pb",
    "ps_ttm",
    "dv_ttm",
)

HISTORY_COLUMNS: tuple[str, ...] = (
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "pct_chg",
    "amplitude",
    "volume",
    "amount",
    "turnover_rate",
    "adj_factor",
)


def _normalize_spot(
    df: pd.DataFrame | None, *, volume_in_lots: bool = True
) -> pd.DataFrame:
    """全市场快照归一化。

    数据源两套口径：
        - 东财 ``stock_zh_a_spot_em``：成交量单位**手** → ×100 转股；
        - 新浪 ``stock_zh_a_spot``（东财不可用时的备用源）：成交量单位**股**，
          传 ``volume_in_lots=False`` 不再换算（实测 amount/(volume*price)≈1 验证）。
    最新价即当日收盘价（盘后采集；盘中采集的值也按"最新价"落库，由 ``src`` 追溯）。
    """
    if df is None or df.empty:
        return _empty_frame(SPOT_COLUMNS)

    records: list[dict[str, Any]] = []
    for row in df.to_dict("records"):
        code = _stock_code(_pick(row, COL_CODE))
        if not code:
            continue
        close = _num(_pick(row, COL_CLOSE))
        pre_close = _num(_pick(row, COL_PRE_CLOSE))
        pct = _num(_pick(row, COL_PCT))
        if pct is None and close is not None and pre_close:
            pct = round((close / pre_close - 1) * 100, 3)
        volume_raw = _num(_pick(row, COL_VOLUME))
        records.append(
            {
                "code": code,
                "name": str(_pick(row, COL_NAME) or "").strip(),
                "open": _num(_pick(row, COL_OPEN)),
                "high": _num(_pick(row, COL_HIGH)),
                "low": _num(_pick(row, COL_LOW)),
                "close": close,
                "pre_close": pre_close,
                "pct_chg": pct,
                "amplitude": _num(_pick(row, COL_AMPLITUDE)),
                # 东财是"手"（×100 转股），新浪是"股"（原样）
                "volume": (
                    _to_int(volume_raw * SHARES_PER_LOT)
                    if volume_raw is not None and volume_in_lots
                    else _to_int(volume_raw)
                ),
                "amount": _num(_pick(row, COL_AMOUNT)),
                "turnover_rate": _num(_pick(row, COL_TURNOVER)),
                "market_cap": _num(_pick(row, COL_MARKET_CAP)),
                "float_cap": _num(_pick(row, COL_FLOAT_CAP)),
                "pe_ttm": _num(_pick(row, COL_PE)),
                "pb": _num(_pick(row, COL_PB)),
                "ps_ttm": _num(_pick(row, COL_PS)),
                "dv_ttm": _num(_pick(row, COL_DV)),
            }
        )
    return pd.DataFrame(records, columns=list(SPOT_COLUMNS))


def _normalize_stock_history(
    raw_df: pd.DataFrame | None,
    hfq_df: pd.DataFrame | None,
) -> pd.DataFrame:
    """个股历史日线归一化（不复权 + 后复权两表合并）。

    复权因子算法（回测正确性的核心）::

        adj_factor = 后复权收盘 / 不复权收盘

    用"收盘价比值"而不是"涨跌幅累乘"：前者是交易所口径的精确因子，
    后者在除权日会有四舍五入误差。原始收盘价为空/<=0 的日期（停牌、脏数据）跳过。

    成交量单位：东财历史行情 ``成交量`` 单位是**手**，×100 转股（硬约束 3）。
    """
    if raw_df is None or getattr(raw_df, "empty", True):
        return _empty_frame(HISTORY_COLUMNS)

    # 后复权收盘价映射：date -> hfq close
    hfq_map: dict[date, float] = {}
    if hfq_df is not None and not getattr(hfq_df, "empty", True):
        for row in hfq_df.to_dict("records"):
            trade_date = _to_date(_pick(row, COL_DATE))
            hfq_close = _num(_pick(row, COL_CLOSE))
            if trade_date is not None and hfq_close is not None:
                hfq_map[trade_date] = hfq_close

    records: list[dict[str, Any]] = []
    prev_close: float | None = None
    for row in raw_df.to_dict("records"):
        trade_date = _to_date(_pick(row, COL_DATE))
        if trade_date is None:
            continue
        close = _num(_pick(row, COL_CLOSE))
        if close is None or close <= 0:
            # 原始收盘价无效：该日数据不可信，直接跳过（不写脏行）
            logger.debug("跳过无效原始收盘价的交易日: %s close=%r", trade_date, close)
            continue

        high = _num(_pick(row, COL_HIGH))
        low = _num(_pick(row, COL_LOW))
        open_ = _num(_pick(row, COL_OPEN))
        pct = _num(_pick(row, COL_PCT))
        pre_close = _num(_pick(row, COL_PRE_CLOSE))
        if pre_close is None:
            if pct is not None and pct <= -100:
                pre_close = None
            elif pct is not None:
                pre_close = round(close / (1 + pct / 100.0), 3)
            else:
                pre_close = prev_close
        if pct is None and pre_close:
            pct = round((close / pre_close - 1) * 100, 3)
        amplitude = _num(_pick(row, COL_AMPLITUDE))
        if amplitude is None and high is not None and low is not None and pre_close:
            amplitude = round((high - low) / pre_close * 100, 3)
        volume_lots = _num(_pick(row, COL_VOLUME))
        hfq_close = hfq_map.get(trade_date)
        adj_factor = (
            round(hfq_close / close, 6) if hfq_close is not None and close > 0 else None
        )

        records.append(
            {
                "trade_date": trade_date,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "pre_close": pre_close,
                "pct_chg": pct,
                "amplitude": amplitude,
                # 手 → 股
                "volume": _to_int(volume_lots * SHARES_PER_LOT)
                if volume_lots is not None
                else None,
                # 东财"成交额"单位已是元
                "amount": _num(_pick(row, COL_AMOUNT)),
                "turnover_rate": _num(_pick(row, COL_TURNOVER)),
                "adj_factor": adj_factor,
            }
        )
        prev_close = close

    if not records:
        return _empty_frame(HISTORY_COLUMNS)
    frame = pd.DataFrame(records, columns=list(HISTORY_COLUMNS))
    frame = frame.sort_values("trade_date").reset_index(drop=True)
    return frame


def _normalize_sector_list(
    industry_df: pd.DataFrame | None,
    concept_df: pd.DataFrame | None,
) -> pd.DataFrame:
    """板块列表（行业 + 概念）归一化，同时用于**当日板块日线快照**。

    东财板块列表自带：最新价/涨跌幅/换手率/上涨家数/下跌家数/领涨股票 ——
    正好可以拼出当日的 ``fact_sector_daily`` 一行，不需要逐个板块再拉历史。
    """
    columns = (
        "sector_code",
        "sector_name",
        "sector_type",
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "pct_chg",
        "amplitude",
        "volume",
        "amount",
        "turnover_rate",
        "up_count",
        "down_count",
        "leading_stock",
        "leading_stock_pct",
    )
    records: list[dict[str, Any]] = []
    for frame, sector_type in ((industry_df, "industry"), (concept_df, "concept")):
        if frame is None or getattr(frame, "empty", True):
            continue
        for row in frame.to_dict("records"):
            sector_code = _pick(row, SECTOR_CODE)
            sector_name = _pick(row, SECTOR_NAME)
            if not sector_code or not sector_name:
                continue
            close = _num(_pick(row, COL_CLOSE))
            pct = _num(_pick(row, COL_PCT))
            pre_close = (
                round(close / (1 + pct / 100.0), 3)
                if close is not None and pct is not None and pct > -100
                else None
            )
            records.append(
                {
                    "sector_code": str(sector_code).strip(),
                    "sector_name": str(sector_name).strip(),
                    "sector_type": sector_type,
                    "open": _num(_pick(row, COL_OPEN)),
                    "high": _num(_pick(row, COL_HIGH)),
                    "low": _num(_pick(row, COL_LOW)),
                    "close": close,
                    "pre_close": pre_close,
                    "pct_chg": pct,
                    "amplitude": _num(_pick(row, COL_AMPLITUDE)),
                    "volume": None,  # 板块列表没有成交量，不能拿"总市值"冒充（历史 bug #18）
                    "amount": None,  # 同上，amount 必须真的是成交额
                    "turnover_rate": _num(_pick(row, COL_TURNOVER)),
                    "up_count": _to_int(_pick(row, SECTOR_UP_COUNT)),
                    "down_count": _to_int(_pick(row, SECTOR_DOWN_COUNT)),
                    "leading_stock": _pick(row, SECTOR_LEADER),
                    "leading_stock_pct": _num(_pick(row, SECTOR_LEADER_PCT)),
                }
            )
    return pd.DataFrame(records, columns=list(columns))


def _normalize_sector_hist(
    df: pd.DataFrame | None,
    sector_code: str,
    sector_name: str,
    sector_type: str,
    trade_date: date,
) -> dict[str, Any] | None:
    """单个板块历史日线归一化，只取 ``trade_date`` 当天一行。"""
    if df is None or getattr(df, "empty", True):
        return None
    for row in df.to_dict("records"):
        row_date = _to_date(_pick(row, COL_DATE))
        if row_date != trade_date:
            continue
        close = _num(_pick(row, COL_CLOSE))
        volume_lots = _num(_pick(row, COL_VOLUME))
        return {
            "sector_code": sector_code,
            "sector_name": sector_name,
            "sector_type": sector_type,
            "open": _num(_pick(row, COL_OPEN)),
            "high": _num(_pick(row, COL_HIGH)),
            "low": _num(_pick(row, COL_LOW)),
            "close": close,
            "pre_close": _num(_pick(row, COL_PRE_CLOSE)),
            "pct_chg": _num(_pick(row, COL_PCT)),
            "amplitude": _num(_pick(row, COL_AMPLITUDE)),
            # 板块历史行情的成交量同样按"手"处理（新浪/东财口径）
            "volume": _to_int(volume_lots * SHARES_PER_LOT)
            if volume_lots is not None
            else None,
            "amount": _num(_pick(row, COL_AMOUNT)),
            "turnover_rate": _num(_pick(row, COL_TURNOVER)),
            "up_count": None,
            "down_count": None,
            "leading_stock": None,
            "leading_stock_pct": None,
        }
    return None


SECTOR_MEMBER_COLUMNS = ("sector_code", "sector_name", "sector_type", "stock_code", "stock_name")


# 东财板块清单缓存：{"industry"|"concept": (缓存日期, 精确名→BK代码, 归一化名→BK代码)}
_EM_BOARD_CODE_CACHE: dict[str, tuple[date, dict[str, str], dict[str, str]]] = {}


def normalize_board_name(name: str) -> str:
    """板块名归一化：去括号注释/空格/罗马数字后缀/通用尾缀，再忽略大小写。

    THS 与东财的板块命名并不一致（如 ``MR(混合现实)`` vs ``MR``、
    ``证券Ⅱ`` vs ``证券``），精确匹配会漏掉大量实际存在的板块。
    """
    text = str(name or "").strip()
    text = re.sub(r"[（(].*?[）)]", "", text).replace(" ", "").replace("　", "")
    text = re.sub(r"[ⅡⅢⅣ]$", "", text)
    for suffix in ("概念", "行业", "板块", "指数"):
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)]
            break
    return text.upper()


def build_board_code_maps(
    frame: pd.DataFrame | None,
) -> tuple[dict[str, str], dict[str, str]]:
    """东财板块清单 → (精确名→BK代码, 归一化名→BK代码)。

    归一化后出现一对多歧义时直接剔除该键，避免把不同板块的成分串到一起。
    """
    exact: dict[str, str] = {}
    normalized: dict[str, str] = {}
    ambiguous: set[str] = set()
    if frame is None or frame.empty:
        return exact, normalized
    if "板块名称" not in frame.columns or "板块代码" not in frame.columns:
        return exact, normalized
    for raw_name, raw_code in zip(frame["板块名称"], frame["板块代码"]):
        name = str(raw_name or "").strip()
        code = str(raw_code or "").strip()
        if not name or not code:
            continue
        exact[name] = code
        key = normalize_board_name(name)
        if key in normalized and normalized[key] != code:
            ambiguous.add(key)
        else:
            normalized[key] = code
    for key in ambiguous:
        normalized.pop(key, None)
    return exact, normalized


def resolve_em_board_code(
    exact: dict[str, str],
    normalized: dict[str, str],
    sector_name: str,
    sector_type: str,
) -> str | None:
    """THS 板块名 → 东财 BK 代码：精确 → 归一化 → 概念同义词。

    同义词只对概念板块生效，且只收录语义相同的改名/缩写（见
    ``em_board.CONCEPT_ALIASES``）；解析不到返回 None，由调用方跳过。
    """
    code = exact.get(sector_name) or normalized.get(normalize_board_name(sector_name))
    if code or sector_type != "concept":
        return code
    for alias in CONCEPT_ALIASES.get(sector_name, []):
        code = exact.get(alias) or normalized.get(normalize_board_name(alias))
        if code:
            return code
    return None


def _normalize_sector_members(
    df: pd.DataFrame | None,
    sector_code: str,
    sector_name: str,
    sector_type: str,
) -> pd.DataFrame:
    """板块成分股归一化（代码 + 名称）。"""
    if df is None or getattr(df, "empty", True):
        return _empty_frame(SECTOR_MEMBER_COLUMNS)
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in df.to_dict("records"):
        code = _stock_code(_pick(row, COL_CODE))
        if not code or code in seen:
            continue
        seen.add(code)
        records.append(
            {
                "sector_code": sector_code,
                "sector_name": sector_name,
                "sector_type": sector_type,
                "stock_code": code,
                "stock_name": str(_pick(row, COL_NAME) or "").strip() or None,
            }
        )
    return pd.DataFrame(records, columns=list(SECTOR_MEMBER_COLUMNS))


MONEYFLOW_COLUMNS = ("code", "main_net_t0", "main_net_5d")


def _normalize_moneyflow(
    today_df: pd.DataFrame | None,
    five_day_df: pd.DataFrame | None,
) -> pd.DataFrame:
    """个股资金流归一化：今日主力净流入 + 5 日主力净流入（单位：元）。"""
    records: dict[str, dict[str, Any]] = {}

    def _absorb(frame: pd.DataFrame | None, column_candidates: Sequence[str],
                target: str) -> None:
        if frame is None or getattr(frame, "empty", True):
            return
        for row in frame.to_dict("records"):
            code = _stock_code(_pick(row, COL_CODE))
            if not code:
                continue
            value = _num(_pick(row, column_candidates))
            item = records.setdefault(code, {"code": code, "main_net_t0": None,
                                             "main_net_5d": None})
            if value is not None:
                item[target] = value

    _absorb(today_df, MF_MAIN_T0, "main_net_t0")
    _absorb(five_day_df, MF_MAIN_5D, "main_net_5d")
    if not records:
        return _empty_frame(MONEYFLOW_COLUMNS)
    return pd.DataFrame(list(records.values()), columns=list(MONEYFLOW_COLUMNS))


def _margin_map_sse(
    df: pd.DataFrame | None, default_date: date | None = None
) -> dict[date, dict[str, float | None]]:
    """上交所两融：金额单位**元** → 亿元。

    SSE 返回带 ``信用交易日期`` 列；``default_date`` 仅在源缺日期列时兜底。
    """
    result: dict[date, dict[str, float | None]] = {}
    if df is None or getattr(df, "empty", True):
        return result
    for row in df.to_dict("records"):
        trade_date = _to_date(_pick(row, MARGIN_DATE)) or default_date
        if trade_date is None:
            continue
        margin = _num(_pick(row, MARGIN_BALANCE))
        short = _num(_pick(row, SHORT_BALANCE))
        result[trade_date] = {
            "margin_balance": round(margin / YI, 4) if margin is not None else None,
            "short_balance": round(short / YI, 4) if short is not None else None,
        }
    return result


def _margin_map_szse(
    df: pd.DataFrame | None, default_date: date | None = None
) -> dict[date, dict[str, float | None]]:
    """深交所两融：金额单位**已是亿元**，不换算。

    深交所 ``stock_margin_szse(date=...)`` 只返回**单日一行且没有日期列**，
    因此必须用调用方传入的 ``default_date`` 补齐。
    """
    result: dict[date, dict[str, float | None]] = {}
    if df is None or getattr(df, "empty", True):
        return result
    for row in df.to_dict("records"):
        trade_date = _to_date(_pick(row, MARGIN_DATE)) or default_date
        if trade_date is None:
            continue
        result[trade_date] = {
            "margin_balance": _num(_pick(row, MARGIN_BALANCE)),
            "short_balance": _num(_pick(row, SHORT_BALANCE)),
        }
    return result


def _margin_effective_date(
    sse_map: dict[date, dict[str, float | None]],
    requested_date: date,
    max_lag_days: int = MARGIN_MAX_PUBLISHED_LAG_DAYS,
) -> date | None:
    """区间内已披露的最新两融日期；无数据或过旧（超过 ``max_lag_days``）返回 None。

    过旧判定不可省略：akshare 的 ``stock_margin_sse()`` 无参调用默认区间截止
    2023-09-22，网络抖动触发兜底时会把三年前的数据当成"最新已披露日"。
    """
    published = [day for day in sse_map if day <= requested_date]
    if not published:
        return None
    latest = max(published)
    if (requested_date - latest).days > max_lag_days:
        return None
    return latest


def _normalize_north(df: pd.DataFrame | None) -> dict[date, float | None]:
    """北向资金：``当日成交净买额`` 单位亿元；可能长期为空（2024-08 后披露口径变化），
    属于预期内，**任何情况下不得报错阻断采集**。"""
    result: dict[date, float | None] = {}
    if df is None or getattr(df, "empty", True):
        return result
    for row in df.to_dict("records"):
        trade_date = _to_date(_pick(row, NORTH_DATE))
        if trade_date is None:
            continue
        result[trade_date] = _num(_pick(row, NORTH_NET))
    return result


INDEX_COLUMNS = (
    "index_code",
    "index_name",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "pct_chg",
    "volume",
    "amount",
)


def _normalize_index(
    df: pd.DataFrame | None,
    index_code: str,
    index_name: str,
) -> pd.DataFrame:
    """指数日线归一化（新浪 ``stock_zh_index_daily``）。

    新浪返回全历史，这里先按日期升序算 ``pre_close``（前一行收盘），再由编排层
    过滤目标交易日 —— 只抓一天也必须保留前一行才能算涨跌幅。
    """
    if df is None or getattr(df, "empty", True):
        return _empty_frame(INDEX_COLUMNS)
    records: list[dict[str, Any]] = []
    for row in df.to_dict("records"):
        trade_date = _to_date(_pick(row, COL_DATE))
        if trade_date is None:
            continue
        volume_raw = _num(_pick(row, COL_VOLUME))
        records.append(
            {
                "index_code": index_code,
                "index_name": index_name,
                "trade_date": trade_date,
                "open": _num(_pick(row, COL_OPEN)),
                "high": _num(_pick(row, COL_HIGH)),
                "low": _num(_pick(row, COL_LOW)),
                "close": _num(_pick(row, COL_CLOSE)),
                "volume": _to_int(volume_raw * SHARES_PER_LOT)
                if volume_raw is not None and INDEX_VOLUME_IN_LOTS
                else _to_int(volume_raw),
                "amount": _num(_pick(row, COL_AMOUNT)),
            }
        )
    if not records:
        return _empty_frame(INDEX_COLUMNS)
    frame = pd.DataFrame(records, columns=list(INDEX_COLUMNS))
    frame = frame.sort_values("trade_date").reset_index(drop=True)
    frame["pre_close"] = frame["close"].shift(1)
    close_series = pd.to_numeric(frame["close"], errors="coerce")
    pre_close_series = pd.to_numeric(frame["pre_close"], errors="coerce")
    valid = (
        close_series.notna()
        & pre_close_series.notna()
        & (pre_close_series > 0)
    )
    pct_series = (close_series / pre_close_series - 1) * 100
    frame["pct_chg"] = pct_series.where(valid).round(3)
    return frame[list(INDEX_COLUMNS)]


def _normalize_us_index(
    df: pd.DataFrame | None,
    index_code: str,
    index_name: str,
    trade_date: date,
) -> dict[str, Any] | None:
    """美股指数（yfinance）归一化：只取 ``trade_date`` 当天一行。

    美股日期按交易所本地日（yfinance 返回 tz-aware DatetimeIndex，这里转本地日期）。
    成交量单位是**股**（美股没有"手"），不乘 100。
    """
    if df is None or getattr(df, "empty", True):
        return None
    try:
        frame = df.copy()
        index = pd.to_datetime(frame.index, errors="coerce")
        if getattr(index, "tz", None) is not None:
            index = index.tz_localize(None)
        frame["trade_date"] = index.date
    except Exception:  # noqa: BLE001 —— yfinance 结构不稳定，失败就跳过
        return None
    frame = frame.reset_index(drop=True)
    frame = frame.sort_values("trade_date")
    pre_close = None
    for row in frame.to_dict("records"):
        row_date = _to_date(_pick(row, ("trade_date", "Date", "日期")))
        close = _num(_pick(row, ("Close", "Adj Close", "收盘")))
        if row_date == trade_date:
            return {
                "index_code": index_code,
                "index_name": index_name,
                "trade_date": row_date,
                "open": _num(_pick(row, ("Open", "开盘"))),
                "high": _num(_pick(row, ("High", "最高"))),
                "low": _num(_pick(row, ("Low", "最低"))),
                "close": close,
                "pre_close": pre_close,
                "pct_chg": round((close / pre_close - 1) * 100, 3)
                if close is not None and pre_close
                else None,
                "volume": _to_int(_pick(row, ("Volume", "成交量"))),
                "amount": None,
            }
        if close is not None:
            pre_close = close
    return None


CALENDAR_COLUMNS = ("trade_date",)


def _normalize_calendar(df: pd.DataFrame | None) -> pd.DataFrame:
    """交易日历归一化：``ak.tool_trade_date_hist_sina()`` 的 trade_date 列。"""
    if df is None or getattr(df, "empty", True):
        return _empty_frame(CALENDAR_COLUMNS)
    dates: set[date] = set()
    for row in df.to_dict("records"):
        value = _pick(row, ("trade_date", "日期", "date"))
        parsed = _to_date(value)
        if parsed is not None:
            dates.add(parsed)
    return pd.DataFrame({"trade_date": sorted(dates)}, columns=list(CALENDAR_COLUMNS))


# ============================================================
# AKShare Provider
# ============================================================
class AkshareProvider(BaseProvider):
    """AKShare 主数据源（方案 5：主采集）。"""

    name = "akshare"

    def __init__(self) -> None:
        self._throttle_interval = 1.0 / max(config.HISTORY_REQUESTS_PER_SEC, 0.1)
        self._last_call_at = 0.0
        self._ua_installed = False

    # ---------- 可用性 / 合规 ----------
    def available(self) -> bool:
        """akshare 已安装且允许联网时为 True（CI 离线自动降级）。"""
        return ak is not None and bool(config.INGEST_NETWORK_ENABLED)

    def _install_user_agent(self) -> None:
        """固定 UA（合规要求）：best-effort 修改 requests 默认 UA。

        AKShare 内部大多用 ``requests.get`` 且不传 UA，requests 会回落到
        ``requests.sessions.default_headers()``。这里替换默认 header 工厂，
        让整个采集进程带 ``config.HTTP_USER_AGENT``。**不做任何反爬绕过**。
        失败（requests 版本差异）不影响采集，只是 UA 不生效。
        """
        if self._ua_installed:
            return
        self._ua_installed = True
        try:
            import requests
            import requests.sessions

            original = requests.utils.default_headers

            def _headers_with_ua() -> dict[str, str]:
                headers = dict(original())
                headers["User-Agent"] = config.HTTP_USER_AGENT
                return headers

            requests.utils.default_headers = _headers_with_ua  # type: ignore[assignment]
            requests.sessions.default_headers = _headers_with_ua  # type: ignore[attr-defined]
            logger.debug("已设置固定 User-Agent：%s", config.HTTP_USER_AGENT)
        except Exception:  # noqa: BLE001 —— UA 设置失败不影响主流程
            logger.debug("设置固定 User-Agent 失败（忽略）", exc_info=True)

    def _throttle(self, interval: float | None = None) -> None:
        """同步节流（Provider 内多接口循环之间使用；跨任务节流在编排层做）。"""
        wait = self._throttle_interval if interval is None else interval
        now = time.monotonic()
        sleep_for = self._last_call_at + wait - now
        if sleep_for > 0:
            time.sleep(sleep_for)
        self._last_call_at = time.monotonic()

    def _safe_call(self, func: Any, *args: Any, what: str = "", **kwargs: Any) -> Any:
        """统一包裹一次 AKShare 调用：异常 → ProviderError（上层重试/降级）。

        约定：``NotImplementedError`` 原样抛出（降级链据此判断"能力不支持"），
        其余一切异常（requests/JSON/KeyError/TypeError）都包装成 ProviderError，
        保证编排层只需要处理一种异常类型。
        """
        if ak is None:  # pragma: no cover —— available() 已拦截，这里防御
            raise ProviderError("AKShare 未安装", detail={"provider": "akshare"})
        if not config.INGEST_NETWORK_ENABLED:  # pragma: no cover
            raise ProviderError(
                "INGEST_NETWORK_ENABLED=0，禁止联网采集",
                detail={"provider": "akshare"},
            )

        self._install_user_agent()
        label = what or getattr(func, "__name__", "akshare_call")
        try:
            return func(*args, **kwargs)
        except NotImplementedError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 外部库异常类型不可控，统一收敛
            raise ProviderError(
                f"AKShare 调用失败[{label}]: {type(exc).__name__}: {exc}",
                detail={"provider": "akshare", "what": label,
                        "exception": type(exc).__name__},
            ) from exc

    def _fetch_one(self, frame: Any, what: str, **kwargs: Any) -> pd.DataFrame:
        """调用并校验返回类型是 DataFrame。"""
        result = self._safe_call(frame, what=what, **kwargs)
        if result is None:
            raise ProviderError(f"AKShare 返回空对象[{what}]", detail={"provider": "akshare"})
        if not isinstance(result, pd.DataFrame):
            raise ProviderError(
                f"AKShare 返回类型异常[{what}]: {type(result).__name__}",
                detail={"provider": "akshare"},
            )
        return result

    # ---------- A7 交易日历 ----------
    def fetch_trade_calendar(self, start: date, end: date) -> FetchResult:
        started = time.perf_counter()
        raw = self._fetch_one(ak.tool_trade_date_hist_sina, "tool_trade_date_hist_sina")
        frame = _normalize_calendar(raw)
        if not frame.empty:
            frame = frame[
                (frame["trade_date"] >= start) & (frame["trade_date"] <= end)
            ].reset_index(drop=True)
        elapsed = int((time.perf_counter() - started) * 1000)
        return FetchResult(
            rows=len(frame),
            total=len(frame),
            elapsed_ms=elapsed,
            detail={"data": frame, "source": self.name, "range": [start.isoformat(),
                                                                   end.isoformat()]},
        )

    # ---------- A2 全市场快照 ----------
    def fetch_daily_snapshot(self, trade_date: date) -> FetchResult:
        """一次请求拿全市场快照。

        主源东财 ``stock_zh_a_spot_em``（字段最全：市值/PE/PB/换手）；
        失败时降级到新浪 ``stock_zh_a_spot``（只有 OHLCV，但东财接口被网络/风控
        拦截时仍能保证"全市场当日行情"这条主链不断）。
        """
        started = time.perf_counter()
        spot_source = "em"
        try:
            raw = self._fetch_one(ak.stock_zh_a_spot_em, "stock_zh_a_spot_em")
            frame = _normalize_spot(raw, volume_in_lots=True)
        except ProviderError as exc:
            logger.warning("东财全市场快照失败，降级新浪源：%s", exc)
            raw = self._fetch_one(ak.stock_zh_a_spot, "stock_zh_a_spot")
            frame = _normalize_spot(raw, volume_in_lots=False)
            spot_source = "sina"
        if frame.empty:
            raise ProviderError(
                "全市场快照归一化后为空（东财与新浪均无有效数据）",
                detail={"provider": self.name},
            )
        elapsed = int((time.perf_counter() - started) * 1000)
        logger.info("全市场快照 %s：%d 行（来源 %s）", trade_date, len(frame), spot_source)
        return FetchResult(
            rows=len(frame),
            total=len(frame),
            elapsed_ms=elapsed,
            detail={"data": frame, "source": self.name,
                    "spot_source": spot_source,
                    "trade_date": trade_date.isoformat()},
        )

    # ---------- A3 历史日线 ----------
    def fetch_stock_history(self, code: str, start: date, end: date) -> FetchResult:
        """不复权 + 后复权两次请求，合并出 adj_factor。

        两次请求之间 ``_throttle()`` 一次，满足"历史回补 ≤ 2 请求/秒"的合规要求。
        """
        started = time.perf_counter()
        code = _stock_code(code) or str(code)
        start_date = start.strftime("%Y%m%d")
        end_date = end.strftime("%Y%m%d")

        raw = self._fetch_one(
            ak.stock_zh_a_hist,
            "stock_zh_a_hist(raw)",
            symbol=code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="",
        )
        self._throttle()
        hfq = self._fetch_one(
            ak.stock_zh_a_hist,
            "stock_zh_a_hist(hfq)",
            symbol=code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="hfq",
        )
        frame = _normalize_stock_history(raw, hfq)
        elapsed = int((time.perf_counter() - started) * 1000)
        adj_missing = int(frame["adj_factor"].isna().sum()) if len(frame) else 0
        return FetchResult(
            rows=len(frame),
            total=len(frame),
            failed=adj_missing,
            elapsed_ms=elapsed,
            detail={
                "data": frame,
                "source": self.name,
                "code": code,
                "range": [start.isoformat(), end.isoformat()],
                "adj_factor_missing": adj_missing,
            },
        )

    # ---------- A1 股票池 ----------
    def fetch_stock_universe(self) -> FetchResult:
        """全市场标的：代码/名称/市值/行业/上市退市日期/股本。

        数据来源（按可靠性组合，任一环节失败都尽量降级而不是整单失败）：
            1. ``stock_info_a_code_name``   —— 代码 + 名称（基础清单；失败时用交易所名单兜底）；
            2. ``stock_info_sh_name_code`` / ``stock_info_sz_name_code``
               —— 上市日期 + 深市总股本/流通股本（实测列名：证券代码/上市日期；
               深市是 A股代码/A股上市日期/A股总股本/A股流通股本）；
            3. 全市场快照：东财 ``stock_zh_a_spot_em``（含市值/PE/PB），
               失败降级新浪 ``stock_zh_a_spot``（只有 OHLCV，且新浪快照含北交所代码）；
            4. ``stock_board_industry_name_em`` + 板块成分 —— 行业归属（失败不阻断）；
            5. ``stock_info_sh_delist`` / ``stock_info_sz_delist`` —— 退市股（失败不阻断）。
        """
        started = time.perf_counter()
        records: dict[str, dict[str, Any]] = {}
        base_source = "a_code_name"

        try:
            base = self._fetch_one(ak.stock_info_a_code_name, "stock_info_a_code_name")
            for row in base.to_dict("records"):
                code = _stock_code(_pick(row, COL_CODE))
                if not code:
                    continue
                records[code] = {
                    "code": code,
                    "name": str(_pick(row, COL_NAME) or "").strip(),
                }
        except ProviderError as exc:
            base_source = "exchange_lists"
            logger.warning(
                "基础清单 stock_info_a_code_name 失败，降级用交易所名单+行情快照：%s", exc
            )

        # 交易所名单：上市日期 + 深市股本（顺带可用于补基础清单缺失的标的）
        listing_rows = self._fetch_cn_stock_lists()
        listing_map = {row["code"]: row for row in listing_rows}
        if not records:
            for code, extra in listing_map.items():
                records[code] = {"code": code, **{
                    key: value for key, value in extra.items() if key != "code"
                }}
        else:
            for code, item in records.items():
                extra = listing_map.get(code)
                if not extra:
                    continue
                for key in ("list_date", "total_share", "float_share"):
                    if extra.get(key) is not None:
                        item[key] = extra[key]

        # 全市场快照：东财 → 新浪 两级降级
        spot_source: str | None = None
        try:
            try:
                spot_raw = self._fetch_one(ak.stock_zh_a_spot_em, "stock_zh_a_spot_em")
                spot = _normalize_spot(spot_raw, volume_in_lots=True)
                spot_source = "em"
            except ProviderError as exc:
                logger.warning("东财快照失败，股票池降级新浪快照：%s", exc)
                spot_raw = self._fetch_one(ak.stock_zh_a_spot, "stock_zh_a_spot")
                spot = _normalize_spot(spot_raw, volume_in_lots=False)
                spot_source = "sina"
            for row in spot.to_dict("records"):
                code = row["code"]
                item = records.setdefault(
                    code, {"code": code, "name": row.get("name") or ""}
                )
                for key in ("name", "close", "market_cap", "float_cap",
                            "turnover_rate", "pe_ttm", "pb"):
                    if row.get(key) is not None:
                        item[key] = row[key]
        except ProviderError as exc:
            logger.warning("股票池补市值/价格失败（降级继续）：%s", exc)

        if not records:
            raise ProviderError(
                "股票池为空：基础清单、交易所名单与行情快照均不可用",
                detail={"provider": self.name},
            )

        # 行业归属（东财板块；接口不可用时快速返回空，不阻断股票池）
        industry_map = self._fetch_industry_map()
        for code, item in records.items():
            item["industry_l1"] = industry_map.get(code)
            item.setdefault("delist_date", None)
            item.setdefault("list_date", listing_map.get(code, {}).get("list_date"))

        # 退市清单（代码/名称/退市日/上市日）
        delist_map = self._fetch_delisted_map()
        for code, info in delist_map.items():
            item = records.setdefault(code, {"code": code, "name": info.get("name") or ""})
            item["delist_date"] = info.get("delist_date")
            if info.get("name"):
                item["name"] = info["name"]
            if info.get("list_date") is not None and item.get("list_date") is None:
                item["list_date"] = info["list_date"]

        columns = [
            "code", "name", "close", "market_cap", "float_cap", "industry_l1",
            "turnover_rate", "pe_ttm", "pb", "list_date", "delist_date",
            "total_share", "float_share",
        ]
        frame = pd.DataFrame(list(records.values()), columns=columns)
        elapsed = int((time.perf_counter() - started) * 1000)
        logger.info(
            "股票池拉取完成：%d 只（基础源 %s，快照源 %s，行业归属 %d，退市 %d）",
            len(frame), base_source, spot_source, len(industry_map), len(delist_map),
        )
        return FetchResult(
            rows=len(frame),
            total=len(frame),
            elapsed_ms=elapsed,
            detail={
                "data": frame,
                "source": self.name,
                "base_source": base_source,
                "spot_source": spot_source,
                "industry_mapped": len(industry_map),
                "delisted": len(delist_map),
                "list_date_mapped": int(frame["list_date"].notna().sum())
                if "list_date" in frame.columns else 0,
            },
        )

    def _fetch_cn_stock_lists(self) -> list[dict[str, Any]]:
        """上交所/深交所股票名单：代码/名称/上市日期/股本（深市有股本，沪市无）。

        实测列名（akshare 1.19.1）：
            - 上交所：证券代码 / 证券简称 / 上市日期；
            - 深交所：A股代码 / A股简称 / A股上市日期 / A股总股本 / A股流通股本。
        """
        rows: list[dict[str, Any]] = []
        sources: list[tuple[Any, str, str]] = []
        sh_func = getattr(ak, "stock_info_sh_name_code", None)
        sz_func = getattr(ak, "stock_info_sz_name_code", None)
        if sh_func is not None:
            sources.append((sh_func, "stock_info_sh_name_code[主板A股]", "主板A股"))
            sources.append((sh_func, "stock_info_sh_name_code[科创板]", "科创板"))
        if sz_func is not None:
            sources.append((sz_func, "stock_info_sz_name_code[A股列表]", "A股列表"))

        for func, label, symbol in sources:
            self._throttle()
            try:
                frame = self._fetch_one(func, label, symbol=symbol)
            except ProviderError as exc:
                logger.warning("交易所名单获取失败[%s]：%s", label, exc)
                continue
            for row in frame.to_dict("records"):
                code = _stock_code(
                    _pick(row, ("A股代码", "证券代码", "公司代码", "股票代码", "代码"))
                )
                if not code:
                    continue
                rows.append(
                    {
                        "code": code,
                        "name": str(
                            _pick(row, ("证券简称", "A股简称", "公司简称", "名称")) or ""
                        ).strip(),
                        "list_date": _to_date(
                            _pick(row, ("A股上市日期", "上市日期", "上市时间", "上市日"))
                        ),
                        "total_share": _num(_pick(row, ("A股总股本", "总股本"))),
                        "float_share": _num(_pick(row, ("A股流通股本", "流通股本"))),
                    }
                )
        return rows

    def _fetch_industry_map(self) -> dict[str, str]:
        """行业板块 → 成分映射（code → 行业名）。失败返回已拿到的部分，不抛异常。"""
        mapping: dict[str, str] = {}
        try:
            boards = self._fetch_one(
                ak.stock_board_industry_name_em, "stock_board_industry_name_em"
            )
        except ProviderError as exc:
            logger.warning("行业板块列表获取失败，跳过行业归属：%s", exc)
            return mapping

        names = []
        for row in boards.to_dict("records"):
            name = _pick(row, SECTOR_NAME)
            if name:
                names.append(str(name).strip())

        interval = 1.0 / max(config.HISTORY_REQUESTS_PER_SEC, 0.1)
        for board_name in names:
            self._throttle(interval)
            try:
                cons = self._fetch_one(
                    ak.stock_board_industry_cons_em,
                    f"stock_board_industry_cons_em[{board_name}]",
                    symbol=board_name,  # 参数是板块**名称**，不是 BK 代码
                )
            except ProviderError as exc:
                logger.warning("行业成分获取失败[%s]：%s", board_name, exc)
                continue
            for row in cons.to_dict("records"):
                code = _stock_code(_pick(row, COL_CODE))
                if code:
                    mapping[code] = board_name
        return mapping

    def _fetch_delisted_map(self) -> dict[str, dict[str, Any]]:
        """退市股清单（代码 → 名称/终止上市日期/上市日期）。拿不到就空，绝不抛异常。"""
        result: dict[str, dict[str, Any]] = {}
        for func, label in (
            (getattr(ak, "stock_info_sh_delist", None), "stock_info_sh_delist"),
            (getattr(ak, "stock_info_sz_delist", None), "stock_info_sz_delist"),
        ):
            if func is None:
                continue
            try:
                frame = self._fetch_one(func, label)
            except ProviderError as exc:
                logger.warning("退市清单获取失败[%s]：%s", label, exc)
                continue
            for row in frame.to_dict("records"):
                code = _stock_code(_pick(row, DELIST_CODE))
                if not code:
                    continue
                result[code] = {
                    "name": _pick(row, DELIST_NAME),
                    "delist_date": _to_date(_pick(row, DELIST_DATE)),
                    "list_date": _to_date(_pick(row, LIST_DATE)),
                }
        return result

    def _fetch_list_dates(self) -> dict[str, date]:
        """上市日期（代码 → list_date），基于 :meth:`_fetch_cn_stock_lists`。"""
        return {
            row["code"]: row["list_date"]
            for row in self._fetch_cn_stock_lists()
            if row.get("list_date") is not None
        }

    # ---------- A4 板块 ----------
    def fetch_sector_list(self, trade_date: date) -> FetchResult:
        """板块列表 + 板块日线。

        - ``trade_date == 今天``：直接用东财板块快照（一次请求拿全，快）；
        - ``trade_date < 今天``：逐板块拉历史日线（``stock_board_industry_hist_em`` /
          ``stock_board_concept_hist_em``，约 2 req/s，~5 分钟），用于 A4 历史回补；
        - 未来日期：直接返回空。
        """
        started = time.perf_counter()
        today = _today_cn()
        if trade_date > today:
            return FetchResult(
                rows=0,
                total=0,
                elapsed_ms=0,
                detail={"data": _empty_frame(("sector_code",)), "source": self.name,
                        "empty_reason": "future_date"},
            )

        industry = self._fetch_one(
            ak.stock_board_industry_name_em, "stock_board_industry_name_em"
        )
        self._throttle()
        concept = self._fetch_one(
            ak.stock_board_concept_name_em, "stock_board_concept_name_em"
        )

        if trade_date == today:
            frame = _normalize_sector_list(industry, concept)
        else:
            frame = self._fetch_sector_history(industry, concept, trade_date)

        elapsed = int((time.perf_counter() - started) * 1000)
        logger.info("板块日线 %s：%d 行（%s）", trade_date, len(frame),
                    "快照" if trade_date == today else "历史回补")
        return FetchResult(
            rows=len(frame),
            total=len(frame),
            elapsed_ms=elapsed,
            detail={"data": frame, "source": self.name,
                    "trade_date": trade_date.isoformat(),
                    "mode": "snapshot" if trade_date == today else "history"},
        )

    def _fetch_sector_history(
        self,
        industry: pd.DataFrame,
        concept: pd.DataFrame,
        trade_date: date,
    ) -> pd.DataFrame:
        """逐板块拉历史单日行情（A4 历史回补路径）。失败的板块跳过并计数。"""
        columns = (
            "sector_code", "sector_name", "sector_type", "open", "high", "low", "close",
            "pre_close", "pct_chg", "amplitude", "volume", "amount", "turnover_rate",
            "up_count", "down_count", "leading_stock", "leading_stock_pct",
        )
        records: list[dict[str, Any]] = []
        failed = 0
        date_text = trade_date.strftime("%Y%m%d")

        for frame, sector_type, func_name, period in (
            # 注意：行业板块历史接口 period 用 "日k"，概念板块历史接口用 "daily"（akshare 1.19 实测签名）
            (industry, "industry", "stock_board_industry_hist_em", "日k"),
            (concept, "concept", "stock_board_concept_hist_em", "daily"),
        ):
            func = getattr(ak, func_name, None)
            if func is None or frame is None or frame.empty:
                continue
            for row in frame.to_dict("records"):
                sector_code = _pick(row, SECTOR_CODE)
                sector_name = _pick(row, SECTOR_NAME)
                if not sector_code or not sector_name:
                    continue
                self._throttle()
                try:
                    hist = self._fetch_one(
                        func,
                        f"{func_name}[{sector_name}]",
                        symbol=str(sector_name).strip(),  # 参数是板块名称
                        start_date=date_text,
                        end_date=date_text,
                        period=period,
                        adjust="",
                    )
                except ProviderError as exc:
                    failed += 1
                    logger.warning("板块历史获取失败[%s]：%s", sector_name, exc)
                    continue
                item = _normalize_sector_hist(
                    hist, str(sector_code).strip(), str(sector_name).strip(),
                    sector_type, trade_date,
                )
                if item is not None:
                    records.append(item)
        if failed:
            logger.warning("板块历史回补 %s：失败 %d 个板块", trade_date, failed)
        if not records:
            return _empty_frame(columns)
        return pd.DataFrame(records, columns=list(columns))

    def _em_board_code_maps(
        self, sector_type: str
    ) -> tuple[dict[str, str], dict[str, str]]:
        """东财板块名→BK 代码映射，按天缓存。

        akshare 的 cons 接口按名称调用时每次都会重拉整张板块清单再精确匹配，
        名称不存在时直接 ``IndexError``。预先解析成 BK 代码后：
        ① 每个板块只剩 1 次请求；② 未匹配的板块可以快速跳过而不是重试。
        清单刷新失败时沿用上一次成功的映射（BK 代码很稳定），避免单次
        DNS/网络抖动让全部板块在本轮都不可抓。
        """
        key = "industry" if sector_type == "industry" else "concept"
        cached = _EM_BOARD_CODE_CACHE.get(key)
        today = date.today()
        if cached is not None and cached[0] == today:
            return cached[1], cached[2]
        try:
            raw = self._em_board_list_df(key)
            exact, normalized = build_board_code_maps(raw)
            if not exact:
                raise ProviderError(f"东财{key}板块清单为空")
        except ProviderError:
            if cached is None:
                raise
            logger.warning(
                "东财 %s 板块清单刷新失败，沿用 %s 的缓存映射（%d 个板块）",
                key, cached[0], len(cached[1]),
            )
            return cached[1], cached[2]
        _EM_BOARD_CODE_CACHE[key] = (today, exact, normalized)
        return exact, normalized

    def _em_clist_page(
        self, fs: str, fields: str, page: int, rows_so_far: int, what: str
    ) -> tuple[list[dict], int]:
        """抓取东财 clist 的一页并校验完整性（残页重试同一页，绝不返回半份）。

        akshare 的板块接口每页固定 100 行、翻页间 sleep 0.5~1.5s，480 个板块
        一轮要 20 分钟以上，直接顶穿采集预算；这里直连 clist，按 ≤2 请求/秒
        自己翻页，并对每一页做 total 自洽校验。
        """
        from curl_cffi import requests as _cffi_req

        params = {
            "fid": "f12", "po": "1", "pz": str(_EM_BOARD_CONS_PAGE_SIZE),
            "pn": str(page), "np": "1", "fltt": "2", "invt": "2", "ut": _EM_UT,
            "fs": fs, "fields": fields,
        }
        last_error: Exception | None = None
        for attempt in range(_EM_BOARD_CONS_RETRIES):
            try:
                response = _cffi_req.get(
                    _EM_CLIST_URL, params=params, impersonate="chrome", timeout=15
                )
                response.raise_for_status()
                data = (response.json() or {}).get("data")
                if not isinstance(data, dict):
                    raise ProviderError("响应缺少 data（可能被风控）")
                if data.get("total") is None:
                    raise ProviderError("响应缺少 total")
                total = int(data["total"])
                diff = data.get("diff")
                if diff is None and total == 0:
                    diff = []
                if isinstance(diff, dict):
                    diff = list(diff.values())
                if not isinstance(diff, list):
                    raise ProviderError("响应缺少 diff")
                rows = [
                    item for item in diff
                    if isinstance(item, dict) and str(item.get("f12") or "").strip()
                ]
                if (
                    len(rows) < _EM_BOARD_CONS_PAGE_SIZE
                    and rows_so_far + len(rows) < total
                ):
                    raise ProviderError(
                        f"疑似截断：本页 {len(rows)} 行 < {_EM_BOARD_CONS_PAGE_SIZE}，"
                        f"累计 {rows_so_far + len(rows)}/{total}"
                    )
                return rows, total
            except Exception as exc:  # noqa: BLE001 —— 单页内自愈，不污染外层
                last_error = exc
                if attempt < _EM_BOARD_CONS_RETRIES - 1:
                    time.sleep(min(1.5 * (attempt + 1), 5.0))
        raise ProviderError(
            f"东财 {what} 第 {page} 页连续 {_EM_BOARD_CONS_RETRIES} 次失败或返回残页："
            f"{last_error}",
            detail={"provider": self.name, "what": what, "page": page},
        )

    def _em_clist_rows(self, fs: str, fields: str, what: str) -> list[dict]:
        """按 fs 条件抓取东财 clist 全部行（严格完整性，零容差）。"""
        rows: list[dict] = []
        seen: set[str] = set()
        total = 0
        page = 1
        while True:
            page_rows, total = self._em_clist_page(fs, fields, page, len(rows), what)
            for item in page_rows:
                code = str(item.get("f12") or "").strip()
                if code and code not in seen:
                    seen.add(code)
                    rows.append(item)
            if len(rows) >= total or not page_rows:
                break
            page += 1
            self._throttle()
        if len(rows) < total:
            # 去重后仍少于 total：榜单在翻页期间变动或分页不自洽。宁可整批失败，
            # 也不能把残缺名单交给 SCD2 去关闭区间。
            raise ProviderError(
                f"东财 {what} 不完整：total={total}，实际 {len(rows)}",
                detail={"provider": self.name, "what": what,
                        "total": total, "fetched": len(rows)},
            )
        return rows

    def _em_board_list_df(self, sector_type: str) -> pd.DataFrame:
        """直连东财抓板块清单（行业/概念），返回 板块名称/板块代码 两列。"""
        fs = "m:90 t:2 f:!50" if sector_type == "industry" else "m:90 t:3 f:!50"
        rows = self._em_clist_rows(fs, "f12,f14", f"{sector_type}_board_list")
        return pd.DataFrame(
            {
                "板块名称": [item.get("f14") for item in rows],
                "板块代码": [str(item.get("f12")) for item in rows],
            }
        )

    def _em_board_cons_df(self, em_code: str) -> pd.DataFrame:
        """直连东财抓取板块全部成分（严格完整性，零容差）。"""
        rows = self._em_clist_rows(
            f"b:{em_code} f:!50", "f12,f14", f"板块成分[{em_code}]"
        )
        return pd.DataFrame(
            {
                "代码": [str(item.get("f12")) for item in rows],
                "名称": [item.get("f14") for item in rows],
            }
        )

    def fetch_sector_members(
        self, sector_code: str, sector_name: str, sector_type: str
    ) -> FetchResult:
        """板块成分股。

        THS 板块名先按「精确 → 归一化 → 概念同义词」解析成东财 BK 代码，再用代码取成分；
        东财清单里没有的板块返回 ``skip_reason=em_name_not_found`` 的空结果，
        由编排层按"不支持的板块"跳过（不重试、不触发熔断、不动已有区间）。
        """
        started = time.perf_counter()
        exact, normalized = self._em_board_code_maps(sector_type)
        em_code = resolve_em_board_code(exact, normalized, sector_name, sector_type)
        if not em_code:
            elapsed = int((time.perf_counter() - started) * 1000)
            frame = _empty_frame(SECTOR_MEMBER_COLUMNS)
            return FetchResult(
                rows=0,
                total=0,
                elapsed_ms=elapsed,
                detail={
                    "data": frame,
                    "source": self.name,
                    "sector_code": sector_code,
                    "sector_name": sector_name,
                    "sector_type": sector_type,
                    "skip_reason": "em_name_not_found",
                },
            )
        raw = self._em_board_cons_df(em_code)
        frame = _normalize_sector_members(raw, sector_code, sector_name, sector_type)
        elapsed = int((time.perf_counter() - started) * 1000)
        return FetchResult(
            rows=len(frame),
            total=len(frame),
            elapsed_ms=elapsed,
            detail={"data": frame, "source": self.name,
                    "sector_code": sector_code, "sector_name": sector_name,
                    "sector_type": sector_type},
        )

    # ---------- A5 资金流与两融 ----------
    def _em_moneyflow_page(
        self, indicator: str, pn: int, page_size: int, rows_so_far: int,
        total: int | None,
    ) -> tuple[list[dict], int]:
        """抓取并校验东财资金流的一页（截断页重试同页，绝不返回残页）。

        东财在风控/链路抖动时会返回 HTTP 200 但 ``diff`` 被截短（少于 page_size
        且 total 仍更大），也可能 ``data`` 直接为 null。若把这种页当成功缓存，
        残页会在当日所有重试里永久生效，全市场资金流就永远缺行。
        """
        from curl_cffi import requests as _cffi_req

        fid, field_csv, _raw_field, _col_name = _EM_MF_SPEC[indicator]
        params = {"fid": fid, "po": "1", "pz": str(page_size), "pn": str(pn),
                  "np": "1", "fltt": "2", "invt": "2", "ut": _EM_UT,
                  "fs": _EM_MF_FS, "fields": field_csv}
        last_error: Exception | None = None
        for attempt in range(6):
            try:
                response = _cffi_req.get(
                    _EM_CLIST_URL, params=params, impersonate="chrome", timeout=15
                )
                response.raise_for_status()
                data = (response.json() or {}).get("data")
                if not isinstance(data, dict):
                    raise ProviderError("响应缺少 data（可能被风控）")
                diff = data.get("diff")
                if isinstance(diff, dict):
                    diff = list(diff.values())
                if not isinstance(diff, list):
                    raise ProviderError("响应缺少 diff")
                if data.get("total") is None:
                    raise ProviderError("响应缺少 total")
                page_total = int(data["total"])
                page_rows = [
                    item for item in diff
                    if isinstance(item, dict) and str(item.get("f12") or "").strip()
                ]
                if (
                    len(page_rows) < page_size
                    and rows_so_far + len(page_rows) < page_total
                ):
                    raise ProviderError(
                        f"疑似截断：本页 {len(page_rows)} 行 < {page_size}，"
                        f"累计 {rows_so_far + len(page_rows)}/{page_total}"
                    )
                return page_rows, page_total
            except Exception as exc:  # noqa: BLE001 —— 单页内自愈，不污染外层
                last_error = exc
                if attempt < 5:
                    time.sleep(min(2.0 * (attempt + 1), 8.0))
        raise ProviderError(
            f"东财资金流第 {pn} 页连续 6 次失败或返回残页：{last_error}",
            detail={"provider": self.name, "page": pn, "indicator": indicator},
        )

    def _em_moneyflow_df(self, indicator: str) -> tuple[pd.DataFrame, dict[str, Any]]:
        """直连东财 clist 分页抓个股资金流（每页独立重试 + 完整性校验）。

        为什么不用 ak.stock_individual_fund_flow_rank：该函数要连续翻 ~54 页，
        东财按连接**突发性**重置（实测坏窗口内 0.2s 即被掐断，坏窗口持续数秒~数分钟），
        任何一页失败则整个函数作废，外层重试又在同一风暴里翻页，几乎必败。
        这里改为：curl_cffi(chrome) TLS 指纹直连 + 每页独立重试 6 次。

        返回 ``(frame, stats)``；只有与 ``total`` 自洽（容差内）的结果才会返回并
        写入页缓存，否则抛 ``ProviderError`` 让降级链换源，绝不落半份数据。
        """
        fid, _field_csv, raw_field, col_name = _EM_MF_SPEC[indicator]
        date_key = time.strftime("%Y%m%d")
        _prune_mf_cache(date_key)
        total_key = (indicator, date_key)
        total = _EM_MF_TOTAL_CACHE.get(total_key)
        rows: list[dict] = []
        seen: set[str] = set()
        pages_hit = pages_fetched = 0
        pn = 1
        while True:
            cache_key = (indicator, date_key, pn)
            page_rows = _EM_MF_PAGE_CACHE.get(cache_key)
            if page_rows is None:
                if pn > 1:
                    time.sleep(0.3)
                page_rows, page_total = self._em_moneyflow_page(
                    indicator, pn, _EM_MF_PAGE_SIZE, len(rows), total
                )
                total = page_total if total is None else max(total, page_total)
                _EM_MF_PAGE_CACHE[cache_key] = page_rows
                _EM_MF_TOTAL_CACHE[total_key] = total
                pages_fetched += 1
            else:
                pages_hit += 1
            for item in page_rows:
                code = str(item.get("f12") or "").strip()
                if code and code not in seen:
                    seen.add(code)
                    rows.append(item)
            if total is None or len(rows) >= total or not page_rows:
                break
            pn += 1

        if total is not None:
            shortfall = total - len(rows)
            tolerance = max(_EM_MF_MIN_TOLERANCE, math.ceil(total * _EM_MF_TOLERANCE_RATIO))
            if shortfall > tolerance:
                raise ProviderError(
                    f"东财资金流分页不完整：total={total}，实际 {len(rows)} 行",
                    detail={"provider": self.name, "indicator": indicator,
                            "total": total, "fetched": len(rows)},
                )
            if shortfall > 0:
                logger.warning("东财资金流 %s 少量缺行：total=%d，实际 %d",
                               indicator, total, len(rows))

        frame = pd.DataFrame({
            "代码": [str(item.get("f12", "")) for item in rows],
            "名称": [item.get("f14") for item in rows],
            col_name: [item.get(raw_field) for item in rows],
        })
        stats = {"total": total, "fetched": len(rows), "pages": pn,
                 "pages_hit": pages_hit, "pages_fetched": pages_fetched}
        return frame, stats

    def _ths_moneyflow_df(self, symbol: str, out_col: str) -> pd.DataFrame:
        """同花顺个股资金流兜底（东财被风控时的降级源）。

        口径差异：THS ``净额`` = 流入-流出（**总净额**），非东财"主力净额"
        （超大单+大单）。仅作降级使用，结果 detail 里会标注 source=ths。
        数值为 "16.74亿" / "-810.40万" 字符串，需解析为元。
        """
        df = None
        last_exc: ProviderError | None = None
        for attempt in range(3):  # THS 域名偶发 DNS 瞬时失败（Errno -5），重试即可
            try:
                df = self._fetch_one(
                    ak.stock_fund_flow_individual,
                    f"stock_fund_flow_individual[{symbol}]",
                    symbol=symbol,
                )
                break
            except ProviderError as exc:
                last_exc = exc
                time.sleep(2.0 * (attempt + 1))
        if df is None:
            raise last_exc  # type: ignore[misc]
        if df is not None and getattr(df, "empty", True):
            return pd.DataFrame(columns=["代码", "名称", out_col])
        return pd.DataFrame({
            "代码": df["股票代码"].astype(str),
            "名称": df["股票简称"],
            out_col: df["净额"].map(_parse_cn_amount),
        })

    def fetch_moneyflow(self, trade_date: date) -> FetchResult:
        """个股资金流：今日 + 5 日（单位：元）。5 日失败降级为只有今日。

        源策略：东财 clist 直连（每页重试+当日页缓存）优先；
        东财被风控（连续坏窗口）时整体降级到同花顺（口径：总净额，非主力净额）。
        """
        started = time.perf_counter()
        source_tag = "akshare"
        today_stats: dict[str, Any] = {}
        five_stats: dict[str, Any] = {}
        five_day_df = None
        try:
            today_df, today_stats = self._em_moneyflow_df("今日")
            self._throttle()
            try:
                five_day_df, five_stats = self._em_moneyflow_df("5日")
            except ProviderError as exc:
                logger.warning("资金流 5 日榜获取失败（降级为仅今日）：%s", exc)
        except ProviderError as em_exc:
            logger.warning(
                "东财资金流不可用（%s），降级到同花顺（口径：总净额）", em_exc)
            source_tag = "ths-fallback"
            today_df = self._ths_moneyflow_df("即时", "今日主力净流入-净额")
            self._throttle()
            try:
                five_day_df = self._ths_moneyflow_df("5日排行", "5日主力净流入-净额")
            except ProviderError as exc:
                logger.warning("同花顺 5 日排行获取失败（降级为仅今日）：%s", exc)

        frame = _normalize_moneyflow(today_df, five_day_df)
        elapsed = int((time.perf_counter() - started) * 1000)
        return FetchResult(
            rows=len(frame),
            total=len(frame),
            elapsed_ms=elapsed,
            detail={"data": frame, "source": self.name, "source_tag": source_tag,
                    "trade_date": trade_date.isoformat(),
                    "five_day_available": five_day_df is not None,
                    "page_checks": {"today": today_stats, "five_day": five_stats}},
        )

    def fetch_margin(self, trade_date: date) -> FetchResult:
        """两融 + 北向（市场级，单位：亿元）。

        关键坑（实测确认）：两融数据是 **T+1 披露** 的。晚上跑 D 日流水线时，
        交易所还没有 D 日数据；而深交所接口**返回的 DataFrame 没有日期列**，
        盲目把返回值当 D 日会把 D-1 的数据写到 D 上（脏数据）。
        因此这里：

        1. 上交所按区间拉取（带 ``信用交易日期``）→ 取 ``<= trade_date`` 的最新
           已披露日作为 **effective_date**（可能 = D-1），过旧则视为未披露；
        2. 深交所按 **effective_date 显式请求**（接口支持 date 参数）；
        3. 落库行使用 effective_date，并在 detail 里标记 ``published_date_lag``。

        两个市场都必须成功：库里存的是沪深合计，缺任一市场会低估约一半；
        也**不做无日期参数的兜底**（akshare 默认区间分别截止 2023-09-22 和
        2024-04-11，会把陈旧数据标成请求日）。

        单位：上交所金额**元**（/1e8→亿元）；深交所金额**已是亿元**；
        北向"当日成交净买额"缺失属预期内（2024-08 后口径变化），失败不报错。
        """
        started = time.perf_counter()
        requested_date = trade_date
        lookback_start = requested_date - timedelta(days=15)
        sse_df = szse_df = north_df = None
        sse_ok = szse_ok = False

        try:
            sse_df = self._fetch_one(
                ak.stock_margin_sse,
                "stock_margin_sse",
                start_date=lookback_start.strftime("%Y%m%d"),
                end_date=requested_date.strftime("%Y%m%d"),
            )
            sse_ok = True
        except ProviderError as exc:
            # 不做无日期参数兜底：akshare 的默认区间截止 2023-09-22，
            # 会把多年前的数据当"最新已披露日"落库。
            logger.warning("上交所两融获取失败：%s", exc)

        sse_map = _margin_map_sse(sse_df) if sse_ok else {}
        effective_date = _margin_effective_date(sse_map, requested_date)
        if sse_ok and effective_date is None:
            # 上交所成功但区间内无近期数据：说明尚未披露或源返回陈旧历史，
            # 宁可"跳过"也不写错日期
            raise ProviderError(
                f"上交所两融区间内无近期已披露数据（{lookback_start} ~ {requested_date}）",
                detail={"provider": "akshare", "reason": "margin_not_published"},
            )
        if effective_date is None:
            effective_date = requested_date

        try:
            szse_df = self._fetch_one(
                ak.stock_margin_szse,
                "stock_margin_szse",
                date=effective_date.strftime("%Y%m%d"),
            )
            szse_ok = True
        except ProviderError as exc:
            # 不做无参兜底：该接口返回单行且无日期列，无参时 akshare 固定取
            # date="20240411"，会被误标成请求日写入。宁可缺一个市场的数据。
            logger.warning("深交所两融获取失败：%s", exc)

        if not sse_ok:
            # 没有上交所数据就无法确认披露日，SZSE 的日期只能靠猜
            raise ProviderError(
                "上交所两融获取失败，无法确认披露日期",
                detail={"provider": "akshare", "reason": "sse_unavailable"},
            )
        if not szse_ok:
            # 库里存的是沪深合计；只有单市场会低估约一半，不能落库
            raise ProviderError(
                "深交所两融获取失败，无法确认沪深合计余额",
                detail={"provider": "akshare", "reason": "szse_unavailable"},
            )

        szse_map = _margin_map_szse(szse_df, effective_date)

        # 北向：软失败（2024-08 后可能长期无数据，属预期内）
        self._throttle()
        try:
            north_df = self._fetch_one(ak.stock_hsgt_hist_em, "stock_hsgt_hist_em")
        except ProviderError as exc:
            logger.info("北向资金获取失败（预期内，忽略）：%s", exc)
        north_map = _normalize_north(north_df)

        sse_item = sse_map.get(effective_date, {})
        szse_item = szse_map.get(effective_date, {})
        margin_balance = None
        short_balance = None
        if sse_item.get("margin_balance") is not None or \
                szse_item.get("margin_balance") is not None:
            margin_balance = round(
                (sse_item.get("margin_balance") or 0.0)
                + (szse_item.get("margin_balance") or 0.0), 4,
            )
        if sse_item.get("short_balance") is not None or \
                szse_item.get("short_balance") is not None:
            short_balance = round(
                (sse_item.get("short_balance") or 0.0)
                + (szse_item.get("short_balance") or 0.0), 4,
            )

        rows = [
            {
                "trade_date": effective_date,
                "margin_balance": margin_balance,
                "short_balance": short_balance,
                "northward_net": north_map.get(effective_date),
            }
        ]
        frame = pd.DataFrame(rows)
        elapsed = int((time.perf_counter() - started) * 1000)
        lag = (requested_date - effective_date).days
        if lag > 0:
            logger.info(
                "两融数据 T+1 披露：请求日 %s 尚未披露，实际落库 %s（滞后 %d 天）",
                requested_date, effective_date, lag,
            )
        return FetchResult(
            rows=len(frame),
            total=len(frame),
            failed=0,
            elapsed_ms=elapsed,
            detail={
                "data": frame,
                "source": self.name,
                "requested_date": requested_date.isoformat(),
                "effective_date": effective_date.isoformat(),
                "published_date_lag": lag,
                "sse_available": sse_ok,
                "szse_available": szse_ok,
                "north_available": north_map.get(effective_date) is not None,
            },
        )

    # ---------- A6 指数 ----------
    def fetch_index_daily(self, trade_date: date, include_us: bool = False) -> FetchResult:
        """A 股指数日线（逐指数拉历史后过滤目标日）；美股可选（yfinance 未装则跳过）。"""
        from app.core.constants import INDEX_MAP

        started = time.perf_counter()
        frames: list[pd.DataFrame] = []
        failed = 0
        for index_code, (index_name, is_a_share) in INDEX_MAP.items():
            if not is_a_share:
                continue
            try:
                raw = self._fetch_one(
                    ak.stock_zh_index_daily,
                    f"stock_zh_index_daily[{index_code}]",
                    symbol=index_code,
                )
            except ProviderError as exc:
                failed += 1
                logger.warning("指数日线获取失败[%s]：%s", index_code, exc)
                continue
            self._throttle()
            normalized = _normalize_index(raw, index_code, index_name)
            if normalized.empty:
                continue
            target = normalized[normalized["trade_date"] == trade_date]
            if not target.empty:
                frames.append(target)

        if include_us:
            frames.extend(self._fetch_us_indices(trade_date))

        if not frames:
            frame = _empty_frame(INDEX_COLUMNS)
        else:
            frame = pd.concat(frames, ignore_index=True)
        elapsed = int((time.perf_counter() - started) * 1000)
        return FetchResult(
            rows=len(frame),
            total=len(frame),
            failed=failed,
            elapsed_ms=elapsed,
            detail={"data": frame, "source": self.name,
                    "trade_date": trade_date.isoformat(),
                    "include_us": include_us},
        )

    def _fetch_us_indices(self, trade_date: date) -> list[pd.DataFrame]:
        """美股三大指数（可选依赖 yfinance）。未安装/失败 → 返回空列表，不报错。"""
        try:  # pragma: no cover —— 取决于环境是否安装 yfinance
            import yfinance as yf
        except ImportError:
            logger.info("yfinance 未安装，跳过美股指数（不影响 A 股链路）")
            return []

        from app.core.constants import INDEX_MAP

        frames: list[pd.DataFrame] = []
        for index_code, (index_name, is_a_share) in INDEX_MAP.items():
            if is_a_share:
                continue
            try:
                ticker = yf.Ticker(index_code)
                hist = ticker.history(
                    start=(trade_date - pd.Timedelta(days=10)).isoformat(),
                    end=(trade_date + pd.Timedelta(days=1)).isoformat(),
                    auto_adjust=False,
                )
            except Exception as exc:  # noqa: BLE001 —— 可选能力，失败忽略
                logger.warning("美股指数获取失败[%s]：%s", index_code, exc)
                continue
            item = _normalize_us_index(hist, index_code, index_name, trade_date)
            if item is not None:
                frames.append(pd.DataFrame([item], columns=list(INDEX_COLUMNS)))
        return frames


__all__ = [
    "AkshareProvider",
    "SHARES_PER_LOT",
    "SPOT_COLUMNS",
    "HISTORY_COLUMNS",
    "SECTOR_MEMBER_COLUMNS",
    "MONEYFLOW_COLUMNS",
    "INDEX_COLUMNS",
    "CALENDAR_COLUMNS",
    "_pick",
    "_num",
    "_to_date",
    "_stock_code",
    "_normalize_spot",
    "_normalize_stock_history",
    "_normalize_sector_list",
    "_normalize_sector_hist",
    "_normalize_sector_members",
    "_normalize_moneyflow",
    "_margin_map_sse",
    "_margin_map_szse",
    "_normalize_north",
    "_normalize_index",
    "_normalize_us_index",
    "_normalize_calendar",
    "build_board_code_maps",
    "normalize_board_name",
    "resolve_em_board_code",
]
