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


# ==================== 市场温度计（V6 T3）====================
# 温度分 = 六个子项各自归一化到 0–100 后加权平均。
# 归一化全部使用**固定的分段断点**（原始值 → 得分），不做逐日 min-max 缩放：
# 温度分必须跨日可比，绝不能因为今天整体偏冷就把最冷的一天算成 0 分。
# 断点依据：2021–2025 全市场日频分布的经验分位（上涨占比 30/50/70%、
# 涨停 20/50/80 家、站上 MA20 40/50/60%、量能 0.8/1.0/1.3 倍、年化波动 15/20/25%）。
TEMP_SCORE_MIN = 0.0
TEMP_SCORE_MAX = 100.0

#: 子项权重（合计 1.0）。跌停家数与指数波动率是**降温项**：
#: 其断点已把"数值越大"映射为"得分越低"（direction=-1），合成公式保持统一。
TEMP_WEIGHTS: dict[str, float] = {
    "up_ratio": 0.30,       # 市场宽度：最直接的赚钱效应
    "limit_up": 0.15,       # 涨停家数：短线情绪强度
    "limit_down": 0.10,     # 跌停家数（降温）
    "above_ma20": 0.20,     # 站上 MA20 比例：中期趋势结构
    "amount_ratio": 0.15,   # 量能比：资金参与度
    "index_vol": 0.10,      # 指数年化波动（降温）
}

#: 子项顺序（API/前端展示顺序，与权重字典解耦保证稳定）
TEMP_COMPONENT_ORDER = (
    "up_ratio", "limit_up", "limit_down",
    "above_ma20", "amount_ratio", "index_vol",
)

#: 子项展示元信息
TEMP_COMPONENT_META: dict[str, dict[str, str]] = {
    "up_ratio": {"label": "上涨占比", "unit": "%"},
    "limit_up": {"label": "涨停家数", "unit": "家"},
    "limit_down": {"label": "跌停家数", "unit": "家"},
    "above_ma20": {"label": "站上 MA20", "unit": "%"},
    "amount_ratio": {"label": "量能比", "unit": "倍"},
    "index_vol": {"label": "指数波动率", "unit": "%"},
}

#: 归一化断点：((原始值, 得分), ...)，必须按原始值单调、得分单调不减（降温项单调不增）。
#: 超出两端按端点截断，不外推 —— 极端值不应把温度分推到负值或 100 以上。
TEMP_BREAKPOINTS: dict[str, tuple[tuple[float, float], ...]] = {
    # 上涨占比 50%→50 分、70%→80 分、30%→20 分（任务书示例）
    "up_ratio": ((0.0, 0.0), (0.30, 20.0), (0.50, 50.0), (0.70, 80.0), (1.0, 100.0)),
    # 涨停家数：A 股常态 30–80 家，200 家以上视为极热
    "limit_up": ((0, 5.0), (20, 25.0), (50, 50.0), (80, 70.0), (120, 85.0), (200, 100.0)),
    # 跌停家数（降温项）：0 家不扣分，100 家以上视为极冷
    "limit_down": ((0, 100.0), (5, 90.0), (15, 70.0), (30, 45.0), (60, 15.0), (100, 0.0)),
    # 站上 MA20 比例：50% 为多空分界
    "above_ma20": ((0.0, 0.0), (0.20, 10.0), (0.40, 40.0), (0.50, 50.0),
                   (0.60, 65.0), (0.80, 90.0), (1.0, 100.0)),
    # 量能比 = 当日全市场成交额 / 前 20 个交易日均值
    "amount_ratio": ((0.5, 0.0), (0.8, 25.0), (1.0, 50.0),
                     (1.3, 75.0), (1.6, 90.0), (2.0, 100.0)),
    # 沪深300 20 日年化波动率（%）：10% 极稳，50% 以上极冷
    "index_vol": ((10.0, 100.0), (15.0, 85.0), (20.0, 65.0),
                  (25.0, 45.0), (35.0, 20.0), (50.0, 0.0)),
}

#: 降温项（direction=-1 仅用于前端标注"降温项"，得分已由断点反转）
TEMP_COOLING_COMPONENTS = ("limit_down", "index_vol")

#: 关键子项：缺失时温度分不可信 → confidence=insufficient、总分 null。
#: 宽度（上涨占比）是温度的基础；站上 MA20 是中期结构锚，特征覆盖率 < 阈值即视为缺失。
TEMP_CRITICAL_COMPONENTS = ("up_ratio", "above_ma20")

#: 至少有多少个可用子项才给总分；不足 → insufficient（禁止用 0 代入缺失项）
TEMP_MIN_COMPONENTS = 4

#: MA20 特征覆盖率门槛（与策略侧 `_assert_data_ready` 的 0.5 保持一致）
TEMP_MIN_FEATURE_COVERAGE = 0.5

#: 量能比窗口：前 N 个交易日全市场成交额均值；至少 N_MIN 天才计算
TEMP_AMOUNT_MA_DAYS = 20
TEMP_AMOUNT_MIN_DAYS = 10
#: 取 21 个交易日所需的自然日回看（含春节长假冗余）
TEMP_AMOUNT_LOOKBACK_DAYS = 60

#: 指数波动率窗口（交易日）
TEMP_INDEX_VOL_DAYS = 20


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
