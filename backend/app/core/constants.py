"""
A 股市场常量（方案 6.1：涨跌停幅度不同，回测必须区分）
=======================================================
集中管理：交易所判定、板块判定、涨跌停规则、ST 判定、指数映射。
不要在业务代码里散落 `code.startswith("688")` 这类魔法判断。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

# ==================== 交易所 / 板块 ====================
EXCHANGE_SH = "SH"
EXCHANGE_SZ = "SZ"
EXCHANGE_BJ = "BJ"

BOARD_MAIN = "主板"
BOARD_GEM = "创业板"      # 300 / 301
BOARD_STAR = "科创板"     # 688 / 689
BOARD_BSE = "北交所"      # 4xxxxx / 8xxxxx / 920xxx

# 涨停幅度（普通股；ST 另行处理）
LIMIT_PCT: dict[str, float] = {
    BOARD_MAIN: 0.10,
    BOARD_GEM: 0.20,
    BOARD_STAR: 0.20,
    BOARD_BSE: 0.30,
}
ST_LIMIT_PCT: dict[str, float] = {
    BOARD_MAIN: 0.05,
    BOARD_GEM: 0.20,
    BOARD_STAR: 0.20,
    BOARD_BSE: 0.30,
}

# ==================== 标的生命周期 ====================
STATUS_NORMAL = 1
STATUS_SUSPENDED = 2
STATUS_DELISTED = 9

# ==================== 所有制（A3 策略输入）====================
OWNERSHIP_CENTRAL = "央企"
OWNERSHIP_LOCAL = "地方国企"
OWNERSHIP_PRIVATE = "民企"
OWNERSHIP_FOREIGN = "外资"
OWNERSHIP_OTHER = "其他"
OWNERSHIP_UNKNOWN = "未知"

# ==================== 指数映射 ====================
# 代码 → (名称, 是否 A 股)
INDEX_MAP: dict[str, tuple[str, bool]] = {
    "sh000001": ("上证指数", True),
    "sz399001": ("深证成指", True),
    "sz399006": ("创业板指", True),
    "sh000688": ("科创50", True),
    "sh000300": ("沪深300", True),      # 回测基准
    "sh000905": ("中证500", True),
    "sh000852": ("中证1000", True),
    "^DJI": ("道琼斯工业", False),
    "^GSPC": ("标普500", False),
    "^IXIC": ("纳斯达克", False),
}
BENCHMARK_INDEX = "sh000300"

# ==================== 板块类型 ====================
SECTOR_INDUSTRY = "industry"
SECTOR_CONCEPT = "concept"


# ==================== 函数 ====================
def exchange_of(code: str) -> str:
    """6 位代码 → 交易所。"""
    code = str(code).zfill(6)
    if code.startswith(("6", "9")):
        return EXCHANGE_SH
    if code.startswith(("0", "2", "3")):
        return EXCHANGE_SZ
    if code.startswith(("4", "8", "920")):
        return EXCHANGE_BJ
    # 兜底：按首位粗略判断，避免 None
    return EXCHANGE_SZ


def board_of(code: str) -> str:
    """6 位代码 → 板块。"""
    code = str(code).zfill(6)
    if code.startswith(("688", "689")):
        return BOARD_STAR
    if code.startswith(("300", "301")):
        return BOARD_GEM
    if code.startswith(("4", "8", "920")):
        return BOARD_BSE
    return BOARD_MAIN


def limit_pct_of(code: str, is_st: bool = False) -> float:
    """涨跌停幅度（小数，如 0.10）。"""
    board = board_of(code)
    return (ST_LIMIT_PCT if is_st else LIMIT_PCT).get(board, 0.10)


def limit_prices(pre_close: float | Decimal | None, code: str,
                 is_st: bool = False) -> tuple[float | None, float | None]:
    """由前收盘价算出 (涨停价, 跌停价)，按 A 股规则四舍五入到分。"""
    if pre_close is None:
        return None, None
    pre = float(pre_close)
    if pre <= 0:
        return None, None
    pct = limit_pct_of(code, is_st)
    # 交易所规则：涨跌停价 = round(前收 × (1±幅度), 2)
    up = round(pre * (1 + pct) + 1e-9, 2)
    down = round(pre * (1 - pct) + 1e-9, 2)
    return up, down


def is_st_name(name: str | None) -> bool:
    """按名称判定 ST/*ST/退市整理。"""
    if not name:
        return False
    n = name.upper().replace(" ", "")
    return "ST" in n or "退" in n


def is_index_code(code: str) -> bool:
    """是否指数代码（用于把指数从个股池里剔除）。"""
    return code in INDEX_MAP


def trading_year_range(start: date, end: date) -> list[int]:
    """返回需要建分区的年份列表（含两端）。"""
    return list(range(start.year, end.year + 1))


__all__ = [
    "exchange_of", "board_of", "limit_pct_of", "limit_prices", "is_st_name",
    "is_index_code", "trading_year_range", "LIMIT_PCT", "ST_LIMIT_PCT",
    "BOARD_MAIN", "BOARD_GEM", "BOARD_STAR", "BOARD_BSE", "INDEX_MAP",
    "BENCHMARK_INDEX", "SECTOR_INDUSTRY", "SECTOR_CONCEPT",
]
