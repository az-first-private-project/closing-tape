# 🚀 GFNA V3 — 盘后量化分析终端 · AI 执行说明书

> **本文档用途**：给 AI（或任何执行者）看的逐步构建说明书。
> 每一步精确到文件路径、函数签名、数据库字段。按顺序执行即可。
>
> **前提**：后端 FastAPI + PostgreSQL，前端 React + Vite + TypeScript + Tailwind + shadcn/ui 已就绪（V2 成果）。

---

## 🎯 最终产品画像

每天 A 股收盘后（15:00），系统自动执行一条流水线：

```
① 拉取行情数据 (AKShare + yfinance)
       │
② 爬取论坛情绪数据 (东方财富股吧)
       │
③ 计算技术指标 + 新闻情感 + 综合评分
       │
④ 识别各板块实力龙头 / 情绪龙头
       │
⑤ 调 DeepSeek API 生成复盘报告 + 短/中/长期预测
       │
⑥ 前端仪表盘展示：指数卡片 → 板块热力图 → 评分面板 → AI 报告
```

---

## 📁 目标项目结构（新增/修改的文件）

```
grna/
├── backend/
│   ├── app/
│   │   ├── api/v1/
│   │   │   ├── articles.py          # [已有] 新闻 CRUD
│   │   │   ├── stats.py             # [已有] 统计接口
│   │   │   └── analysis.py          # [新增] V3 分析仪表盘 API
│   │   ├── db/
│   │   │   ├── models.py            # [已有] 原有 ORM
│   │   │   └── models_v3.py         # [新增] V3 新增表 ORM
│   │   ├── services/
│   │   │   ├── llm_service.py       # [已有] DeepSeek 调用
│   │   │   ├── market_data.py       # [新增] AKShare + yfinance 数据拉取
│   │   │   ├── technical_analysis.py # [新增] 技术指标计算
│   │   │   ├── sentiment.py         # [新增] 情绪分析（融资+论坛+北向）
│   │   │   ├── scoring.py           # [新增] 三维综合评分
│   │   │   ├── leader_identifier.py # [新增] 龙头识别
│   │   │   ├── report_generator.py  # [新增] AI 报告生成
│   │   │   └── crawler_service.py   # [已有] 爬虫调度
│   │   └── main.py                  # [修改] 注册新路由
│   └── scripts/
│       ├── fetch_daily_data.py      # [新增] 拉取今日行情
│       ├── run_analysis.py          # [新增] 执行全部分析
│       └── init_v3_db.py            # [新增] 初始化 V3 表结构
├── frontend/
│   └── src/
│       ├── pages/
│       │   └── DashboardV3.tsx      # [新增] V3 仪表盘主页面
│       ├── components/
│       │   ├── IndexCard.tsx        # [新增] 指数卡片
│       │   ├── SectorHeatmap.tsx    # [新增] 板块热力图
│       │   ├── ScoreRanking.tsx     # [新增] 综合评分 Top 10
│       │   └── AIReport.tsx         # [新增] AI 报告渲染
│       ├── hooks/
│       │   └── useDashboard.ts      # [新增] 仪表盘数据 hook
│       ├── lib/
│       │   └── api.ts               # [修改] 新增仪表盘 API 调用
│       └── App.tsx                  # [修改] 新增路由
└── docs/
    └── ROADMAP-V3.md               # 本文件
```

---

## 🔴 Step 1：数据库——新增 4 张表

### 执行内容

在 `backend/app/db/models_v3.py` 中创建以下 ORM 模型。

### 数据模型定义

