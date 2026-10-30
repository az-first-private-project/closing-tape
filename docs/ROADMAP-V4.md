# 🚀 GFNA V4 — 盘后量化分析终端 + 量化选股算法中心 · AI 执行说明书

> **本文档用途**：给 AI（或任何执行者）看的逐步构建说明书。
> 每一步精确到文件路径、函数签名、数据库字段。按顺序执行即可。
>
> **前提**：后端 FastAPI + PostgreSQL，前端 React + Vite + TypeScript + Tailwind + shadcn/ui 已就绪（V2 成果）。
>
> **V4 新增**：在 V3 盘后分析终端基础上，新增「量化选股算法中心」模块——集成腾讯自选股 `westock-tool` CLI 的 4 个核心量化算法，作为独立页面/API 接入系统。

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
⑥ 执行量化选股算法 (腾讯自选股 westock-tool)
       │
⑦ 前端仪表盘展示：指数卡片 → 板块热力图 → 评分面板 → AI 报告
       │
⑧ 前端算法中心：4 大算法卡片 → 参数可调 → 数据表格 → 可视化图表
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
│   │   │   ├── analysis.py          # [新增] V3 分析仪表盘 API
│   │   │   └── algo.py              # [V4新增] 量化选股算法 API
│   │   ├── db/
│   │   │   ├── models.py            # [已有] 原有 ORM
│   │   │   ├── models_v3.py         # [新增] V3 新增表 ORM
│   │   │   └── models_v4.py         # [V4新增] V4 算法缓存表 ORM
│   │   ├── services/
│   │   │   ├── llm_service.py       # [已有] DeepSeek 调用
│   │   │   ├── market_data.py       # [新增] AKShare + yfinance 数据拉取
│   │   │   ├── technical_analysis.py # [新增] 技术指标计算
│   │   │   ├── sentiment.py         # [新增] 情绪分析（融资+论坛+北向）
│   │   │   ├── scoring.py           # [新增] 三维综合评分
│   │   │   ├── leader_identifier.py # [新增] 龙头识别
│   │   │   ├── report_generator.py  # [新增] AI 报告生成
│   │   │   ├── crawler_service.py   # [已有] 爬虫调度
│   │   │   └── algo_service.py      # [V4新增] 量化算法 CLI 封装
│   │   └── main.py                  # [修改] 注册新路由
│   └── scripts/
│       ├── fetch_daily_data.py      # [新增] 拉取今日行情
│       ├── run_analysis.py          # [新增] 执行全部分析
│       ├── init_v3_db.py            # [新增] 初始化 V3 表结构
│       ├── init_v4_db.py            # [V4新增] 初始化 V4 表结构
│       └── run_algo.py              # [V4新增] 执行量化算法并缓存
├── westock-tool/                    # [V4新增] 腾讯自选股 CLI（原样放这里）
│   └── scripts/
│       └── index.js
├── frontend/
│   └── src/
│       ├── pages/
│       │   ├── DashboardV3.tsx      # [新增] V3 仪表盘主页面
│       │   └── AlgoCenter.tsx       # [V4新增] 量化算法中心页面
│       ├── components/
│       │   ├── IndexCard.tsx        # [新增] 指数卡片
│       │   ├── SectorHeatmap.tsx    # [新增] 板块热力图
│       │   ├── ScoreRanking.tsx     # [新增] 综合评分 Top 10
│       │   ├── AIReport.tsx         # [新增] AI 报告渲染
│       │   ├── AlgoCard.tsx         # [V4新增] 算法卡片
│       │   ├── AlgoDataTable.tsx    # [V4新增] 算法结果表格
│       │   ├── AlgoParamPanel.tsx   # [V4新增] 参数调节面板
│       │   ├── AlgoBarChart.tsx     # [V4新增] 柱状图（资金流）
│       │   └── AlgoRadarChart.tsx   # [V4新增] 雷达图（评分）
│       ├── hooks/
│       │   ├── useDashboard.ts      # [新增] 仪表盘数据 hook
│       │   └── useAlgo.ts           # [V4新增] 算法数据 hook
│       ├── lib/
│       │   ├── api.ts               # [修改] 新增仪表盘 + 算法 API 调用
│       │   └── algo-types.ts        # [V4新增] 算法相关 TypeScript 类型
│       └── App.tsx                  # [修改] 新增路由
└── docs/
    └── ROADMAP-V4.md               # 本文件
```

---

## 🔴 Step 1：数据库——新增 4 张表（V3）

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

**方式 B**：在 `backend/app/db/models.py` 文件末尾 import models_v3：
```python
from app.db.models_v3 import MarketIndex, SectorDaily, MarginTrading, SectorScore
```

### 验证

```bash
psql -U postgres -d gfna -c "\dt"
# 应该看到 market_indices, sector_daily, margin_trading, sector_score 四张新表
```

---

## 🟣 Step 1.5（V4新增）：数据库——算法缓存表

### 执行内容

在 `backend/app/db/models_v4.py` 中创建算法结果缓存表。

### 设计动机

`westock-tool` CLI 每次调用耗时 5~30 秒，且数据是**每日收盘后更新**——没必要每次前端请求都重跑 CLI。缓存到数据库，前端秒开；定时任务每日刷新一次即可。

### 数据模型定义

```python
# backend/app/db/models_v4.py

from sqlalchemy import Column, Integer, String, Date, Numeric, Text, JSON, DateTime, Index
from datetime import datetime
from app.db.base import Base

