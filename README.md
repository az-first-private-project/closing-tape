# GFNA V5 — A 股行情采集 + 量化选股系统

> 从「新闻聚合器」改造而来：新闻链路降级为**情绪因子输入**，主线变成
> **行情采集 → 本地 PostgreSQL 单一事实源 → 指标/因子 → 条件树选股 → 回测**。
>
> 历史：V1 原生 JS + MySQL → V2 React/FastAPI + PostgreSQL → V3 板块分析 → V4 外部 CLI 选股（mock）
> → **V5 全链路本地计算（本版）**

---

## 0. 一句话说明它现在能做什么

- 采集 A 股全市场股票池、近 5 年日线（含复权因子）、板块日线与成分（SCD2）、指数、两融、资金流；
- 在本地库上计算 MA/RSI/MACD/BOLL/ATR/动量/波动/量比，以及六维因子（价值/质量/动量/波动/流动性/情绪）；
- 用**条件树 + 四个内置策略**选出股票，结果带 `run_id`、参数快照、数据版本，**可复现**；
- 对任一策略跑**回测**：T+1 开盘成交、费用/滑点、涨跌停与停牌不可成交、后复权收益、基准超额；
- 前端展示盘后分析、选股中心（参数真生效 + CSV 导出）、个股详情（K 线/指标/因子雷达）、任务监控与**数据新鲜度**。

**它不是"能稳定赚钱的策略"**，而是一套让决策可复现、可归因、可证伪的数据与信号系统。回测 ≠ 实盘。

---

## 1. 快速开始（从零到第一份选股结果）

```bash
# 1) 依赖（国内建议用镜像）
cd backend
python -m venv .venv            # 已有 .venv 可跳过
.venv\Scripts\activate          # Windows；Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 2) 配置（把 DEEPSEEK_API_KEY 填上，或留空——AI 日报会自动降级为数据摘要）
copy .env.example .env

# 3) 建库 + 建表 + 分区 + 视图 + 策略种子
python scripts/bootstrap_db.py            # 会创建 gfna_db（若不存在）

# 4) 采集真实数据（串行执行，见下方"采集任务"）
python scripts/pipeline_cli.py calendar   # 交易日历
python scripts/pipeline_cli.py universe   # 股票池
python scripts/pipeline_cli.py backfill --start 2025-10-21   # 近 1 年日线（断点续传）
python scripts/pipeline_cli.py index --date 2026-10-21       # 指数
python scripts/pipeline_cli.py snapshot --date 2026-10-21    # 当日快照（估值快照源，东财不可用时跳过）
python scripts/pipeline_cli.py sector --date 2026-10-21      # 板块（东财不可用会自动降级同花顺）
python scripts/pipeline_cli.py valuation --start 2025-11-06  # 历史估值（财报+分红→本地算 PE/PB/股息率）
python scripts/pipeline_cli.py features --date 2026-10-21    # 指标
python scripts/pipeline_cli.py scores   --date 2026-10-21    # 个股因子评分（依赖估值）
python scripts/pipeline_cli.py sector_scores --date 2026-10-21
python scripts/pipeline_cli.py algos    --date 2026-10-21    # A3/A4 + B1–B5 选股
```
> 顺序很重要：**日线 → 估值 → 指标 → 因子 → 选股**。
> `valuation` 首次会同步财报与分红（全市场约 45 分钟 @2req/s，可用 `--codes` 先小规模试），
> 之后每日只算估值（零网络，秒级）。

# 5) 启动后端（默认 http://127.0.0.1:8000 ，文档 /docs）
python -m uvicorn app.main:app --reload --port 8000