```python
# backend/app/db/models_v3.py

from sqlalchemy import Column, Integer, String, Date, BigInteger, Numeric, Text, UniqueConstraint
from app.db.base import Base

class MarketIndex(Base):
    """大盘指数日线"""
    __tablename__ = "market_indices"
    __table_args__ = (UniqueConstraint("index_code", "trade_date"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    index_code = Column(String(20), nullable=False, comment="如 sh000001")
    index_name = Column(String(50), nullable=False, comment="上证指数")
    trade_date = Column(Date, nullable=False)
    open_price = Column(Numeric(12, 3))
    close_price = Column(Numeric(12, 3))
    high_price = Column(Numeric(12, 3))
    low_price = Column(Numeric(12, 3))
    volume = Column(BigInteger)
    amount = Column(Numeric(20, 2))
    change_pct = Column(Numeric(6, 3), comment="涨跌幅 %")


class SectorDaily(Base):
    """板块日线——热力图的数据源"""
    __tablename__ = "sector_daily"
    __table_args__ = (UniqueConstraint("sector_code", "trade_date"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    sector_code = Column(String(20), nullable=False)
    sector_name = Column(String(50), nullable=False)
    sector_type = Column(String(20), default="concept", comment="industry 或 concept")
    trade_date = Column(Date, nullable=False)
    close_price = Column(Numeric(12, 3))
    change_pct = Column(Numeric(6, 3), comment="涨跌幅 %")
    volume = Column(BigInteger)
    amount = Column(Numeric(20, 2))
    turnover_rate = Column(Numeric(6, 3))
    leading_stock = Column(String(50), comment="领涨股名称")
    leading_stock_pct = Column(Numeric(6, 3))
    up_count = Column(Integer, comment="上涨家数")
    down_count = Column(Integer, comment="下跌家数")


class MarginTrading(Base):
    """融资融券 + 北向资金（市场级）"""
    __tablename__ = "margin_trading"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False, unique=True)
    margin_balance = Column(Numeric(16, 2), comment="融资余额（亿元）")
    short_balance = Column(Numeric(16, 2), comment="融券余额（亿元）")
    margin_change = Column(Numeric(12, 2), comment="融资余额日变化（亿元）")
    northward_net = Column(Numeric(12, 2), comment="北向资金净流入（亿元）")


class SectorScore(Base):
    """板块综合评分——仪表盘的核心数据"""
    __tablename__ = "sector_score"
    __table_args__ = (UniqueConstraint("trade_date", "sector_code"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False)
    sector_code = Column(String(20), nullable=False)
    technical_score = Column(Numeric(4, 2), comment="技术面评分 0-10")
    sentiment_score = Column(Numeric(4, 2), comment="情绪面评分 0-10")
    news_score = Column(Numeric(4, 2), comment="消息面评分 0-10")
    composite_score = Column(Numeric(4, 2), comment="综合评分 0-10")
    strength_leaders = Column(Text, comment="实力龙头 JSON: [{name,code,change_pct}]")
    sentiment_leaders = Column(Text, comment="情绪龙头 JSON: [{name,code,change_pct}]")
    ai_summary = Column(Text, comment="AI 生成的板块分析短文")
```

### 建表方式

两种方式二选一：

**方式 A**（推荐）：在 `backend/app/main.py` 的 startup 事件中加一行：
```python
Base.metadata.create_all(bind=engine)
```
这样所有新表自动创建。

**方式 B**：在 `backend/app/db/models.py` 文件末尾 import models_v3：
```python
from app.db.models_v3 import MarketIndex, SectorDaily, MarginTrading, SectorScore
```
确保 Alembic 或 create_all 能发现新模型。

### 验证

```bash
# 启动后端后检查数据库
psql -U postgres -d gfna -c "\dt"
# 应该看到 market_indices, sector_daily, margin_trading, sector_score 四张新表
```

---

## 🟠 Step 2：数据拉取层——market_data.py

### 2.1 安装依赖

```bash
cd backend
pip install akshare yfinance pandas numpy
```

### 2.2 创建 `backend/app/services/market_data.py`

这个文件是**所有行情数据的唯一入口**。对外暴露 4 个函数：