class AlgoResult(Base):
    """
    量化算法结果缓存表
    每种算法每天一条记录，存原始 JSON 结果 + 元信息
    """
    __tablename__ = "algo_results"
    __table_args__ = (
        Index("ix_algo_results_code_date", "algo_code", "trade_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    algo_code = Column(String(30), nullable=False, comment="算法代码: a1/a2/a3/a4")
    algo_name = Column(String(100), nullable=False, comment="算法名称（中文）")
    trade_date = Column(Date, nullable=False, comment="数据日期")
    raw_data = Column(JSON, nullable=False, comment="算法原始结果（JSON 数组）")
    count = Column(Integer, default=0, comment="信号数量")
    params = Column(JSON, comment="本次运行的参数快照 {pe_max: 50, limit: 20}")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AlgoMeta(Base):
    """
    算法元信息表（静态配置，系统启动时写入）
    前端用来渲染算法卡片：名称、描述、类型、参数 schema
    """
    __tablename__ = "algo_meta"

    id = Column(Integer, primary_key=True, autoincrement=True)
    algo_code = Column(String(30), unique=True, nullable=False)
    algo_name = Column(String(100), nullable=False)
    algo_type = Column(String(20), nullable=False, comment="technical/value/fund/score")
    short_desc = Column(String(200), comment="一句话逻辑说明")
    full_desc = Column(Text, comment="详细说明（Markdown）")
    params_schema = Column(JSON, comment="参数定义 [{key, label, type, default, min, max}]")
    cli_command = Column(String(500), comment="对应的 westock-tool 命令模板")
    is_active = Column(Integer, default=1, comment="是否启用")
```

### 算法元信息种子数据

在 `backend/scripts/init_v4_db.py` 中写入以下种子数据：

```python
ALGO_SEED_DATA = [
    {
        "algo_code": "a1",
        "algo_name": "MACD金叉 × 低估值",
        "algo_type": "technical",
        "short_desc": "技术面刚启动（MACD金叉）+ 估值还便宜 = 戴维斯双击候选",
        "full_desc": "先用 MACD金叉策略筛选出技术面刚启动的股票池（约100只），"
                     "再按估值指标（PE/PB/PS）升序排列，找出估值最低的标的。"
                     "逻辑：技术面确认趋势向上 + 估值提供安全边际。",
        "params_schema": [
            {"key": "limit", "label": "显示数量", "type": "select", "default": 20, "options": [10, 20, 50]},
            {"key": "pe_max", "label": "PE上限", "type": "number", "default": 50, "min": 0, "max": 500},
            {"key": "sort_by", "label": "排序字段", "type": "select", "default": "PE_TTM",
             "options": ["PE_TTM", "PB_LF", "PS_TTM"]},
        ],
        "cli_command": 'node westock-tool/scripts/index.js ranking fin_valuation --within-strategy macd_golden --limit {limit} --raw',
    },
    {
        "algo_code": "a2",
        "algo_name": "均线多头 × 主力5日加仓",
        "algo_type": "fund",
        "short_desc": "均线多头排列 + 主力资金连续净流入 = 趋势资金共振",
        "full_desc": "先用均线多头发散策略筛选趋势向上的股票池（约75只），"
                     "再按近5日主力资金净流入合计降序排列。"
                     "逻辑：均线多头 = 趋势确认，主力加仓 = 真金白银背书。",
        "params_schema": [
            {"key": "limit", "label": "显示数量", "type": "select", "default": 20, "options": [10, 20, 50]},
            {"key": "min_main_5d", "label": "5日净流入下限(万)", "type": "number", "default": 0, "min": -100000, "max": 1000000},
        ],
        "cli_command": 'node westock-tool/scripts/index.js ranking cap_main_5d --within-strategy ma_long --limit {limit} --raw',
    },
    {
        "algo_code": "a3",
        "algo_name": "央企 × 综合评分TOP",
        "algo_type": "score",
        "short_desc": "央企属性过滤 + 腾讯自选股四维评分 = 优质国企筛选",
        "full_desc": "在央企标签股票池（约520只）中，按腾讯自选股综合评分降序排列。"
                     "综合评分 = 资金面(25%) + 基本面(25%) + 技术面(25%) + 风险面(25%) 加权。"
                     "适合寻找稳健型、低暴雷风险的国企标的。",
        "params_schema": [
            {"key": "limit", "label": "显示数量", "type": "select", "default": 20, "options": [10, 20, 50]},
            {"key": "min_score", "label": "最低综合评分", "type": "number", "default": 70, "min": 0, "max": 100},
            {"key": "label", "label": "股票标签", "type": "select", "default": "shareholder_central_state",
             "options": ["shareholder_central_state", "shareholder_local_state", "valuation_lowpb"]},
        ],
        "cli_command": 'node westock-tool/scripts/index.js ranking CompScore --within-label {label} --limit {limit} --raw',
    },
    {
        "algo_code": "a4",
        "algo_name": "高股息 × 低估值",
        "algo_type": "value",
        "short_desc": "高股息率 + 低PE = 红利防守型价值选股",
        "full_desc": "筛选 PE_TTM > 0 且低于阈值（默认15）、股息率高于阈值（默认4%）的股票。"
                     "适合防守型配置、养老金/险资风格、震荡市/熊市底部区域。",
        "params_schema": [
            {"key": "limit", "label": "显示数量", "type": "select", "default": 20, "options": [10, 20, 50]},
            {"key": "pe_max", "label": "PE上限", "type": "number", "default": 15, "min": 0, "max": 100},
            {"key": "div_min", "label": "股息率下限(%)", "type": "number", "default": 4, "min": 0, "max": 20},
            {"key": "sort_by", "label": "排序字段", "type": "select", "default": "DividendRatioTTM",
             "options": ["DividendRatioTTM", "PE_TTM"]},
        ],
        "cli_command": 'node westock-tool/scripts/index.js filter "intersect([PE_TTM > 0, PE_TTM < {pe_max}, DividendRatioTTM > {div_min}])" --by {sort_by} --desc --limit {limit} --raw',
    },
]
```

### 注册新模型

在 `backend/app/main.py` 或 `models.py` 中加上：
```python
from app.db.models_v4 import AlgoResult, AlgoMeta
```

### 验证

```bash
psql -U postgres -d gfna -c "\dt"
# 应新增 algo_results, algo_meta 两张表
```

---

## 🟠 Step 2：数据拉取层——market_data.py

### 2.1 安装依赖

```bash
cd backend
pip install akshare yfinance pandas numpy
```

### 2.2 创建 `backend/app/services/market_data.py`

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
    pass

# ============================================================
# A 股 — 板块行情（热力图数据源）
# ============================================================

def fetch_sector_boards(trade_date: Optional[date] = None) -> list[dict]:
    """
    拉取东方财富板块行情（概念板块 + 行业板块）。
    返回: [{"sector_code": "BK1186", "sector_name": "半导体",
             "change_pct": 3.2, ...}, ...]
    """
    pass

# ============================================================
# A 股 — 融资融券 + 北向资金
# ============================================================

def fetch_margin_trading(trade_date: Optional[date] = None) -> dict:
    """融资融券余额 + 北向资金"""
    pass

# ============================================================
# 美股 — 三大指数
# ============================================================

US_INDEX_SYMBOLS = {"^DJI": "道琼斯工业", "^GSPC": "标普500", "^IXIC": "纳斯达克"}

def fetch_us_indices() -> list[dict]:
    """通过 yfinance 拉取美股三大指数"""
    pass

# ============================================================
# A 股 — 板块成分股（辅助：龙头识别需要）
# ============================================================

def fetch_sector_stocks(sector_code: str) -> list[dict]:
    """拉取某个板块的成分股列表及今日行情"""
    pass
```

### ⚠️ 关键注意事项

1. **AKShare 接口偶尔变动**：每个取数函数外面用 `try/except` 包一层，失败了打印日志、返回空列表或 None。
2. **交易日判断**：周末/节假日返回最近交易日数据。
3. **yfinance 美股数据**：用 `yf.Ticker(symbol).history(period="2d")`，取倒数第二行。
4. **板块数量很多**（400+）：全拉回来，前端只取涨跌幅绝对值最大的前 50 个做热力图。

---

## 🟡 Step 3：技术分析——technical_analysis.py

### 3.1 创建 `backend/app/services/technical_analysis.py`

```python
# backend/app/services/technical_analysis.py

"""
技术指标计算（手写，不引入 ta 库）
每个板块取近 30 个交易日的收盘价序列做计算。
"""

import pandas as pd
import numpy as np
from typing import Optional

def calc_ma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window).mean()

def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = series.ewm(span=fast).mean()
    ema_slow = series.ewm(span=slow).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def score_sector_technical(close_prices: pd.Series, volumes: pd.Series) -> float:
    """
    技术面评分 (0-10)
    - 趋势分(40%): 当前价 vs MA5/MA10/MA20
    - 动量分(30%): RSI 40-70 + MACD金叉
    - 量能分(30%): 近5日均量 vs 20日均量
    """
    if len(close_prices) < 20:
        return 5.0

    ma5 = calc_ma(close_prices, 5).iloc[-1]
    ma10 = calc_ma(close_prices, 10).iloc[-1]
    ma20 = calc_ma(close_prices, 20).iloc[-1]
    last_price = close_prices.iloc[-1]

    trend_raw = sum((last_price - ma) / ma * 100 for ma in [ma5, ma10, ma20])
    trend_score = min(10, max(0, 5 + trend_raw * 0.5))

    rsi = calc_rsi(close_prices).iloc[-1]
    momentum_score = 7.0 if 40 <= rsi <= 70 else (5.0 if 30 <= rsi <= 80 else 3.0)
    macd_line, signal_line, _ = calc_macd(close_prices)
    if macd_line.iloc[-1] > signal_line.iloc[-1] and macd_line.iloc[-2] <= signal_line.iloc[-2]:
        momentum_score += 1.5

    vol_5d_avg = volumes.iloc[-5:].mean()
    vol_20d_avg = volumes.iloc[-20:].mean()
    vol_ratio = vol_5d_avg / vol_20d_avg if vol_20d_avg > 0 else 1.0
    volume_score = min(10, max(0, 5 + (vol_ratio - 1) * 5))

    composite = trend_score * 0.4 + momentum_score * 0.3 + volume_score * 0.3
    return round(min(10, max(0, composite)), 2)
```

### 验证

```python
prices = pd.Series([100 + i * 0.5 + (i % 5) for i in range(30)])
volumes = pd.Series([1000000 for _ in range(30)])
score = score_sector_technical(prices, volumes)
print(f"技术面评分: {score}")  # 0~10
```

---

## 🟢 Step 4：情绪分析——sentiment.py

### 4.1 创建 `backend/app/services/sentiment.py`

```python
# backend/app/services/sentiment.py

"""
市场情绪分析
数据来源：融资融券 / 北向资金 / 论坛讨论热度 / 板块主力资金流向
"""

def calc_margin_sentiment(margin_data: dict) -> float:
    """融资情绪分 (0-10)：连续3日增加→高分，连续下降→低分"""
    pass

def calc_northward_sentiment(northward_net: float) -> float:
    """北向资金情绪分 (0-10)：净流入>50亿→8+，净流出>50亿→<4"""
    pass

def calc_forum_heat_sentiment(sector_code: str, trade_date) -> float:
    """论坛热度情绪分 (0-10)：热度暴增→高分，暴跌恐慌→低分"""
    pass

def calc_sector_sentiment(sector_code: str, trade_date, margin_data: dict,
                          northward_net: float) -> float:
    """综合情绪面评分 (0-10)：融资25% + 北向25% + 论坛25% + 资金流25%"""
    pass
```

---

## 🔵 Step 5：综合评分——scoring.py

### 5.1 创建 `backend/app/services/scoring.py`

```python
# backend/app/services/scoring.py

"""
板块综合评分：技术面 + 情绪面 + 消息面 → 综合评分
"""

from app.services.technical_analysis import score_sector_technical
from app.services.sentiment import calc_sector_sentiment

def score_news_for_sector(sector_name: str, trade_date) -> float:
    """消息面评分 (0-10)：调 DeepSeek 判断每条新闻情感，加权汇总"""
    pass

def compute_composite(technical: float, sentiment: float, news: float) -> float:
    """综合评分 = 技术面(40%) + 情绪面(35%) + 消息面(25%)"""
    return round(technical * 0.4 + sentiment * 0.35 + news * 0.25, 2)

def score_all_sectors(trade_date) -> list[dict]:
    """
    对当天所有板块计算完整评分，写入 sector_score 表。
    返回: [{"sector_code": "BK1186", "sector_name": "半导体",
             "technical_score": 8.5, "sentiment_score": 7.2,
             "news_score": 8.0, "composite_score": 7.9}, ...]
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
    实力龙头：市值前10 → 日均成交额前3
    返回: [{"code": "688981", "name": "中芯国际", "market_cap": 4500, ...}, ...]
    """
    pass

def identify_sentiment_leaders(sector_code: str, trade_date) -> list[dict]:
    """
    情绪龙头：50亿<市值<2000亿 → (涨幅×换手率)前2
    返回: [{"code": "688256", "name": "寒武纪", "change_pct": 5.8, ...}, ...]
    """
    pass
```

---

## 🔴 Step 7：AI 报告生成——report_generator.py

### 7.1 创建 `backend/app/services/report_generator.py`

```python
# backend/app/services/report_generator.py

"""
AI 复盘报告生成（DeepSeek API）
将量化分析结果组装成结构化 Prompt → 调 DeepSeek → 存 Markdown
"""

import json
from app.services.llm_service import chat

def build_report_prompt(trade_date, market_data: dict, top_sectors: list,
                        bottom_sectors: list, margin_data: dict,
                        related_news: list) -> str:
    """组装结构化 Prompt（含大盘/领涨/领跌/资金/新闻五段）"""
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
（150 字以内）

## 领涨板块分析
## 领跌板块分析
## 短期走势预测（1-5 个交易日）
## 中期走势预测（1-4 周）
## 长期走势预测（1-6 个月）

---
注意：所有判断必须引用具体数据；预测部分标注"此为 AI 推测，不构成投资建议"；语气专业克制。
"""
    return prompt

def generate_report(trade_date) -> str:
    """主流程：读数据 → 组装Prompt → 调DeepSeek → 存数据库 → 返回Markdown"""
    pass

def _format_indices(market_data): ...
def _format_sectors(sectors): ...
def _format_news(news): ...
```

---

## 🟠 Step 8：API 层——analysis.py + algo.py

### 8.1 创建 `backend/app/api/v1/analysis.py`（V3 仪表盘 API）

```python
# backend/app/api/v1/analysis.py

from fastapi import APIRouter, Query
from datetime import date

router = APIRouter(prefix="/api/v1/analysis")

@router.get("/dashboard")
async def get_dashboard(trade_date: date = Query(None)):
    """
    返回仪表盘全部数据：
    { indices, us_indices, sectors, heatmap_data, top_sectors,
      bottom_sectors, margin_data, report }
    """
    pass

@router.post("/run-analysis")
async def trigger_analysis(trade_date: date = Query(None)):
    """手动触发全部分析流程"""
    pass

@router.get("/sector/{sector_code}")
async def get_sector_detail(sector_code: str, trade_date: date = Query(None)):
    """单个板块详情：评分明细 + 龙头 + 新闻 + AI分析"""
    pass
```

### 8.2（V4新增）创建 `backend/app/api/v1/algo.py`

```python
# backend/app/api/v1/algo.py

"""
V4 量化选股算法 API
所有算法通过调用 algo_service 执行 westock-tool CLI
结果优先读缓存（algo_results 表），缓存未命中或强制刷新时重跑 CLI
"""

from fastapi import APIRouter, Query, HTTPException
from typing import Optional
from app.services.algo_service import run_algo, get_cached_result, list_algos

router = APIRouter(prefix="/api/v1/algo")

@router.get("/list")
async def get_algo_list():
    """
    返回所有可用算法清单（含名称、描述、参数 schema）。
    前端算法中心首页用这个接口渲染 4 张卡片。
    """
    return await list_algos()

@router.get("/{algo_code}")
async def get_algo_result(
    algo_code: str,
    trade_date: Optional[date] = Query(None, description="日期，默认最新"),
    limit: int = Query(20, ge=1, le=100),
    force_refresh: bool = Query(False, description="强制重跑CLI，忽略缓存"),
    # A1 专属
    pe_max: Optional[int] = Query(None),
    sort_by: Optional[str] = Query(None),
    # A2 专属
    min_main_5d: Optional[float] = Query(None),
    # A3 专属
    min_score: Optional[float] = Query(None),
    label: Optional[str] = Query(None),
    # A4 专属
    div_min: Optional[float] = Query(None),
):
    """
    获取某个算法的执行结果。
    - 先查 algo_results 缓存表
    - 缓存命中且未强制刷新 → 直接返回
    - 缓存未命中或强制刷新 → 调 algo_service 跑 CLI → 写缓存 → 返回
    """
    params = {k: v for k, v in {
        "limit": limit, "pe_max": pe_max, "sort_by": sort_by,
        "min_main_5d": min_main_5d, "min_score": min_score,
        "label": label, "div_min": div_min,
    }.items() if v is not None}

    result = await get_cached_result(algo_code, trade_date)
    if result and not force_refresh:
        return result

    return await run_algo(algo_code, trade_date, params)

@router.post("/{algo_code}/refresh")
async def refresh_algo(algo_code: str, params: dict = {}):
    """强制重跑某个算法"""
    return await run_algo(algo_code, None, params, force=True)

@router.get("/health")
async def algo_health():
    """检查 westock-tool CLI 是否可用"""
    pass
```

### 8.3 注册路由

在 `backend/app/main.py` 中加上：
```python
from app.api.v1 import analysis, algo
app.include_router(analysis.router)
app.include_router(algo.router)  # V4 新增
```

---

## 🔵 Step 8.5（V4新增）：算法服务层——algo_service.py

这是 V4 最核心的新文件。它封装了所有 `westock-tool` CLI 的调用、参数校验、缓存读写、超时控制。

### 8.5.1 创建 `backend/app/services/algo_service.py`

```python
# backend/app/services/algo_service.py

"""
量化选股算法服务层
封装腾讯自选股 westock-tool CLI 的调用、缓存管理、参数校验

数据源：腾讯自选股数据接口（通过 westock-tool Node.js CLI 调用）
数据更新频率：每日收盘后更新
"""

import asyncio
import json
import os
import re
from datetime import date, datetime
from typing import Optional, Any

from sqlalchemy.orm import Session
from app.db.models_v4 import AlgoResult, AlgoMeta
from app.db.session import get_db

# ============================================================
# 配置
# ============================================================

# westock-tool CLI 路径（通过环境变量注入，禁止硬编码）
WESTOCK_TOOL_PATH = os.getenv(
    "WESTOCK_TOOL_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "westock-tool", "scripts", "index.js")
)
ALGO_TIMEOUT = 60  # CLI 调用超时（秒）
ALGO_DEFAULT_LIMIT = 20

# 算法代码 → westock-tool 命令模板映射
# {} 占位符由 build_command() 填充
ALGO_COMMAND_MAP = {
    "a1": {
        "type": "ranking",
        "base_cmd": "ranking fin_valuation --within-strategy macd_golden",
        "params": ["limit", "pe_max", "sort_by"],
        "sort_field_map": {
            "PE_TTM": "PE_TTM",   # 升序（默认）
            "PB_LF": "PB_LF",
            "PS_TTM": "PS_TTM",
        },
        "default_sort": "PE_TTM",
        "asc": True,  # 估值最低 = 升序
    },
    "a2": {
        "type": "ranking",
        "base_cmd": "ranking cap_main_5d --within-strategy ma_long",
        "params": ["limit", "min_main_5d"],
        "default_sort": "MainSum5d",
        "asc": False,  # 主力净流入最高 = 降序
    },
    "a3": {
        "type": "ranking_label",
        "base_cmd": "ranking CompScore --within-label {label}",
        "params": ["limit", "min_score", "label"],
        "default_label": "shareholder_central_state",
        "default_sort": "CompScore",
        "asc": False,
    },
    "a4": {
        "type": "filter",
        "base_cmd": 'filter "intersect([PE_TTM > 0, PE_TTM < {pe_max}, DividendRatioTTM > {div_min}])"',
        "params": ["limit", "pe_max", "div_min", "sort_by"],
        "default_pe_max": 15,
        "default_div_min": 4,
        "default_sort": "DividendRatioTTM",
        "asc": False,
    },
}


# ============================================================
# 核心函数
# ============================================================

async def run_algo(algo_code: str, trade_date: Optional[date],
                   params: dict, force: bool = False) -> dict:
    """
    执行某个量化算法：
    1. 校验 algo_code 合法
    2. 构建 CLI 命令
    3. 异步执行（带超时）
    4. 解析 JSON 输出
    5. 写入缓存表
    6. 返回结构化结果
    """
    if algo_code not in ALGO_COMMAND_MAP:
        raise ValueError(f"未知算法: {algo_code}，可用: {list(ALGO_COMMAND_MAP.keys())}")

    trade_date = trade_date or date.today()
    cmd_config = ALGO_COMMAND_MAP[algo_code]

    # 填充参数默认值
    params = _fill_defaults(algo_code, params)

    # 构建命令
    command = _build_command(algo_code, params)

    # 执行 CLI
    try:
        raw_json = await _exec_cli(command, timeout=ALGO_TIMEOUT)
    except asyncio.TimeoutError:
        raise RuntimeError(f"算法 {algo_code} 执行超时（{ALGO_TIMEOUT}s）")
    except Exception as e:
        raise RuntimeError(f"算法 {algo_code} 执行失败: {e}")

    # 解析 + 清洗数据
    data = _parse_and_clean(raw_json, algo_code)

    # 写缓存
    await _cache_result(algo_code, trade_date, data, params)

    return {
        "algo": algo_code,
        "trade_date": trade_date.isoformat(),
        "count": len(data),
        "params": params,
        "data": data,
        "from_cache": False,
    }


async def get_cached_result(algo_code: str, trade_date: Optional[date]) -> Optional[dict]:
    """
    从 algo_results 表读取缓存。
    命中条件：algo_code + trade_date 匹配 + raw_data 非空
    """
    trade_date = trade_date or date.today()
    db: Session = next(get_db())
    try:
        record = db.query(AlgoResult).filter(
            AlgoResult.algo_code == algo_code,
            AlgoResult.trade_date == trade_date,
        ).first()
        if record and record.raw_data:
            return {
                "algo": algo_code,
                "trade_date": trade_date.isoformat(),
                "count": record.count,
                "params": record.params or {},
                "data": record.raw_data,
                "from_cache": True,
                "cached_at": record.updated_at.isoformat() if record.updated_at else None,
            }
    finally:
        db.close()
    return None


async def list_algos() -> list[dict]:
    """返回所有可用算法清单（含名称、描述、参数 schema）"""
    db: Session = next(get_db())
    try:
        metas = db.query(AlgoMeta).filter(AlgoMeta.is_active == 1).all()
        if not metas:
            # 元信息表为空时，从种子数据返回
            return _seed_list_fallback()
        return [
            {
                "algo_code": m.algo_code,
                "algo_name": m.algo_name,
                "algo_type": m.algo_type,
                "short_desc": m.short_desc,
                "full_desc": m.full_desc,
                "params_schema": m.params_schema,
                "is_active": m.is_active,
            }
            for m in metas
        ]
    finally:
        db.close()


# ============================================================
# 内部辅助函数
# ============================================================

def _fill_defaults(algo_code: str, params: dict) -> dict:
    """为缺失参数填充默认值"""
    defaults = {
        "limit": ALGO_DEFAULT_LIMIT,
        "pe_max": 50,
        "sort_by": "PE_TTM",
        "min_main_5d": 0,
        "min_score": 70,
        "label": "shareholder_central_state",
        "div_min": 4,
    }
    return {**defaults, **params}


def _build_command(algo_code: str, params: dict) -> str:
    """
    根据算法配置 + 参数构建完整 CLI 命令。
    安全注意：所有参数必须白名单校验，防止命令注入。
    """
    config = ALGO_COMMAND_MAP[algo_code]
    base = config["base_cmd"]

    # 安全校验：limit 范围
    limit = max(1, min(int(params.get("limit", 20)), 100))

    if algo_code == "a1":
        sort_field = params.get("sort_by", "PE_TTM")
        # 白名单校验
        allowed_sorts = ["PE_TTM", "PB_LF", "PS_TTM"]
        sort_field = sort_field if sort_field in allowed_sorts else "PE_TTM"
        cmd = f"node {WESTOCK_TOOL_PATH} {base} --limit {limit} --raw"
        # 注：westock-tool ranking 默认按指标升序，符合"估值最低"语义
        return cmd

    elif algo_code == "a2":
        cmd = f"node {WESTOCK_TOOL_PATH} {base} --limit {limit} --raw"
        return cmd

    elif algo_code == "a3":
        label = params.get("label", "shareholder_central_state")
        allowed_labels = [
            "shareholder_central_state", "shareholder_local_state",
            "valuation_lowpb", "valuation_negpe", "fin_high_roettm",
        ]
        label = label if label in allowed_labels else "shareholder_central_state"
        # base_cmd 中有 {label} 占位符
        base_with_label = base.format(label=label)
        cmd = f"node {WESTOCK_TOOL_PATH} {base_with_label} --limit {limit} --raw"
        return cmd

    elif algo_code == "a4":
        pe_max = max(1, min(int(params.get("pe_max", 15)), 500))
        div_min = max(0, min(float(params.get("div_min", 4)), 20))
        sort_by = params.get("sort_by", "DividendRatioTTM")
        allowed_sorts = ["DividendRatioTTM", "PE_TTM"]
        sort_by = sort_by if sort_by in allowed_sorts else "DividendRatioTTM"
        # base_cmd 中有 {pe_max} 和 {div_min} 占位符
        base_filled = base.format(pe_max=pe_max, div_min=div_min)
        cmd = f"node {WESTOCK_TOOL_PATH} {base_filled} --by {sort_by} --desc --limit {limit} --raw"
        return cmd

    raise ValueError(f"未支持的算法: {algo_code}")


async def _exec_cli(command: str, timeout: int = 60) -> str:
    """
    异步执行 CLI 命令，返回 stdout 文本。
    使用 asyncio.create_subprocess_shell 避免阻塞事件循环。
    """
    # 安全：禁止 shell 注入字符
    if any(c in command for c in [';', '&', '|', '`', '$', '>', '<']):
        raise ValueError("命令包含非法字符")

    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise

    if proc.returncode != 0:
        err_msg = stderr.decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"CLI 返回非零: {err_msg}")

    return stdout.decode("utf-8", errors="replace")


def _parse_and_clean(raw_json: str, algo_code: str) -> list[dict]:
    """
    解析 westock-tool --raw 输出的 JSON，清洗为统一格式。
    注意：westock-tool 的 raw JSON 字段名是中文（如 "代码"、"名称"），
    这里统一转为英文 snake_case。
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        # 有时 westock-tool 会在 JSON 前后打印日志行，尝试提取 JSON 数组
        match = re.search(r'\[.*\]', raw_json, re.DOTALL)
        if match:
            data = json.loads(match.group())
        else:
            raise RuntimeError("无法解析 CLI 输出为 JSON")

    if not isinstance(data, list):
        # 有些命令返回 { "data": [...] } 结构
        data = data.get("data", data.get("result", []))

    # 字段名映射（westock-tool 中文 → 英文）
    field_map = {
        "代码": "code", "名称": "name",
        "综合评分": "composite_score", "资金评分": "capital_score",
        "基本面评分": "fundamental_score", "风险评分": "risk_score",
        "技术评分": "technical_score",
        "PE_TTM": "pe_ttm", "PB_LF": "pb_lf", "PS_TTM": "ps_ttm",
        "PCF_TTM": "pcf_ttm", "DIV_TTM": "div_ttm",
        "DividendRatioTTM": "dividend_ratio_ttm",
        "ClosePrice": "close_price", "ChangePCT": "change_pct",
        "MainSum5d": "main_sum_5d",
        "MainT0": "main_t0", "MainT1": "main_t1",
        "MainT2": "main_t2", "MainT3": "main_t3", "MainT4": "main_t4",
    }

    cleaned = []
    for item in data:
        new_item = {}
        for k, v in item.items():
            new_key = field_map.get(k, k)
            new_item[new_key] = v
        # 统一 code 格式：去掉 sh/sz 前缀中的数字代码提取
        if "code" in new_item and isinstance(new_item["code"], str):
            new_item["code_raw"] = new_item["code"]
            new_item["code_num"] = re.sub(r'[^0-9]', '', new_item["code"])
        cleaned.append(new_item)

    return cleaned


async def _cache_result(algo_code: str, trade_date: date, data: list, params: dict):
    """写入 algo_results 缓存表（upsert 语义）"""
    db: Session = next(get_db())
    try:
        existing = db.query(AlgoResult).filter(
            AlgoResult.algo_code == algo_code,
            AlgoResult.trade_date == trade_date,
        ).first()

        if existing:
            existing.raw_data = data
            existing.count = len(data)
            existing.params = params
            existing.updated_at = datetime.utcnow()
        else:
            record = AlgoResult(
                algo_code=algo_code,
                algo_name=_get_algo_name(algo_code),
                trade_date=trade_date,
                raw_data=data,
                count=len(data),
                params=params,
            )
            db.add(record)
        db.commit()
    finally:
        db.close()


def _get_algo_name(algo_code: str) -> str:
    names = {
        "a1": "MACD金叉×低估值", "a2": "均线多头×主力5日加仓",
        "a3": "央企×综合评分TOP", "a4": "高股息×低估值",
    }
    return names.get(algo_code, algo_code)


def _seed_list_fallback() -> list[dict]:
    """algo_meta 表为空时的兜底数据"""
    from backend.scripts.init_v4_db import ALGO_SEED_DATA
    return [
        {
            "algo_code": a["algo_code"],
            "algo_name": a["algo_name"],
            "algo_type": a["algo_type"],
            "short_desc": a["short_desc"],
            "full_desc": a["full_desc"],
            "params_schema": a["params_schema"],
            "is_active": 1,
        }
        for a in ALGO_SEED_DATA
    ]
```

### 8.5.2 安全要点（⚠️ 写给执行者）

1. **命令注入防护**：`_exec_cli` 中已禁止 `; & | ` > <` 等 shell 元字符。所有参数通过白名单校验。
2. **超时控制**：CLI 调用必须设 60s 超时，防止 westock-tool 卡死拖垮后端。
3. **路径不硬编码**：`WESTOCK_TOOL_PATH` 通过环境变量注入，默认值为相对路径。
4. **数据不入库全文**：只缓存 JSON 结果，不存原始 HTML/文本。

---

## 🟢 Step 9：定时任务——worker.py + 脚本

### 9.1 创建 `backend/app/worker.py`

```python
# backend/app/worker.py

"""
定时任务调度（APScheduler）
每天 15:05 开始跑数据流水线
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime

scheduler = AsyncIOScheduler()

@scheduler.scheduled_job("cron", day_of_week="mon-fri", hour=15, minute=5)
async def daily_pipeline():
    """
    每日流水线：
    1. fetch_daily_data    → 拉取今日行情
    2. run_sentiment_crawl → 爬取论坛情绪
    3. run_analysis        → 计算评分 + 龙头
    4. generate_report     → AI 生成报告
    5. run_all_algos       → [V4] 执行全部量化算法并缓存
    """
    print(f"[{datetime.now()}] 📊 开始今日盘后分析流水线...")
    # Step 1-4 (V3 原有)
    # Step 5 (V4 新增)
    from app.services.algo_service import run_algo
    for algo_code in ["a1", "a2", "a3", "a4"]:
        try:
            await run_algo(algo_code, None, {})
            print(f"  ✅ 算法 {algo_code} 完成")
        except Exception as e:
            print(f"  ⚠️ 算法 {algo_code} 失败: {e}")
    print(f"[{datetime.now()}] ✅ 今日分析完成")

def start_scheduler():
    scheduler.start()
```

在 `backend/app/main.py` 的 startup 事件中调用 `start_scheduler()`。

### 9.2 独立脚本

```python
# backend/scripts/fetch_daily_data.py  → 拉取行情写入数据库
# backend/scripts/run_analysis.py     → 执行全部分析
# backend/scripts/init_v3_db.py       → 初始化 V3 表结构
# backend/scripts/init_v4_db.py       → [V4] 初始化 V4 表 + 写入算法种子数据
# backend/scripts/run_algo.py         → [V4] CLI 入口：执行指定算法
```

`backend/scripts/run_algo.py` 示例：
```python
"""
CLI 入口：手动执行某个量化算法
用法：python -m scripts.run_algo a1 --limit 20 --pe-max 50
"""
import asyncio
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.algo_service import run_algo

parser = argparse.ArgumentParser(description="执行量化选股算法")
parser.add_argument("algo_code", choices=["a1", "a2", "a3", "a4"])
parser.add_argument("--limit", type=int, default=20)
parser.add_argument("--pe-max", type=int, default=50)
parser.add_argument("--div-min", type=float, default=4)
parser.add_argument("--min-score", type=float, default=70)
parser.add_argument("--label", default="shareholder_central_state")
args = parser.parse_args()

params = {
    "limit": args.limit,
    "pe_max": args.pe_max,
    "div_min": args.div_min,
    "min_score": args.min_score,
    "label": args.label,
}

result = asyncio.run(run_algo(args.algo_code, None, params))
print(f"算法: {result['algo']}")
print(f"日期: {result['trade_date']}")
print(f"信号数: {result['count']}")
for i, item in enumerate(result["data"][:5], 1):
    print(f"  {i}. {item.get('name', '?')} ({item.get('code_num', '?')})")
```

---

## 🔵 Step 10：前端仪表盘（V3 部分）

### 10.1 新增 API 调用函数

在 `frontend/src/lib/api.ts` 中新增：

```typescript
// V3 仪表盘数据
export async function fetchDashboard(date?: string): Promise<DashboardData> {
  const params = date ? `?trade_date=${date}` : "";
  const res = await fetch(`/api/v1/analysis/dashboard${params}`);
  if (!res.ok) throw new Error("Failed to fetch dashboard");
  return res.json();
}

// V4 算法中心 API
export async function fetchAlgoList(): Promise<AlgoMeta[]> {
  const res = await fetch(`/api/v1/algo/list`);
  if (!res.ok) throw new Error("Failed to fetch algo list");
  return res.json();
}

export async function fetchAlgoResult(
  algoCode: string,
  params: AlgoParams = {},
  forceRefresh = false
): Promise<AlgoResult> {
  const query = new URLSearchParams({
    ...params,
    force_refresh: String(forceRefresh),
  } as Record<string, string>).toString();
  const res = await fetch(`/api/v1/algo/${algoCode}?${query}`);
  if (!res.ok) throw new Error(`Failed to fetch algo ${algoCode}`);
  return res.json();
}
```

### 10.2 TypeScript 类型定义

`frontend/src/lib/algo-types.ts`：

```typescript
// 算法元信息
export interface AlgoMeta {
  algo_code: "a1" | "a2" | "a3" | "a4";
  algo_name: string;
  algo_type: "technical" | "value" | "fund" | "score";
  short_desc: string;
  full_desc: string;
  params_schema: AlgoParamDef[];
  is_active: number;
}

export interface AlgoParamDef {
  key: string;
  label: string;
  type: "number" | "select" | "text";
  default: any;
  min?: number;
  max?: number;
  options?: any[];
}

// 算法执行结果
export interface AlgoResult {
  algo: string;
  trade_date: string;
  count: number;
  params: Record<string, any>;
  data: AlgoStock[];
  from_cache: boolean;
  cached_at?: string | null;
}

// 股票数据（字段按算法不同有差异，用可选属性）
export interface AlgoStock {
  code: string;
  code_num?: string;
  name: string;
  // A1: 估值
  pe_ttm?: number;
  pb_lf?: number;
  ps_ttm?: number;
  pcf_ttm?: number;
  div_ttm?: number;
  // A2: 资金
  main_sum_5d?: number;
  main_t0?: number;
  main_t1?: number;
  main_t2?: number;
  main_t3?: number;
  main_t4?: number;
  // A3: 评分
  composite_score?: number;
  capital_score?: number;
  fundamental_score?: number;
  risk_score?: number;
  technical_score?: number;
  // A4: 股息
  dividend_ratio_ttm?: number;
  close_price?: number;
  change_pct?: number;
}

// 查询参数
export interface AlgoParams {
  limit?: number;
  pe_max?: number;
  sort_by?: string;
  min_main_5d?: number;
  min_score?: number;
  label?: string;
  div_min?: number;
}
```

### 10.3 四个核心 V3 组件

| 组件 | 文件 | 功能 |
|------|------|------|
| **IndexCard** | `frontend/src/components/IndexCard.tsx` | 指数卡片：名称、点数、涨跌幅、迷你面积图 |
| **SectorHeatmap** | `frontend/src/components/SectorHeatmap.tsx` | 板块热力图：红绿方块网格 |
| **ScoreRanking** | `frontend/src/components/ScoreRanking.tsx` | Top 10 板块评分条形图 |
| **AIReport** | `frontend/src/components/AIReport.tsx` | AI 报告 Markdown 渲染 |

### 10.4 V3 仪表盘页面

`frontend/src/pages/DashboardV3.tsx`：

```
┌────────────────────────────────────────────┐
│ 📊 GFNA 盘后分析 | 2026-07-25     [关于]     │
├────────────────────────────────────────────┤
│ [上证] [深证] [创业板] [科创50] [道琼斯]      │
├──────────────────┬─────────────────────────┤
│  板块热力图       │  Top 10 综合评分          │
├──────────────────┴─────────────────────────┤
│  📝 AI 复盘报告                              │
└────────────────────────────────────────────┘
```

---

## 🟣 Step 10.5（V4新增）：前端算法中心

### 10.5.1 数据 Hook

`frontend/src/hooks/useAlgo.ts`：

```typescript
import { useQuery } from "@tanstack/react-query";
import { fetchAlgoList, fetchAlgoResult } from "../lib/api";
import type { AlgoParams } from "../lib/algo-types";

// 获取算法清单
export function useAlgoList() {
  return useQuery({
    queryKey: ["algo-list"],
    queryFn: fetchAlgoList,
    staleTime: 5 * 60 * 1000, // 5分钟内不重新请求
  });
}

// 获取某个算法的结果
export function useAlgoResult(algoCode: string, params: AlgoParams, forceRefresh = false) {
  return useQuery({
    queryKey: ["algo-result", algoCode, params, forceRefresh],
    queryFn: () => fetchAlgoResult(algoCode, params, forceRefresh),
    staleTime: 60 * 60 * 1000, // 1小时（数据每日更新）
    refetchOnWindowFocus: false,
  });
}
```

### 10.5.2 算法中心主页面

`frontend/src/pages/AlgoCenter.tsx`：

```
布局结构（桌面端两栏，移动端堆叠）：

┌──────────────────────────────────────────────────────┐
│ 🧮 量化选股算法中心 | 数据日期: 2026-07-26  [刷新全部] │
├──────────────────────────────────────────────────────┤
│ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐        │
│ │ A1卡片  │ │ A2卡片  │ │ A3卡片  │ │ A4卡片  │        │
│ │MACD金叉 │ │均线多头 │ │央企评分 │ │高股息  │        │
│ │信号:99  │ │信号:75  │ │信号:521 │ │信号:10 │        │
│ │[查看]   │ │[查看]   │ │[查看]   │ │[查看]   │        │
│ └────────┘ └────────┘ └────────┘ └────────┘        │
├──────────────────┬───────────────────────────────────┤
│ 参数面板          │ 数据表格 + 可视化                  │
│ (随选中算法变化)  │ (柱状图/雷达图/条形图)             │
│ - PE上限 [50]    │                                   │
│ - 排序 [PE_TTM▾] │  # 表格（可排序）                  │
│ - 数量 [20▾]     │  | 代码 | 名称 | PE | PB | PS |   │
│ [🔄 执行]        │  |------|------|----|----|----|   │
│                  │  | 603023 | 威帝 |19776|2.73|11.25| │
│                  │  | ...  |      |    |    |    |   │
├──────────────────┴───────────────────────────────────┤
│ ⚠️ 免责声明：数据来自腾讯自选股公开接口，仅作客观展示  │
│    不构成投资建议。量化策略存在滞后与失效风险。          │
└──────────────────────────────────────────────────────┘
```

### 10.5.3 四个 V4 组件

| 组件 | 文件 | 功能 |
|------|------|------|
| **AlgoCard** | `frontend/src/components/AlgoCard.tsx` | 算法卡片：名称、类型标签、信号数、描述、查看按钮 |
| **AlgoParamPanel** | `frontend/src/components/AlgoParamPanel.tsx` | 动态参数面板：根据 `params_schema` 渲染表单 |
| **AlgoDataTable** | `frontend/src/components/AlgoDataTable.tsx` | 结果表格：动态列 + 表头排序 + 分页 |
| **AlgoBarChart** | `frontend/src/components/AlgoBarChart.tsx` | 柱状图（A2 主力资金用） |
| **AlgoRadarChart** | `frontend/src/components/AlgoRadarChart.tsx` | 雷达图（A3 四维评分用） |

### 10.5.4 AlgoCard 组件示例

```tsx
// frontend/src/components/AlgoCard.tsx
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { AlgoMeta } from "../lib/algo-types";

const TYPE_COLORS: Record<string, string> = {
  technical: "bg-blue-500",
  value: "bg-green-500",
  fund: "bg-orange-500",
  score: "bg-purple-500",
};

const TYPE_LABELS: Record<string, string> = {
  technical: "技术派",
  value: "价值派",
  fund: "资金派",
  score: "评分派",
};

interface Props {
  algo: AlgoMeta;
  signalCount: number;
  isActive: boolean;
  onSelect: (code: string) => void;
}

export function AlgoCard({ algo, signalCount, isActive, onSelect }: Props) {
  return (
    <Card className={`cursor-pointer transition-all ${isActive ? "ring-2 ring-primary" : ""}`}
          onClick={() => onSelect(algo.algo_code)}>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">{algo.algo_name}</CardTitle>
          <Badge className={TYPE_COLORS[algo.algo_type]}>
            {TYPE_LABELS[algo.algo_type]}
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground mb-3">{algo.short_desc}</p>
        <div className="flex items-center justify-between">
          <span className="text-xs text-muted-foreground">信号数: {signalCount}</span>
          <Button size="sm" variant={isActive ? "default" : "outline"}>
            查看详情
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
```

### 10.5.5 AlgoParamPanel 组件示例

```tsx
// frontend/src/components/AlgoParamPanel.tsx
import { Slider } from "@/components/ui/slider";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import type { AlgoMeta, AlgoParams } from "../lib/algo-types";

interface Props {
  algo: AlgoMeta | null;
  params: AlgoParams;
  onChange: (params: AlgoParams) => void;
  onExecute: () => void;
  isLoading: boolean;
}

export function AlgoParamPanel({ algo, params, onChange, onExecute, isLoading }: Props) {
  if (!algo) {
    return <div className="text-muted-foreground text-sm">请选择一个算法</div>;
  }

  return (
    <div className="space-y-4">
      <h3 className="font-semibold">{algo.algo_name} — 参数调节</h3>

      {algo.params_schema.map((schema) => {
        if (schema.type === "select") {
          return (
            <div key={schema.key}>
              <label className="text-sm font-medium">{schema.label}</label>
              <Select
                value={String(params[schema.key] ?? schema.default)}
                onValueChange={(v) => onChange({ ...params, [schema.key]: v })}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {schema.options?.map((opt) => (
                    <SelectItem key={String(opt)} value={String(opt)}>{String(opt)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          );
        }

        if (schema.type === "number") {
          return (
            <div key={schema.key}>
              <label className="text-sm font-medium">
                {schema.label}: {params[schema.key] ?? schema.default}
              </label>
              <Slider
                min={schema.min ?? 0}
                max={schema.max ?? 100}
                step={1}
                value={[Number(params[schema.key] ?? schema.default)]}
                onValueChange={([v]) => onChange({ ...params, [schema.key]: v })}
              />
            </div>
          );
        }
        return null;
      })}

      <Button onClick={onExecute} disabled={isLoading} className="w-full">
        {isLoading ? "执行中..." : "🔄 执行算法"}
      </Button>
    </div>
  );
}
```

### 10.5.6 路由注册

在 `App.tsx` 中新增：
```typescript
import { AlgoCenter } from "./pages/AlgoCenter";

// 路由配置
<Route path="/dashboard-v3" component={DashboardV3} />
<Route path="/algo-center" component={AlgoCenter} />  {/* V4 新增 */}
```

导航栏也加上入口：
```tsx
<NavLink to="/dashboard-v3">📊 盘后分析</NavLink>
<NavLink to="/algo-center">🧮 算法中心</NavLink>
```

---

## 🔴 Step 11（可选）：RAG 知识库

```python
# backend/app/services/vector_store.py

"""
向量知识库（ChromaDB）
存入：每个板块每天的技术指标摘要
检索：生成报告时找相似历史形态，增强 AI 预测能力
"""
```

---

## 📊 执行顺序总结

```
Step 1    ████ 数据库 V3       ← 先建表
Step 1.5  ████ 数据库 V4       ← 算法缓存表 + 元信息表 [V4新增]
Step 2    ████ 数据拉取         ← AKShare + yfinance
Step 3    ███  技术分析         ← 指标计算
Step 4    ███  情绪分析         ← 融资/北向/论坛
Step 5    ███  综合评分         ← 三面加权
Step 6    ██   龙头识别         ← 规则驱动
Step 7    ███  AI 报告          ← DeepSeek Prompt 工程
Step 8    ███  API 层           ← analysis.py + algo.py [V4新增]
Step 8.5  ████ algo_service.py  ← CLI封装/缓存/安全 [V4新增 核心]
Step 9    ██   定时任务         ← worker.py 增加算法步骤 [V4修改]
Step 10   ████ 前端 V3 仪表盘   ← 现有页面
Step 10.5 ████ 前端 V4 算法中心 ← 4卡片+参数面板+表格+图表 [V4新增 核心]
Step 11   ██   RAG 知识库       ← 可选加分项
```

---

## ⚠️ 给执行 AI 的重要提示

### 通用提示

1. **不要一次写完所有文件再测试**。每完成一个 Step，立刻验证。
2. **AKShare 是国产库，文档不完善**。接口报错时 print DataFrame 列名再改映射。
3. **新闻情感分析最耗 DeepSeek token**。初期只对 Top 5 新闻做分析。
4. **前端热力图**：Recharts Treemap 搞不定就用 CSS Grid 画方格。
5. **所有异步函数别忘了 await**。
6. **工作日判断**：定时任务只周一到周五跑。
7. **默认日期**：API 的 `trade_date` 为空时，默认查数据库最新交易日。

### V4 专属提示

8. **westock-tool 是 Node.js CLI**，后端是 Python FastAPI——跨语言调用通过 `asyncio.create_subprocess_shell`。**必须设超时**，否则一个卡死的 CLI 会拖垮整个事件循环。

9. **westock-tool `--raw` 输出是 JSON 数组**，但有时会在前后打印日志行。解析时先用 `json.loads`，失败再正则提取 `[...]`。

10. **字段名是中文的！** westock-tool 返回的 JSON key 是 `"代码"` `"名称"` `"综合评分"` 这种中文，必须映射到英文 snake_case（见 `_parse_and_clean` 函数）。

11. **PE 可能为负数或极大值**（亏损股 PE 为负，成长股 PE 上万倍）。前端表格渲染时要处理：`PE < 0` 显示 "亏损"，`PE > 1000` 显示 ">1000"。

12. **缓存策略**：算法结果是每日数据，缓存键 = `algo_code + trade_date`。用户切参数时如果只改了 `limit`，可以复用同一天缓存的子集（前端做分页/截断即可），不必重跑 CLI。

13. **westock-tool 路径**：通过环境变量 `WESTOCK_TOOL_PATH` 注入。开发环境用相对路径，Docker 部署时映射到容器内固定路径。

14. **不要硬编码股票代码前缀逻辑**。前端展示时统一去掉 `sh/sz/hk` 前缀，只显示数字代码 + 名称。

---

## 📋 V4 算法速查表（给前端开发参考）

| 编号 | 算法名称 | 类型标签 | CLI 命令（简化） | 返回核心字段 | 默认排序 |
|---|---|---|---|---|---|
| A1 | MACD金叉×低估值 | 技术派 | `ranking fin_valuation --within-strategy macd_golden` | pe_ttm, pb_lf, ps_ttm, pcf_ttm, div_ttm | PE升序 |
| A2 | 均线多头×主力5日加仓 | 资金派 | `ranking cap_main_5d --within-strategy ma_long` | main_sum_5d, main_t0~t4 | 净流入降序 |
| A3 | 央企×综合评分TOP | 评分派 | `ranking CompScore --within-label shareholder_central_state` | composite_score, capital_score, fundamental_score, risk_score, technical_score | 评分降序 |
| A4 | 高股息×低估值 | 价值派 | `filter "intersect([PE_TTM>0, PE_TTM<15, DividendRatioTTM>4])"` | dividend_ratio_ttm, pe_ttm, close_price, change_pct | 股息率降序 |

### 真实数据样本（2026-07-26 实测）

```json
// A1 样本（MACD金叉×低估值 TOP3）
[
  {"code": "sh603023", "name": "威帝股份", "pe_ttm": 19776.98, "pb_lf": 2.73, "ps_ttm": 11.25},
  {"code": "sz300004", "name": "南风股份", "pe_ttm": 595.16, "pb_lf": 3.21, "ps_ttm": 10.96},
  {"code": "sz301292", "name": "海科新源", "pe_ttm": 563.72, "pb_lf": 6.30, "ps_ttm": 3.32}
]

// A2 样本（均线多头×主力5日加仓 TOP3）
[
  {"code": "sh603893", "name": "瑞芯微", "main_sum_5d": 143422.21, "main_t0": 14524.7, "main_t4": 36405.49},
  {"code": "sz301165", "name": "锐捷网络", "main_sum_5d": 131011.56, "main_t0": 17694.93, "main_t4": 14651.38},
  {"code": "sh601857", "name": "中国石油", "main_sum_5d": 73151.69, "main_t0": 20402.72, "main_t4": 29211.02}
]

// A3 样本（央企×综合评分 TOP3）
[
  {"code": "sz002246", "name": "北化股份", "composite_score": 86.43, "capital_score": 85.41, "fundamental_score": 91.55, "risk_score": 76, "technical_score": 88.79},
  {"code": "sh601061", "name": "中信金属", "composite_score": 83.79, "capital_score": 80.94, "fundamental_score": 89.32, "risk_score": 82.95, "technical_score": 83.66},
  {"code": "sh601872", "name": "招商轮船", "composite_score": 83.53, "capital_score": 59.44, "fundamental_score": 89.89, "risk_score": 81.58, "technical_score": 86.37}
]

// A4 样本（高股息×低估值 TOP3）
[
  {"code": "sh600329", "name": "达仁堂", "dividend_ratio_ttm": 13.08, "pe_ttm": 12.93, "close_price": 36.61, "change_pct": -2.43},
  {"code": "sh603508", "name": "思维列控", "dividend_ratio_ttm": 12.13, "pe_ttm": 13.99, "close_price": 21.64, "change_pct": -4.04},
  {"code": "sz300533", "name": "冰川网络", "dividend_ratio_ttm": 11.04, "pe_ttm": 14.91, "close_price": 12.94, "change_pct": -4.43}
]
```

---

## 🚀 快速启动检查清单

```bash
# 1. 确认 westock-tool 可用
node westock-tool/scripts/index.js strategy --list

# 2. 初始化 V4 数据库表
python -m scripts.init_v4_db

# 3. 手动跑一个算法验证链路
python -m scripts.run_algo a1 --limit 5

# 4. 启动后端，检查 API
curl http://localhost:8000/api/v1/algo/list
curl http://localhost:8000/api/v1/algo/a1?limit=5

# 5. 启动前端，访问算法中心
npm run dev
# 浏览器打开 http://localhost:5173/algo-center
```

---

> **Built with AI | GFNA V4 | 2026**
> **数据来源**：腾讯自选股公开数据接口（westock-tool）
> **免责声明**：本系统所有数据仅作客观量化信号展示，不构成任何投资建议。量化策略存在滞后与失效风险，回测≠实盘。投资有风险，决策需谨慎。