# 6) 启动前端（另开终端）
cd ../frontend
npm install
npm run dev                     # 默认 http://127.0.0.1:5173
```

> **更省事的一键启停**（Windows，脱离会话常驻，日志在 `logs/`）：
> ```powershell
> .\start-local.ps1          # 后端 8000 + 前端 5173（自动确保表结构）
> .\start-local.ps1 -Demo    # 空库时先灌演示数据
> .\start-local.ps1 -Stop    # 停止
> ```

**Playwright（新闻爬虫用，可选）**：只装 Python 包不够，还需要浏览器内核：

```bash
python -m playwright install chromium      # Docker 镜像里已装好
```

**没有网络也能验收全链路**（确定性演示数据，不联网）：

```bash
python scripts/pipeline_cli.py demo       # 42 只标的 × 250 交易日
python scripts/pipeline_cli.py features
python scripts/pipeline_cli.py scores && python scripts/pipeline_cli.py sector_scores
python scripts/pipeline_cli.py algos
```

---

## 2. 架构

```
┌─ L1 采集 ingest ───────────────────────────────────────────┐
│  Provider 抽象 · akshare（主）· baostock（兜底/复权校验）    │
│  交易日历 · 限频(快照≤1/min, 回补≤2/s) · 重试 · 断点续传      │
│  幂等 upsert · 数据质量校验 · DB advisory lock              │
└───────────────────────────┬────────────────────────────────┘
                            ▼
┌─ L2 存储 PostgreSQL ───────────────────────────────────────┐
│  dim_stock · fact_stock_daily(按年分区) · dim_sector_member │
│  fact_sector_daily · fact_feature_daily · fact_stock_score  │
│  fact_moneyflow · market_indices · margin_trading           │
│  stg_run/stg_signal · backtest_run · daily_report           │
│  meta_data_quality · trade_calendar · ingest_job            │
│  视图 v_stock_latest / v_stock_features                     │
└───────────────────────────┬────────────────────────────────┘
                            ▼
┌─ L3 分析 engine ───────────────────────────────────────────┐
│  indicators: MA/RSI(Wilder)/MACD/BOLL/ATR/动量/波动/量比     │
│  factors: 价值/质量/动量/波动/流动性/情绪 → 分位             │
│  cross_section: 行业中性 z-score / winsorize / 分位          │
│  scoring: 板块 技术40/情绪35/消息25（情绪含四子项再分配）     │
└───────────────────────────┬────────────────────────────────┘
                            ▼
┌─ L4 选股 strategy ─────────────────────────────────────────┐
│  condition: AND/OR/between/in/top_pct/行业约束               │
│  strategies: A3/A4 + B1–B5 · runner: run_id/参数指纹/数据版本  │
│  backtest: T+1 成交/费用/涨跌停不可成交/后复权 · metrics 13 项│
└───────────────────────────┬────────────────────────────────┘
                            ▼