```python
# backend/app/services/market_data.py

"""
行情数据拉取服务
- A 股：AKShare（免费，覆盖全）
- 美股：yfinance（免费，稳定）
"""

import akshare as ak
import yfinance as yf
import pandas as pd
from datetime import date, timedelta
from typing import Optional

# ============================================================
# A 股 — 大盘指数
# ============================================================

# 指数代码映射
A_INDEX_CODES = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000688": "科创50",
}

def fetch_a_indices(trade_date: Optional[date] = None) -> list[dict]:
    """
    拉取 A 股四大指数的日线数据。
    返回: [{"index_code": "sh000001", "index_name": "上证指数",
             "close_price": 3250.12, "change_pct": 0.35, ...}, ...]
    """
    # 提示：使用 ak.stock_zh_index_daily(symbol="sh000001")
    # 它的返回 DataFrame 包含 date/open/close/high/low/volume/outstanding_share
    # 注意处理 AKShare 的列名映射
    pass


# ============================================================
# A 股 — 板块行情（热力图数据源）
# ============================================================

def fetch_sector_boards(trade_date: Optional[date] = None) -> list[dict]:
    """
    拉取东方财富板块行情（概念板块 + 行业板块）。
    返回: [{"sector_code": "BK1186", "sector_name": "半导体",
             "change_pct": 3.2, "leading_stock": "中芯国际",
             "leading_stock_pct": 5.8, "up_count": 45, "down_count": 12}, ...]

    用到的 AKShare 函数：
    - ak.stock_board_concept_name_em()  → 概念板块
    - ak.stock_board_industry_name_em() → 行业板块
    """
    pass


# ============================================================
# A 股 — 融资融券 + 北向资金
# ============================================================

def fetch_margin_trading(trade_date: Optional[date] = None) -> dict:
    """
    拉取融资融券余额 + 北向资金。
    返回: {"margin_balance": 15680.0, "short_balance": 980.0,
            "margin_change": 120.0, "northward_net": 45.6}

    用到的 AKShare 函数：
    - ak.stock_margin_sz_sh_daily()  → 融资融券日数据
    - ak.stock_hsgt_north_net_em()   → 北向资金日净流入
    """
    pass


# ============================================================
# 美股 — 三大指数
# ============================================================

US_INDEX_SYMBOLS = {
    "^DJI":  "道琼斯工业",
    "^GSPC": "标普500",
    "^IXIC": "纳斯达克",
}

def fetch_us_indices() -> list[dict]:
    """
    通过 yfinance 拉取美股三大指数昨日收盘数据。
    注意：美股收盘时间是北京时间次日凌晨，所以通常取 T-1 日数据。
    返回格式同 fetch_a_indices。
    """
    pass


# ============================================================
# A 股 — 板块成分股（辅助：龙头识别需要）
# ============================================================

def fetch_sector_stocks(sector_code: str) -> list[dict]:
    """
    拉取某个板块的成分股列表及今日行情。
    返回: [{"code": "688981", "name": "中芯国际", "market_cap": 4500亿,
             "change_pct": 2.1, "turnover_rate": 3.5, "volume": ..., "amount": ...}, ...]

    用到的 AKShare 函数：
    - ak.stock_board_concept_cons_em(symbol=sector_code)
    """
    pass
```

### ⚠️ 关键注意事项（写给执行者）

1. **AKShare 接口偶尔变动**：每个取数函数外面用 `try/except` 包一层，失败了打印日志、返回空列表或 None，**不要让一个数据源挂掉影响其他数据源**。

2. **交易日判断**：如果是周末/节假日，AKShare 返回的可能是最近一个交易日的数据。

3. **yfinance 美股数据**：取最近一个交易日，可以用 `yf.Ticker(symbol).history(period="2d")`，取倒数第二行（因为今天可能还没收盘）。

4. **板块数量很多**（概念板块 400+ 个）：全拉回来，但前端展示只取涨跌幅绝对值最大的前 50 个做热力图。评分只对 Top 20 领涨 + Top 10 领跌做即可，不需要全部分析。

### 验证

```bash
python -c "
from backend.app.services.market_data import fetch_a_indices, fetch_sector_boards
indices = fetch_a_indices()
print(f'指数: {len(indices)} 条')
boards = fetch_sector_boards()
print(f'板块: {len(boards)} 条')
"
```

---

## 🟡 Step 3：技术分析——technical_analysis.py

### 3.1 创建 `backend/app/services/technical_analysis.py`

```python
# backend/app/services/technical_analysis.py

"""
技术指标计算
不引入 ta 库（太重），手写几个核心指标即可。
每个板块取近 30 个交易日的收盘价序列做计算。
"""

import pandas as pd
import numpy as np
from typing import Optional


def calc_ma(series: pd.Series, window: int) -> pd.Series:
    """移动平均线"""
    return series.rolling(window=window).mean()


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """相对强弱指标 RSI"""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calc_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD 指标，返回 (MACD线, Signal线, 柱)"""
    ema_fast = series.ewm(span=fast).mean()
    ema_slow = series.ewm(span=slow).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def score_sector_technical(close_prices: pd.Series, volumes: pd.Series) -> float:
    """
    根据近 30 个交易日的收盘价和成交量，返回技术面评分 (0-10)。

    评分逻辑：
      - 趋势分 (40%): 当前价 vs MA5/MA10/MA20 的位置
      - 动量分 (30%): RSI 是否在 40-70 区间 + MACD 是否金叉
      - 量能分 (30%): 最近 5 日均量 vs 20 日均量

    返回 float: 0.0 ~ 10.0
    """
    if len(close_prices) < 20:
        return 5.0  # 数据不足，给中性分

    ma5 = calc_ma(close_prices, 5).iloc[-1]
    ma10 = calc_ma(close_prices, 10).iloc[-1]
    ma20 = calc_ma(close_prices, 20).iloc[-1]
    last_price = close_prices.iloc[-1]

    # 趋势分：价格在均线之上越多越好
    trend_raw = 0
    for ma in [ma5, ma10, ma20]:
        trend_raw += (last_price - ma) / ma * 100  # 距离均线的百分比
    trend_score = min(10, max(0, 5 + trend_raw * 0.5))

    # 动量分
    rsi = calc_rsi(close_prices).iloc[-1]
    momentum_score = 7.0 if 40 <= rsi <= 70 else (5.0 if 30 <= rsi <= 80 else 3.0)
    # MACD 金叉加分
    macd_line, signal_line, _ = calc_macd(close_prices)
    if macd_line.iloc[-1] > signal_line.iloc[-1] and macd_line.iloc[-2] <= signal_line.iloc[-2]:
        momentum_score += 1.5

    # 量能分
    vol_5d_avg = volumes.iloc[-5:].mean()
    vol_20d_avg = volumes.iloc[-20:].mean()
    vol_ratio = vol_5d_avg / vol_20d_avg if vol_20d_avg > 0 else 1.0
    volume_score = min(10, max(0, 5 + (vol_ratio - 1) * 5))

    composite = trend_score * 0.4 + momentum_score * 0.3 + volume_score * 0.3
    return round(min(10, max(0, composite)), 2)
```

### 验证

```python
# 构造假数据测试
import pandas as pd
prices = pd.Series([100 + i * 0.5 + (i % 5) for i in range(30)])
volumes = pd.Series([1000000 for _ in range(30)])
score = score_sector_technical(prices, volumes)
print(f"技术面评分: {score}")  # 应该输出 0~10 之间的值
```

---

## 🟢 Step 4：情绪分析——sentiment.py

### 4.1 创建 `backend/app/services/sentiment.py`

```python
# backend/app/services/sentiment.py

"""
市场情绪分析
数据来源：
  1. 融资融券数据（margin_trading 表）
  2. 北向资金
  3. 论坛讨论热度（从 sentiment_spider 爬取）
  4. 板块主力资金流向（AKShare 有相关接口）
"""

def calc_margin_sentiment(margin_data: dict) -> float:
    """
    融资情绪分 (0-10)
    - 融资余额连续 3 日增加 → 看多 → 高分
    - 融资余额连续下降 → 去杠杆 → 低分
    需要取近 5 日的 margin_change 序列来判断趋势。
    """
    pass


def calc_northward_sentiment(northward_net: float) -> float:
    """
    北向资金情绪分 (0-10)
    - 净流入 > 50 亿 → 8+
    - 净流出 > 50 亿 → <4
    - 中间线性映射
    """
    pass


def calc_forum_heat_sentiment(sector_code: str, trade_date) -> float:
    """
    论坛热度情绪分 (0-10)
    - 从数据库查当天的论坛帖子数据
    - 计算热度 = 总评论数 * 2 + 总阅读数 / 100
    - 与自身历史均值比较：显著放大 → 关注度高 → 高分
    - 但排除暴跌恐慌（板块当天跌 > 3% 且讨论暴增 → 恐慌 → 低分）
    """
    pass


def calc_sector_sentiment(sector_code: str, trade_date, margin_data: dict,
                          northward_net: float) -> float:
    """
    综合情绪面评分 (0-10)
    权重：融资变化(25%) + 北向资金(25%) + 论坛热度(25%) + 板块资金流(25%)
    注：融资和北向是市场级的，所有板块共享；论坛和资金流是板块级的。
    """
    pass
```

### 4.2 论坛爬虫（简化版）