┌─ L5 服务与展示 ────────────────────────────────────────────┐
│  FastAPI /api/v1 · 统一错误码 · 新鲜度 · React 看板          │
└────────────────────────────────────────────────────────────┘
```

**为什么不再调 `westock-tool`**：它是 117 行 mock；接真版本也无 SLA、5–30 秒响应、无法回测。
全市场日线落库后，选股本质是一次横截面运算——**毫秒级、可复现、参数真生效**。

---

## 3. 数据源与网络现实

| 数据 | 主源 | 兜底 | 备注 |
|---|---|---|---|
| 交易日历 | akshare `tool_trade_date_hist_sina` | pandas bdate_range | 首次必须同步，否则日历为空 |
| 股票池 | akshare `stock_info_a_code_name`（≈5900 只） | baostock `query_stock_basic` | 退市股尽力补 `delist_date` |
| 个股日线 | akshare `stock_zh_a_hist` / 新浪 `stock_zh_a_daily` | **baostock `query_history_k_data_plus`** | 见下方「实测网络」 |
| 指数 | akshare `stock_zh_index_daily` | baostock | 含沪深300 基准 |
| 板块/成分 | akshare 东财板块接口 | **同花顺 `stock_fund_flow_industry/concept`（兜底版）** | 兜底版只有指数/涨跌幅/领涨股，无成交量与涨跌家数（如实置空） |
| 板块资金流 | akshare `stock_sector_fund_flow_rank` | **同花顺 `fetch_net_inflow_map_sync`** | 行业+概念合并取值，来源如实标记 `ths` |
| 资金流(个股) | akshare `stock_individual_fund_flow_rank` | — | 东财/同花顺个股页均不可达 → A2 明确报"数据未采集" |
| **历史估值** | **新浪财报 `stock_financial_analysis_indicator` + 巨潮分红 `stock_dividend_cninfo`** | 新浪 `stock_financial_abstract`（EPS 兜底） | **本地计算 PE_TTM/PB/股息率**，财报落库缓存，日常计算零网络 |
| 两融/北向 | akshare `stock_margin_sse/szse`、`stock_hsgt_hist_em` | — | **北向 2024-08 起暂停披露**，缺失不阻断 |

**本机实测（2026-10，重要）**：东方财富系接口（`push2*`）在本网络被重置
（`RemoteDisconnected`），因此 `stock_zh_a_hist` / `*_spot_em` / 东财板块接口不可用；
**新浪（日线/指数/财报）、同花顺 `data.10jqka.com.cn`（板块资金流）、巨潮（分红）、baostock、DeepSeek 均可用**。
默认降级链 `akshare → baostock` 在本机实际由 **新浪日线 或 baostock** 提供数据。
可在 `.env` 里用 `DATA_PRIMARY_PROVIDER=baostock` 直接跳过东财重试（每只省约 13 秒）。

> **历史估值已打通（2026-10-22 新增）**：不再依赖已下线的 `stock_a_indicator_lg`。
> 做法是「**财报/分红缓存进库 + 本地推算**」：
> `EPS_TTM = 本期累计 + 上年年报 − 上年同期` → `PE = close / EPS_TTM`；
> `PB = close / BPS`；`股息率 = 近 12 个月每股分红 / close`。
> 用「报告期 + 90 天」作为市场可知日做 **point-in-time**（避免未来函数）。
> 实测样例：中国平安 PE 5.62 / PB 0.64 / 股息率 7.19%，中国建筑 PE 5.93 / PB 0.22 / 6.03%，
> 招商银行 PE 7.30 / 4.77% —— 与真实市场一致。
>
> 实测证据见 [`docs/evidence/`](docs/evidence/)：真实数据下的 A4（4 条）与 A3（5/6 条）信号、
> 四份回测净值曲线、真实日线回补记录。

> 合规边界：AKShare 是对公开页面的二次封装，不是交易所授权接口。本系统限频
> （快照 ≤1 次/分钟、回补 ≤2 请求/秒）、固定 UA、**不实现任何反爬绕过/验证码/代理池**。
> 数据仅个人研究，不得对外分发或商用。

---

## 4. 四个内置策略（V5 定稿口径）

统一前置过滤：剔除 ST、停牌/退市、上市不足 60 个交易日、当日无行情。

| 编号 | 名称 | 条件 | 排序 |
|---|---|---|---|
| **A1** | MACD金叉 × 低估值 | **当日** DIF 上穿 DEA；`PE_TTM>0`；`PE_TTM<pe_max`(50)；日成交额≥`min_amount`(5000 万) | PE 升序 |
| **A2** | 均线多头 × 主力5日加仓 | **MA5>MA10>MA20**；近 5 日主力净流入 ≥ `min_main_5d`(万元) | 主力净流入降序 |
| **A3** | 央企 × 综合评分 | `ownership='央企'`；因子综合分 ≥ `min_score`(6.0/10) | 综合分降序 |
| **A4** | 高股息 × 低估值 | `PE_TTM>0`；`PE_TTM<pe_max`(15)；`dv_ttm≥div_min`(4%) | 股息率降序 |

- A1 的 `PE>0` 是相对 V4 的**关键修复**（V4 不剔除亏损股，样本 TOP1 的 PE 高达 19776）。
- A3 的"央企"来自 `core/soe.py`：央企名录 + 名称规则，**未知不会被误判为央企**，
  局限见该文件 docstring。`composite_score` 为**本地六因子模型**（0–10），不是腾讯评分。
- 参数会同时进入**计算、缓存键（含参数指纹与数据版本）与 run 快照**——改参数必然产生新 run。

## 5. 回测口径

| 项 | 取值 |
|---|---|
| 调仓 | 每 **20 个交易日**（按真实交易日历） |
| 持仓 | **20 只等权** |
| 成交 | **T 日收盘出信号 → T+1 开盘成交**（买入 ×(1+滑点)、卖出 ×(1−滑点)） |
| 成本 | 佣金 0.025%（双边，最低 5 元）、印花税 0.1%（仅卖出）、滑点 0.1% |
| 不可成交 | 涨停买不进、跌停卖不出、停牌、无数据、不足 1 手 |
| 收益 | **后复权**（`close × adj_factor`），缺复权因子时在 metrics 标 `adjusted=false` |
| 基准 | 沪深300（`sh000300`） |
| 起点 | 自动裁剪到该策略**数据可用起点**，响应带 `start_trimmed=true` 与 `effective_start`（避免个别调仓日缺数据导致整体失败） |
| 指标 | 总收益/年化/最大回撤/夏普/索提诺/卡玛/胜率/盈亏比/换手/成交数/基准收益/超额/波动 |

**防未来函数**：代码层禁止 `.shift(-`，`assert_no_lookahead()` 静态扫描本包；
测试里可用"把 T+1 价格换成 T 日价格"来验证检测会失败。

---

## 6. 接口

完整契约见 [`docs/V5-API-CONTRACT.md`](docs/V5-API-CONTRACT.md)。常用：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 数据库状态 / 最新交易日 / 数据就绪 |
| GET | `/api/v1/market/dashboard` | 盘后分析（指数/板块/评分/龙头/两融/日报/新鲜度） |
| GET | `/api/v1/market/sector/{code}` | 板块详情（三面子分 + 龙头 + 成分） |
| GET | `/api/v1/stocks/{code}` | 个股详情（行情/指标/因子/板块） |
| GET | `/api/v1/stocks/{code}/kline` | K 线序列（含 MA） |
| GET | `/api/v1/algo/list` | 策略清单（参数 schema） |
| GET | `/api/v1/algo/{code}` | 执行/读取策略结果（含 run 元信息） |
| GET | `/api/v1/algo/{code}/export.csv` | 结果导出 |
| POST | `/api/v1/backtest/{code}` | 回测 |
| GET | `/api/v1/meta/freshness` | 数据新鲜度（前端徽标唯一来源） |
| GET | `/api/v1/meta/jobs` | 采集任务监控 |
| POST | `/api/v1/admin/ingest/{task}` | 手动触发采集（需 `X-Admin-Key`） |

错误统一为 `{"ok": false, "error": {"code", "message", "detail"}}`，
HTTP 语义：参数 422 / 不存在 404 / 数据未就绪 409 / 外部源 503。

---

## 7. 测试

```bash
cd backend
..\.venv\Scripts\python.exe -m pytest tests -q          # 纯逻辑测试，不需要数据库
# 需要真实库的集成测试（先跑 demo + features/scores/algos）
set GFNA_TEST_DB=1 && set SCHEDULER_ENABLED=0
..\.venv\Scripts\python.exe -m pytest tests -q
```

覆盖：指标对已知值（Wilder RSI 参考值）、金叉无未来函数、因子单调性、条件树、
参数指纹、回测绩效对已知净值、回测未来函数检测、API 契约与统一错误体、
**参数真生效**（`pe_max` 变化 → 不同 run 与结果）、**可复现性**（同参同 run）。

---

## 8. 目录结构

```
backend/
├── app/
│   ├── core/        配置、常量、错误码、日志、所有制规则(soe.py)
│   ├── db/          Base/Session/模型(models/)/分区与视图(views.py)/种子(seed.py)
│   ├── ingest/      Provider 抽象、akshare、baostock、日历、质量校验、编排
│   ├── engine/      指标、因子、横截面、个股评分、板块评分、龙头、情绪、日报输入
│   ├── strategy/    条件树、A3/A4 + B1–B5、runner、回测、绩效
│   ├── api/v1/      新闻、行情、个股、选股、回测、元信息、管理
│   ├── pipeline.py  任务编排（run_task / run_daily_pipeline）
│   ├── worker.py    定时任务（15:36 盘后 / 16:30 选股 / 02:00 补齐 / 新闻）
│   └── fixtures/    离线确定性演示数据
├── alembic/         迁移（0001 为 V5 基线：建表+分区+视图）
└── scripts/         bootstrap_db.py / pipeline_cli.py / ingest_cli.py
frontend/src/        api/ types/ hooks/ components/ pages/（Dashboard·AlgoCenter·StockDetail·Jobs）
docs/                V5 方案、API 契约、架构、变更清单
```

---

## 9. Docker 部署

```bash
# 前提：启动 Docker Desktop（否则报 npipe dockerDesktopLinuxEngine 找不到）
docker compose up -d --build        # 首次约 5–10 分钟
docker compose logs -f backend
# 浏览器：http://localhost:3000   (前端 Nginx，/api 反代到 backend:8000)
```

镜像已包含：

| 项 | 说明 |
|---|---|
| **Playwright Chromium** | `RUN python -m playwright install --with-deps chromium`（含 libnss3 等系统库，`PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`） |
| 自动初始化 | 入口脚本先 `bootstrap_db.py`（建表/分区/视图/种子），再 `first_run.py` |
| **首次自动灌数据** | `SEED_DEMO_ON_EMPTY=1`（compose 默认）→ 空库时灌演示数据并算指标/评分/选股，打开页面就有内容；已有数据则跳过，绝不覆盖 |
| 时区 | `TZ=Asia/Shanghai`（避免容器 UTC 导致 15:36 实际是北京 23:36） |
| 健康检查 | `/health`（含数据库连通性） |

> **关于"启动后没有数据"**：原因不是缺 Playwright（它只影响新闻爬虫），
> 而是空库没有任何行情。现在入口脚本会在空库时自动初始化；
> 想直接用真实数据则设 `SEED_DEMO_ON_EMPTY=0`，再执行
> `docker compose exec backend python scripts/pipeline_cli.py all`。
>
> PostgreSQL 端口映射到宿主机 **5433**（避免与本机已装的 5432 冲突），容器内部仍用 `postgres:5432`。

---

## 10. 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| `pip install` 卡住/超时 | 用镜像：`-i https://mirrors.aliyun.com/pypi/simple/` |
| 导入即崩、缺 asyncpg/pandas | 依赖未装或装错解释器；确认用 `backend/.venv` |
| Windows 下 uvicorn 卡死 | 已在 `main.py` 设 `WindowsSelectorEventLoopPolicy`；不要移除 |
| **页面打开但没有数据** | 空库没有行情 → `python scripts/first_run.py`（空库自动灌演示数据）或按顺序采集真实数据 |
| **新闻爬虫报 `Executable doesn't exist ... chromium`** | Playwright 只装了 Python 包：`python -m playwright install chromium`（Docker 镜像里已装） |
| 东财接口 `RemoteDisconnected` | 本网络屏蔽东财；用 `DATA_PRIMARY_PROVIDER=baostock`；板块会自动降级同花顺 |
| 板块热力图空 | 先跑 `pipeline_cli.py sector`（东财失败会自动用同花顺兜底，只含指数/涨跌幅/领涨股） |
| A2 报"个股资金流尚未采集" | 个股资金流仅东财源，本网络不可达；A2 需要真实资金流才能算 |
| A1/A4/A3 无真实信号 | 先跑 `pipeline_cli.py valuation --start …`，否则历史日线没有 PE/PB/股息率 |
| 估值整体为空 | 财报接口只回约 3.5 年（14–15 期），更早日期按设计留空不外推 |
| 北向资金为空 | 2024-08 起暂停披露，属预期，不参与评分缺陷 |
| 评分全 5.0 | 历史不足 20 个交易日；先 `backfill` 再算 `features` |
| `run` 记录查不到 | `get_db` 已改为成功即 commit；若自建会话请显式 commit |

---

## 11. 免责声明

本系统为工程技术项目，**不含投资建议**。所有信号仅供个人研究，不构成买卖依据。
量化价值在于把决策变得可复现、可归因、可证伪；回测漂亮的策略在实盘失效是常态。
市场有风险，投资需谨慎。