不要用完整的 Scrapy Spider——太重量级。直接在 `market_data.py` 里加一个轻量函数，用 `requests` + `BeautifulSoup` 或 AKShare 的现成接口。

实际上，东方财富有行情接口可以拿到"个股人气榜"，AKShare 也封装了这些接口。优先找现成的，实在没有再自己爬。

```python
# 可以放在 market_data.py 或新建 sentiment.py 里

def fetch_forum_hot_posts(sector_name: str, limit: int = 20) -> list[dict]:
    """
    拉取东方财富股吧某个板块的今日热帖。
    方案 1（优先）：用东方财富的公开 API（如果找到）
    方案 2（fallback）：requests + BeautifulSoup 爬取
    返回: [{"title": "...", "read_count": 12345, "comment_count": 89,
             "post_time": "2026-07-25 14:30"}, ...]
    """
    pass
```

---

## 🔵 Step 5：综合评分——scoring.py

### 5.1 创建 `backend/app/services/scoring.py`

```python
# backend/app/services/scoring.py

"""
板块综合评分
技术面 + 情绪面 + 消息面 → 综合评分
"""

from app.services.technical_analysis import score_sector_technical
from app.services.sentiment import calc_sector_sentiment
from app.services.llm_service import analyze_news_sentiment  # 用 DeepSeek 判断新闻情感


def score_news_for_sector(sector_name: str, trade_date) -> float:
    """
    消息面评分 (0-10)
    1. 从 news_articles 表查出当天该板块相关的新闻
    2. 调用 DeepSeek API 判断每条新闻的情感倾向（利好/利空/中性，严重程度 1-5）
    3. 加权汇总得到消息面评分
    """
    pass


def compute_composite(technical: float, sentiment: float, news: float) -> float:
    """
    综合评分 = 技术面(40%) + 情绪面(35%) + 消息面(25%)
    注意权重可调整，这只是初始版本。
    """
    return round(technical * 0.4 + sentiment * 0.35 + news * 0.25, 2)


def score_all_sectors(trade_date) -> list[dict]:
    """
    对当天所有板块计算完整评分。
    返回: [{"sector_code": "BK1186", "sector_name": "半导体",
             "technical_score": 8.5, "sentiment_score": 7.2,
             "news_score": 8.0, "composite_score": 7.9}, ...]

    流程：
    1. 遍历 sector_daily 表中 trade_date 的所有板块
    2. 对每个板块：查近 30 天收盘价 → technical_analysis
    3. 对每个板块：查情绪数据 → sentiment
    4. 对每个板块：查相关新闻 → LLM 情感分析 → news_score
    5. 加权得 composite_score
    6. 写入 sector_score 表
    """
    pass
```

---

## 🟣 Step 6：龙头识别——leader_identifier.py

### 6.1 创建 `backend/app/services/leader_identifier.py`

```python
# backend/app/services/leader_identifier.py

"""
板块龙头识别
- 实力龙头：市值大 + 流动性稳（近 30 天数据）
- 情绪龙头：当日涨幅大 + 换手率高（当天数据）
"""

def identify_strength_leaders(sector_code: str, trade_date) -> list[dict]:
    """
    实力龙头识别（纯规则，不需要 AI）

    算法：
    1. 取板块所有成分股
    2. 按市值降序，取前 10
    3. 在 Top 10 中按近 30 日日均成交额降序，取前 3
    4. 返回这 3 只的基本信息

    返回: [{"code": "688981", "name": "中芯国际",
             "market_cap": 4500, "avg_amount_30d": 12.5亿,
             "change_pct": 2.1, "reason": "市值第1 + 日均成交第2"}, ...]
    """
    pass


def identify_sentiment_leaders(sector_code: str, trade_date) -> list[dict]:
    """
    情绪龙头识别（纯规则，不需要 AI）

    算法：
    1. 取板块所有成分股当日行情
    2. 过滤：50亿 < 市值 < 2000亿（排除拉不动的大象和庄股嫌疑的小盘）
    3. 按 (当日涨幅 × 换手率) 降序，取前 2
    4. 返回这 2 只的基本信息

    返回: [{"code": "688256", "name": "寒武纪",
             "change_pct": 5.8, "turnover_rate": 15.2,
             "reason": "涨幅第1 × 换手率第2"}, ...]
    """
    pass
```

---

## 🔴 Step 7：AI 报告生成——report_generator.py

### 7.1 创建 `backend/app/services/report_generator.py`

这是整个系统最"智能"的部分。核心工作：**把分析引擎算好的数据组装成一个结构化 Prompt → 调 DeepSeek → 把返回的 Markdown 存进数据库**。

```python
# backend/app/services/report_generator.py

"""
AI 复盘报告生成
用 DeepSeek API 将量化分析结果转化为自然语言报告
"""

import json
from app.services.llm_service import chat  # 复用现有 DeepSeek 调用


def build_report_prompt(trade_date, market_data: dict, top_sectors: list,
                        bottom_sectors: list, margin_data: dict,
                        related_news: list) -> str:
    """
    组装 Prompt。

    输入：
    - market_data: 大盘指数数据
    - top_sectors: Top 5 领涨板块（含三面评分）
    - bottom_sectors: Top 5 领跌板块
    - margin_data: 融资融券 + 北向
    - related_news: 当日重要新闻（含 AI 预判的情绪标签）

    输出：一个结构化的 Prompt 字符串
    """
    prompt = f"""你是一个专业 A 股量化分析师。以下是 {trade_date} 收盘后的市场数据，请撰写一份复盘报告。

【大盘概况】
{_format_indices(market_data)}

【领涨板块 Top 5】
{_format_sectors(top_sectors)}

【领跌板块 Top 5】
{_format_sectors(bottom_sectors)}

【融资融券与外资动向】
融资余额: {margin_data.get('margin_balance', 'N/A')} 亿
北向资金净流入: {margin_data.get('northward_net', 'N/A')} 亿

【今日重要财经新闻】
{_format_news(related_news)}

请按以下格式输出（用 Markdown，不要用代码块包裹）：

## 今日大盘综述
（150 字以内，概括今日市场整体情况）

## 领涨板块分析
（对每个领涨板块，50 字分析背后原因，结合新闻和技术面）

## 领跌板块分析
（同上）

## 短期走势预测（1-5 个交易日）
（基于技术指标如 RSI/MACD、市场情绪、近期新闻，给出大盘和重点板块的短期判断）

## 中期走势预测（1-4 周）
（基于趋势分析、政策面、板块轮动规律）

## 长期走势预测（1-6 个月）
（基于宏观基本面、资金面、估值水平等）

---
注意：
- 所有判断必须引用上面给出的具体数据，不要泛泛而谈
- 预测部分必须标注"此为 AI 基于数据模型的推测，不构成投资建议"
- 语气专业克制，不要夸张渲染
"""
    return prompt


def generate_report(trade_date) -> str:
    """
    主流程：
    1. 从数据库读取当天分析数据
    2. 组装 Prompt
    3. 调用 DeepSeek
    4. 将返回的 Markdown 存入 sector_score 表（每个板块的 ai_summary 字段）
       或者存到一个独立的 daily_report 表
    5. 返回完整报告文本
    """
    pass


# ========== 辅助格式化函数 ==========

def _format_indices(market_data: dict) -> str:
    """把大盘指数数据格式化成 Prompt 中的可读文本"""
    lines = []
    for idx in market_data.get("indices", []):
        emoji = "🔴" if idx["change_pct"] < 0 else "🟢"
        lines.append(f"{idx['index_name']}: {idx['close_price']} "
                     f"({idx['change_pct']:+.2f}%) {emoji}")
    return "\n".join(lines)


def _format_sectors(sectors: list) -> str:
    """把板块数据格式化成 Prompt 可读文本"""
    lines = []
    for i, s in enumerate(sectors, 1):
        lines.append(
            f"{i}. {s['sector_name']} {s['change_pct']:+.2f}% "
            f"(技术面: {s['technical_score']}/10, "
            f"情绪面: {s['sentiment_score']}/10, "
            f"消息面: {s['news_score']}/10)"
        )
    return "\n".join(lines)


def _format_news(news: list) -> str:
    """格式化为 [情绪标签-板块] 标题 的形式"""
    lines = []
    for n in news[:10]:  # 最多 10 条
        tag = "利好" if n.get("sentiment") == "positive" else "利空"
        lines.append(f"- [{tag}-{n.get('sector', '市场')}] {n['title']}")
    return "\n".join(lines)
```

---

## 🟠 Step 8：API 层——analysis.py

### 8.1 创建 `backend/app/api/v1/analysis.py`

```python
# backend/app/api/v1/analysis.py

"""
V3 量化分析 API
"""

from fastapi import APIRouter, Query
from datetime import date
from app.services.scoring import score_all_sectors
from app.services.report_generator import generate_report
from app.services.leader_identifier import identify_strength_leaders, identify_sentiment_leaders

router = APIRouter(prefix="/api/v1/analysis")


@router.get("/dashboard")
async def get_dashboard(trade_date: date = Query(None, description="日期，默认最新交易日")):
    """
    返回仪表盘所需的全部数据：
    {
        "indices": [...],          // 大盘指数
        "us_indices": [...],       // 美股指数
        "sectors": [...],          // 板块行情（含评分）
        "heatmap_data": [...],     // 热力图专用数据（简化版）
        "top_sectors": [...],      // 领涨 Top 10（含龙头）
        "bottom_sectors": [...],   // 领跌 Top 10
        "margin_data": {...},      // 融资融券
        "report": "markdown..."    // AI 复盘报告
    }
    """
    pass


@router.post("/run-analysis")
async def trigger_analysis(trade_date: date = Query(None)):
    """
    手动触发全部分析流程。
    正常情况由定时任务自动触发，这个接口是手动入口。
    """
    pass


@router.get("/sector/{sector_code}")
async def get_sector_detail(sector_code: str, trade_date: date = Query(None)):
    """
    获取单个板块的详细信息：
    - 评分明细（三项分别多少分）
    - 实力龙头和情绪龙头
    - 相关新闻列表
    - AI 写的板块分析
    """
    pass
```

### 8.2 注册路由

在 `backend/app/main.py` 中加上：
```python
from app.api.v1 import analysis
app.include_router(analysis.router)
```

---

## 🟢 Step 9：定时任务——worker.py + 脚本

### 9.1 创建 `backend/app/worker.py`

```python
# backend/app/worker.py

"""
定时任务调度
使用 APScheduler（项目已依赖）
每天 15:05 开始跑数据流水线
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime

scheduler = AsyncIOScheduler()


@scheduler.scheduled_job("cron", day_of_week="mon-fri", hour=15, minute=5)
async def daily_pipeline():
    """
    每日流水线（按顺序执行，每步完成后再下一步）：
    1. fetch_daily_data    → 拉取今日行情
    2. run_sentiment_crawl → 爬取论坛情绪
    3. run_analysis        → 计算评分 + 龙头
    4. generate_report     → AI 生成报告
    """
    print(f"[{datetime.now()}] 📊 开始今日盘后分析流水线...")
    # Step 1
    # Step 2
    # Step 3
    # Step 4
    print(f"[{datetime.now()}] ✅ 今日分析完成")


def start_scheduler():
    scheduler.start()
```

在 `backend/app/main.py` 的 startup 事件中调用 `start_scheduler()`。

### 9.2 三个独立脚本（也可直接从 worker 调用）

这三个脚本既是独立可执行文件，其核心逻辑也可以被 worker 直接 import 调用。

```python
# backend/scripts/fetch_daily_data.py
"""拉取今日所有行情数据，写入数据库"""
# 调用 market_data.py 的四个 fetch 函数 → 写入对应的 SQL 表

# backend/scripts/run_analysis.py
"""执行全部分析计算"""
# 调用 scoring.score_all_sectors() + leader_identifier 的龙头识别

# backend/scripts/init_v3_db.py
"""首次启动时：建表 + 灌近 30 天历史数据（可选）"""
```

---

## 🔵 Step 10：前端仪表盘

### 10.1 新增 API 调用函数

在 `frontend/src/lib/api.ts` 中新增：

```typescript
// 仪表盘数据
export async function fetchDashboard(date?: string): Promise<DashboardData> {
  const params = date ? `?trade_date=${date}` : "";
  const res = await fetch(`/api/v1/analysis/dashboard${params}`);
  if (!res.ok) throw new Error("Failed to fetch dashboard");
  return res.json();
}
```

### 10.2 创建数据 hook

`frontend/src/hooks/useDashboard.ts`：
```typescript
// 用 TanStack Query 封装仪表盘数据获取
// 支持日期切换（默认今天，可以切到历史日期）
```

### 10.3 四个核心组件

| 组件 | 文件 | 功能 |
|------|------|------|
| **IndexCard** | `frontend/src/components/IndexCard.tsx` | 单个指数卡片：名称、点数、涨跌幅、迷你面积图（近 5 日） |
| **SectorHeatmap** | `frontend/src/components/SectorHeatmap.tsx` | 板块热力图：所有板块的红绿方块网格，颜色深浅表示涨跌幅度，面积统一即可（简单方案） |
| **ScoreRanking** | `frontend/src/components/ScoreRanking.tsx` | Top 10 板块评分条形图，显示三面分数和综合分 |
| **AIReport** | `frontend/src/components/AIReport.tsx` | 渲染 AI 返回的 Markdown 报告（用 `react-markdown` 库） |

### 10.4 仪表盘页面

`frontend/src/pages/DashboardV3.tsx`：

```
布局结构（移动端向下堆叠，桌面端两栏）：

┌────────────────────────────────────────────┐
│ 📊 GFNA 盘后分析 | 2026-07-25     [关于]     │  ← Navbar（已有）
├────────────────────────────────────────────┤
│ [上证] [深证] [创业板] [科创50] [道琼斯]      │  ← 指数卡片行，横向滚动
├──────────────────┬─────────────────────────┤
│  板块热力图       │  Top 10 综合评分          │  ← 左右两栏
│  (红绿方块网格)   │  (条形图 + 龙头信息)       │
├──────────────────┴─────────────────────────┤
│  📝 AI 复盘报告                              │  ← 全宽
│  (Markdown 渲染：大盘综述 → 领涨/领跌 →      │
│   短期 → 中期 → 长期预测)                     │
└────────────────────────────────────────────┘
```

### 10.5 路由注册

在 `App.tsx` 中新增路由：`/dashboard-v3` → `DashboardV3`

---

## 🟣 Step 11（可选）：RAG 知识库

> **这个 Step 不着急做。** 等 Step 1-10 全部跑通、前端能正常展示了，再考虑加。

```python
# backend/app/services/vector_store.py

"""
向量知识库（基于 ChromaDB）

存入内容：每个板块每一天的技术指标摘要
  "{sector_name} {trade_date}: MA5趋势={}, RSI={}, MACD={},
   量比={}, 涨跌幅={}, 融资趋势={}"

检索用途：生成报告时，向量检索最近 3 个月内与当前技术形态最相似的 3 天
  → 塞进 Prompt → AI 可以写"历史上类似形态出现后，该板块在 3 日内 67% 概率上涨"
"""
```

---

## 📊 执行顺序总结

```
Step 1   ████ 数据库   ← 先建表，不改这一步后面全堵住
Step 2   ████ 数据拉取  ← 让行情数据能跑进数据库
Step 3   ███  技术分析  ← 第一个"有点意思"的输出
Step 4   ███  情绪分析  ← 进阶级数据源
Step 5   ███  综合评分  ← 技术+情绪+消息 → 一个分数
Step 6   ██   龙头识别  ← 规则驱动，不复杂
Step 7   ███  AI 报告   ← Prompt 工程是核心
Step 8   ██   API 层    ← 把后端能力暴露给前端
Step 9   ██   定时任务  ← 自动化
Step 10  ████ 前端      ← 用户真正能看到的东西
Step 11  ██   RAG       ← 可选加分项
```

---

## ⚠️ 给执行 AI 的重要提示

1. **不要一次写完所有文件再测试**。每完成一个 Step，立刻验证它能跑通。

2. **AKShare 是国产库，文档不完善**。如果某个接口报错，先 print DataFrame 的列名看看实际返回了什么，再改字段映射。

3. **新闻情感分析是最耗 DeepSeek token 的环节**。初期可以只对当日 Top 5 条重要新闻做分析，不要所有新闻都调一次 API。

4. **前端热力图**：如果 Recharts Treemap 搞不定，直接用 CSS Grid 画方格——一个 `div` 一块，`background-color` 映射涨跌幅，简单粗暴但效果一样好。

5. **所有异步函数（数据库查询、API 调用）别忘了 await**。

6. **工作日判断**：定时任务只周一到周五跑，周末跳过。历史数据回溯也要跳过周末。

7. **默认日期**：所有 API 如果 `trade_date` 参数为空，默认查数据库里最新的交易日数据。

---

> **Built with AI | GFNA V3 | 2026**
