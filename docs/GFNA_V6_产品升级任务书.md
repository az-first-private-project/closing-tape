# GFNA V6 产品升级任务书（行情呈现 · 算法中心 · 视觉系统）

> 交付对象：AI 编码助手
> 日期：2026-10-28
> 项目路径：`C:\docker-projects\grna`（backend = FastAPI 0.110 + SQLAlchemy 2.0 async + PostgreSQL 16；frontend = React 18 + Vite 5 + TS + Tailwind v4）
> 前置文档：`docs/V5-API-CONTRACT.md`（字段口径唯一事实源）、`docs/architecture.md`、`docs/GFNA_V5_改造升级方案.md`
> 本任务书共 **15 个任务**，按 T1→T15 顺序执行，**一次只做一个**，做完自测并汇报，等确认再进下一个。

---

## 0. 背景与目标

V5 已经打通"真实数据 → 特征 → 策略 → 回测"的主链路，但产品形态仍停在"给一份名单"。用户打开站点后看不到市场在发生什么、不知道此刻该用哪个策略、也不理解"为什么是这几只票"。

V6 目标分三层，按感知强度与实现成本排序：

1. **行情呈现**（感知最强、成本最低）：把已有日线/特征表的信息密度做满 —— 指数多周期对比、市场温度计、板块 treemap、真正的 K 线。
2. **算法中心**（从"给名单"到"给依据、给跟踪"）：策略天梯、多策略共识、信号逐条解释、参数敏感性、回测面板增强、纸面组合跟踪、自选股信号提醒。
3. **视觉科技感**（信息密度优先、动效克制）：统一设计系统、骨架屏、sparkline、Ctrl+K 命令面板。

**共同原则：信息密度优先，动效克制。** 炫酷过头会牺牲加载速度和可读性，反而扣分。任何视觉改动都要同时看数据正确性和首屏体积。

---

## 1. 事实基线（已逐条核实，不要重新调研、不要怀疑）

引用位置以 **文件 + 符号名** 为准；文中行号是 2026-10-28 的快照，可能随提交漂移，符号名冲突时以符号为准。

### 1.1 前端

| 事实 | 位置 / 内容 |
|---|---|
| 唯一前端根目录，非 monorepo | `frontend/package.json`（`gfna-frontend-v2`，`type: module`，`build: tsc -b && vite build`） |
| `src/` 实际引用的外部包**只有 7 个** | `react` `react-dom` `react-router-dom` `@tanstack/react-query` `framer-motion` `lucide-react` `recharts` |
| 已装版本 | recharts 2.15.4、framer-motion 11.18.2、@tanstack/react-query 5.101.4、react-router-dom 6.30.4、tailwindcss 4.3.3、vite 5.4.21、typescript 5.9.3 |
| **未安装**（勿假设可用） | echarts、antd、zustand、redux、jotai、lightweight-charts、klinecharts、clsx、tailwind-merge、axios |
| ⚠️ 锁文件漂移 | `package-lock.json` 里残留 `axios`（根依赖，node_modules 里物理存在），但 `package.json` 无 axios 且无源码引用。`npm ci` 会把它删掉。**不要顺手 import axios** |
| 样式是 Tailwind v4 **CSS-first** | `frontend/src/index.css`（约 123 行）。**没有 `tailwind.config.js`，也没有 `postcss.config.js`**；token 写在 `@theme` 块里 |
| `@custom-variant dark` 是承重的 | `index.css` 里 `@custom-variant dark (&:where(.dark, .dark *))`。删掉它则所有 `dark:` 静默失效（v4 默认走 `prefers-color-scheme`，本项目切 class） |
| 已有工具类 | `.glass`（导航栏）、`.skeleton` + `@keyframes shimmer`、`.card-3d`、`@utility scrollbar-thin` |
| 图表配色必须用 CSS 变量 | Recharts 现有代码就写 `stroke="var(--color-border)"`；v4 没有默认 `border-border`，能生效只因 `--color-border` 注册在 `@theme` |
| class 拼接 | `frontend/src/lib/utils.ts` → `cn(...classes)`（自研，非 clsx） |
| 格式化工具 | `frontend/src/lib/format.ts` → `fmtNumber / fmtPct / fmtAmount / fmtVolume / shortDate / priceColorClass / scorePercent` |
| API 层是自研 fetch | `frontend/src/api/client.ts` → `BASE_URL = '/api/v1'`、`ApiError`、`request<T>`（20s AbortController 超时；**仅 GET + 5xx/网络错重试 1 次**）、`extractList`（兼容裸数组/`{items}`/`{runs}`/`{jobs}`/`{data}`/`{list}`）、`downloadCsv` |
| react-query 关掉了自己的重试 | `main.tsx` 里 `retry: false`，因为 `client.ts` 已负责重试，别改成双重试 |
| 状态管理 | 服务端数据全走 `frontend/src/hooks/` 的 react-query hooks；全局只有 `ThemeProvider.tsx` 一个 Context（`localStorage` key `gfna-theme`）；其余是局部 useState + URL query |
| 路由 | `frontend/src/App.tsx` 单棵 Routes，全挂在 `Layout.tsx` 下：`/`(HomePage) `/article/:id` `/dashboard` `/algo-center` `/stocks/:code` `/jobs` `/about` `*`。导航项常量在 `Navbar.tsx` 的 `NAV_ITEMS` |
| `IndexCard` 只有一个消费者 | `frontend/src/components/IndexCard.tsx` 导出的 `IndexCardData` 仅被 `pages/DashboardPage.tsx` 用（适配函数 `toIndexCard` / `toUsIndexCard`） |
| `SectorHeatmap` 不是图表库 | `frontend/src/components/SectorHeatmap.tsx` 是 CSS Grid + 硬阈值函数 `heatColor(pct)`（`>8 / >5 / …` 阶梯），用 `motion.div` stagger `delay: index*0.01` |
| ⚠️ **现在没有真正的蜡烛图** | `KlineChart` 是 `pages/StockDetailPage.tsx` 里一个**未导出的局部函数组件**，画的是 Recharts `ComposedChart`：收盘价折线 + ma5/ma20/ma60 + 成交量 Bar |
| 但数据里已经有 OHLC | `frontend/src/types/stock.ts` 的 `KlineItem` 已含 `open high low close volume amount pct_chg ma5 ma20 ma60` → **换蜡烛图不需要动后端取数** |
| K 线 hook | `frontend/src/hooks/useStock.ts` → `useKline(code, limit=250, end)`，queryKey `['kline', code, limit, end||'latest']` |
| spinner 用在哪 | `animate-spin` 出现在 8 个文件：`components/AlgoParamPanel.tsx` `components/BacktestPanel.tsx` `components/RunMetaPanel.tsx` `components/StateViews.tsx` `pages/DashboardPage.tsx` `pages/DetailPage.tsx` `pages/HomePage.tsx` `pages/JobsPage.tsx` |
| 前端只调了两个行情端点 | `src/api/market.ts` 只有 `/market/dashboard` 和 `/market/sector/{code}`；**`/market/indices`（含多日 `series`）前端从未调用** |

### 1.2 后端与数据

| 事实 | 位置 / 内容 |
|---|---|
| 路由器聚合 | `app/main.py` → `app.include_router(api_router)`；`app/api/v1/__init__.py` 逐个 include `articles/stats/market/stocks/algo/backtest/meta/admin` |
| 前缀 | `/api/v1/market`、`/stocks`、`/algo`、`/backtest`、`/meta`、`/admin`；`articles.py` 直接挂 `/api/v1` |
| 响应约定 | **成功返回裸业务对象，不包 `data` 壳**；错误 `{ok:false, error:{code,message,detail}, request_id}`；参数错 422、不存在 404、未就绪/质量 409、外部源不可用 503 |
| 字段口径 | 价格 `open/high/low/close/pre_close`；涨跌 `pct_chg` 是**百分数**；`amount` 单位**元**、`volume` 单位**股**；`*_score` 统一 **0–10**；`is_*` 一律布尔 |
| 新鲜度 | 所有展示接口应带 `Freshness` 对象；dataset key 见 `V5-API-CONTRACT.md`（含 `index_daily`、`moneyflow`、`valuation`…），`meta_data_quality` 是前端新鲜度徽标唯一数据源 |
| DB | PostgreSQL 16（`docker-compose.yml` 服务名 `postgres`，宿主端口默认 `5433`）；`DATABASE_URL` 用 asyncpg、`DATABASE_URL_SYNC` 用 psycopg2；异步引擎 pool_size=10 + max_overflow=10 + `pool_pre_ping` |
| 事务约定 | `get_db()` 依赖在成功路径**显式 commit**（策略/回测只 flush）；后台任务用 `session_scope()`；`app/engine/_db.py` 有 `session_ctx()` / `commit_or_flush(session, external)` / `to_jsonable()` 惯例 |
| **`fact_stock_daily` 是年分区表** | `{"postgresql_partition_by": "RANGE (trade_date)"}`；分区由 `app/db/views.py` 的 `ensure_partitions()` / `ensure_partition_for()` 维护（`fact_stock_daily_y2026` + `_default`）。`close` 注释明确写"**未复权原始价**"，`adj_factor` 是**后复权**因子；另有 `market_cap float_cap pe_ttm pb ps_ttm dv_ttm is_limit_up is_limit_down is_suspended` |
| Alembic 只有一个基线 | `backend/alembic/versions/20261021_0001_v5_baseline.py`，内容就是 `Base.metadata.create_all(bind)` + `ensure_partitions` + `create_views` |
| ⚠️ 改表必须写迁移 | 分区表上 `create_all` **不会**修改已存在的表。任何加列/新表都要新增 alembic revision |
| 特征表 | `fact_feature_daily`（PK code+trade_date），列清单同时是 `app/engine/indicators.py` 的 `FEATURE_COLUMNS`。含 `ma5/10/20/60 ma120 rsi6 rsi14 macd_* boll_mid/upper/lower boll_width boll_width_pct60 atr14 momentum_5/20/60 volatility_20 volume_ratio amount_ma20 ma_bullish ema5/20/60 ema20_up ema_golden_cross ema_cross_3d roc10 kdj_k/d/j/j_min2 kdj_golden_cross high20_close range20_pct amp60 vol_ma5` |
| 个股评分 | `fact_stock_score_daily`（0–10）：`value quality momentum volatility liquidity sentiment composite` + **`detail` JSONB 已存"原始因子与分位"** |
| 板块 | `fact_sector_daily`（PK sector_code+trade_date）有 `close pct_chg amount up_count down_count leading_stock*`；`dim_sector_member` 是 **SCD2**（PK sector_code+stock_code+effective_from，`effective_to` 可为 NULL）；`sector_score` 有 `technical/sentiment/news/composite_score` + `strength_leaders/sentiment_leaders/detail` JSONB |
| ⚠️ SCD2 取成分铁律 | 回看历史某日必须带 `effective_from <= d AND (effective_to IS NULL OR effective_to > d)`，否则拿今天的名单去算历史 |
| ⚠️ 部分板块 `up_count` 是 NULL | 同花顺源刻意不编造涨跌家数（`ingest/ths_sector.py`），东财源才有。**NULL 不能当 0 处理** |
| 资金流 | `fact_moneyflow` 只有**个股级**：`main_net_t0`（当日主力净流入，元）、`main_net_t1..t4`（源不提供、当前为空）、`main_net_5d`、`north_hold_ratio`。**不存在板块级资金流表** |
| 策略集合 | `app/strategy/strategies.py`：现存 `A3 A4 B1 B2 B3 B4 B5`。**A1/A2 已退役**（近三年 -16.4% / -33.7%，最大回撤 -59%），别 resurrect |
| 策略结构 | `SPECS: dict[str, StrategySpec]`（含 `params_schema defaults algo_type short_desc full_desc sort_order`）；`_SORT_SPEC` 定义每策略的排序列与升/降序；`_METRIC_FIELDS` 定义写入 metrics 的列；`build_candidates()` 一条 SQL 取截面；`apply_common_filters()` 去 ST/停牌/上市<60 日；`_assert_data_ready()` 在特征覆盖率 < 0.5 时抛 `DataUnavailableError`；`strategy_mask()` 是**每策略硬编码的 pandas 布尔掩码**；`rank_signals()` 排序+截断+`rank=1..N`+`_metrics_for_row()` |
| ⚠️ 每条件命中明细**当前不存** | `stg_signal` 列只有 `run_id code name rank score metrics(JSONB)`，`metrics` 是扁平指标值（见 `backend/var/check_b1.json`），没有 pass/fail 判定 |
| 但条件 DSL 已存在且闲置 | `app/strategy/condition.py` 有 `Condition/ConditionGroup/evaluate/apply/describe/from_dict/to_dict/flatten_fields`，ops `gt ge lt le eq ne between in not_in top_pct bottom_pct`，`describe()` 注释就是为"人类可读条件写进 run 快照"；**目前只被 `backend/tests/test_strategy.py` import，生产代码没用** |
| ⚠️ **回测也写 `stg_signal`** | `runner.py` 的 `_run_strategy_impl` 对回测 run（`is_backtest=True`）同样插入 StgSignal 行。**任何按信号做的统计必须 `join stg_run` 并过滤 `StgRun.is_backtest.is_(False)`**（现有 `list_runs` 已是这么做的，`runner.py:586`） |
| `stg_signal` 无日期列 | 要按日聚合必须 `stg_signal ⋈ stg_run` 取 `stg_run.trade_date`；`stg_run` 另有 `algo_code trade_date params(JSONB) universe_count signal_count data_version status elapsed_ms` |
| 策略缓存 | run 的缓存键 = `(algo_code, trade_date, params_fingerprint, data_version)`，`data_version = f"{trade_date}:{sha1(4 张表 rows+max(updated_at))[:16]}"`。**改了 metrics 内容不会自动重算历史 run** |
| 回测引擎 | `app/strategy/backtest.py`（920 行）+ `app/strategy/metrics.py`。口径：`rebalance_days=20`、`max_positions=20` 等权、基准 `sh000300`、**T 收盘出信号 → T+1 开盘成交**、佣金 0.00025（最低 5 元）、印花税 0.001（卖出）、滑点 0.001、初始 100 万、整手向下取整、涨跌停/停牌判定为不可成交、估值用后复权 |
| 回测指标 | `metrics.py` 的 `METRIC_KEYS` = `total_return annual_return max_drawdown sharpe sortino calmar win_rate profit_loss_ratio turnover trade_count benchmark_return excess_return volatility`，引擎额外写 `metrics["adjusted"]`；胜率基于 FIFO 回合；rf=0，年化 ×√252 |
| 回测落库 | `backtest_run`：`bt_id algo_code start_date end_date params metrics equity_curve trades status error elapsed_ms`；`equity_curve = [{date, nav, benchmark}]`，`trades = [{date, code, name, side, price, shares, amount, fee, reason}]`（`reason ∈ rebalance|limit_down|suspended|no_data|limit_up`，被拒单 `shares=0`） |
| ⚠️ 前视检测会拦你 | `backtest.py` 的 `detect_lookahead_usage()` / `assert_no_lookahead()` **静态扫描 `app/strategy/*.py` 里的 `.shift(-`**。前瞻收益统计属于事后计算，必须放在扫描路径之外（如 `app/engine/`），并且**任何情况下都不要用 `.shift(-1)` 这类写法** |
| 指数 | 表 `market_indices`（PK `index_code, trade_date`）；采集 `collect_indices()` 遍历 `app/core/constants.py` 的 `INDEX_MAP` 里 `is_a_share=True` 的项，逐个 `ak.stock_zh_index_daily(symbol=index_code)` 拉全历史再过滤目标日；`BENCHMARK_INDEX = "sh000300"` |
| ⚠️ `INDEX_MAP` 现有 A 股指数 | 仅 `sh000001 上证指数 / sz399001 深证成指 / sz399006 创业板指 / sh000688 科创50 / sh000300 沪深300`（+ 美股 `^DJI ^GSPC ^IXIC`，由 `include_us` 控制）。**中证500 `sh000905`、中证1000 `sh000852` 不在里面** |
| 市场宽度已有半成品 | `app/engine/report_input.py` 的 `_load_breadth(session, trade_date)` 已算出 `{up down flat limit_up limit_down median_pct total}`，**但只喂给 AI 日报，未落库、无任何接口暴露** |
| 没有市场温度 | 全库无 `temperature/breadth/涨跌家数占比` 相关的表、列、接口或任务（`market_temperature` 之类要新建） |
| 行情大盘接口 | `GET /api/v1/market/dashboard` 返回 `indices us_indices sectors heatmap_data top_sectors bottom_sectors margin_data report freshness stats{scored_sectors,total_sectors,counted_signal_count}`；`GET /market/indices?trade_date=&days=`（`days=1` 给 `items`，`days>1` 给 `series{index_code:[...]}`）；`GET /market/sectors?trade_date=&sector_type=`；`GET /market/sector/{code}` |
| 调度 | `app/worker.py` 单进程 APScheduler（Asia/Shanghai），`pg_try_advisory_lock(hashtext('gfna:{name}'))` 互斥；现有任务：`daily_pipeline` 15:36、`algos` 16:30、`backfill` 02:00、`news_crawl` 间隔分钟级。仅 `SCHEDULER_ENABLED=1` 时启动 |
| 为什么是 15:36 | `config.SNAPSHOT_READY_*` 默认 15:30，留缓冲；**新加依赖日线的日任务必须排在 snapshot 落库之后** |
| ETL 编排 | `app/pipeline.py`：`DEFAULT_DAILY_STEPS = ("calendar","universe","index","sector","sector_members","snapshot","moneyflow","margin","features","scores","sector_scores","report","algos")`；`TASK_DATASET` 把 task 映射到 `meta_data_quality.dataset` key；`_dispatch()` 里**惰性 import**；手工触发 `POST /api/v1/admin/ingest/{task}`（受 `INGEST_TASKS` 白名单约束，`HEAVY_TASKS` 自动转后台） |

### 1.3 环境与命令

```bash
# 后端（Windows Git Bash；仓库根有 .venv）
cd backend && ../.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
cd backend && ../.venv/Scripts/python.exe -m pytest tests -q        # backend/tests 多数需要 DB
cd C:/docker-projects/grna && .venv/Scripts/python.exe -m pytest tests -q   # 仓库根 tests：纯逻辑，不需要数据库
cd backend && ../.venv/Scripts/python.exe -m alembic upgrade head   # 走 DATABASE_URL_SYNC
cd backend && ../.venv/Scripts/python.exe scripts/pipeline_cli.py <task>

# 前端
cd frontend && npm run dev      # vite 配置端口 5173，/api 代理到 http://localhost:8000
cd frontend && npm run build    # tsc -b && vite build —— 每次交付前的硬门槛
docker compose up -d --build    # 全栈，首次 5–10 分钟
```

- `main.py` 为 Windows 设了 `WindowsSelectorEventLoopPolicy`，**不要移除**。
- Git Bash 里 `pkill` / `lsof` / `ss` 常不可用；换端口占用请用 `netstat -ano` + `taskkill //PID <pid> //F`（注意 MSYS 路径转换会把参数吃掉）。
- 任何跨小时的回填/重算任务：后台运行 + 完成通知，**禁止在前台写 sleep 轮询**；必须逐批提交（每批一个事务）并落 checkpoint、支持断点续传；跑前先验证"断点说已完成 ↔ 库里真有数据"。

---

## 2. 全局工程约束（15 个任务都适用，违反即返工）

1. **口径先于代码。** 涉及价格/收益的写法，动手前先确认基准是未复权还是后复权：`fact_stock_daily.close` 未复权、`adj_factor` 后复权、回测估值用后复权、`/stocks/{code}/kline` 目前按未复权返回。跨除息日的收益**必须**用 `close × adj_factor`。
2. **百分数不再除 100。** `pct_chg` / `turnover_rate` / `dv_ttm` / `amplitude` 落库即百分数；`coverage` 是 0–1 小数；评分是 0–10 分制。前端渲染只加 `%`，不做二次缩放。
3. **单位写明。** `amount` 元、`volume` 股、`market_cap` 元、`fact_moneyflow` 元、`margin_trading` 亿元。跨单位聚合时在 SQL 注释里标出换算。
4. **新表/新列一律 alembic revision**，命名 `YYYYMMDD_NNNN_描述.py`；分区表禁止 `create_all` 改结构；新迁移必须同时给出可重复执行的**回填脚本**（若需要历史数据）。
5. **NULL 不是 0。** 同花顺板块 `up_count` 为 NULL、`main_net_t1..t4` 为空、新股 <60 日、`INDEX_MAP` 新成员历史不足 60 日 —— 一律返回 null 并在 UI 显式标注"数据不足"，禁止把缺失当 0 参与排名或求均值。
6. **SCD2 时间对齐。** 凡"按某日统计某板块成分"，必须带 `effective_from <= d AND (effective_to IS NULL OR effective_to > d)`。
7. **信号统计必须排除回测 run。** `stg_signal ⋈ stg_run` 后过滤 `is_backtest IS FALSE`，否则天梯/共识会重复计数。
8. **不写前视代码。** 不用 `.shift(-n)`；事后前瞻统计只放 `app/engine/`（避开 `app/strategy/*.py` 静态扫描），并在模块 docstring 写明"仅供事后统计，禁止被 strategy 层 import 为特征"。
9. **接口契约先行。** 新增/变更端点先改 `docs/V5-API-CONTRACT.md`（响应字段、错误码、Freshness 是否附带），再写实现，前后端同一份。
10. **依赖最小化。** 除 T4 明确要求 `lightweight-charts` 外，**不新增任何 npm/pip 依赖**。treemap 布局、sparkline、命令面板全部自研。
11. **不引 CDN、不联网取资源。** 前端所有资产本地打包（离线可用是既有约束）。
12. **保持既有 API 兼容。** 现有字段只增不改不删；`extractList` 的多形态兼容、`request` 的重试语义不得改动。
13. **每任务一次提交**，message 形如 `T3 市场温度计：落库 + 接口 + 前端温度计组件`；提交前依次通过 `pytest` → `npm run build` → 浏览器实测（见 §5 验收协议）。
14. **禁止顺手清理。** 不改与本任务无关的文件、不做重命名式重构、不删除 A1/A2 相关历史注释。
15. **注释只写"为什么"**，尤其是价格口径、单位换算、排除回测 run、避开前视扫描这类隐性约束。

---

## 3. 任务总览与执行顺序

> 任务编号刻意用 `T1…T15`，**不要用 A/B 前缀** —— 项目里 A3/A4/B1–B5 是策略代码，撞名会毁掉沟通。

| # | 任务 | 层 | 优先级 | 工作量 | 依赖 |
|---|---|---|---|---|---|
| T1 | 指数多周期对比条（含 中证500/1000） | 行情 | P0 | 1 天 | — |
| T2 | 个股 K 线升级为 lightweight-charts 蜡烛图 | 行情 | P0 | 1 天 | — |
| T3 | 市场温度计（0–100 温度分） | 行情 | P0 | 1.5 天 | — |
| T4 | 板块热力图升级为 treemap | 行情 | P0 | 2 天 | T1(基准相对强度口径) |
| T5 | 策略天梯（信号前瞻收益与胜率） | 算法 | P0 | 2 天 | — |
| T6 | 多策略共识榜 | 算法 | P1 | 0.5 天 | — |
| T7 | 信号逐条解释 | 算法 | P0 | 2 天 | — |
| T8 | 自选股 + 站内信号提醒 | 算法 | P1 | 1 天 | T6 或 T5 |
| T9 | 回测面板增强（月度/回撤/滚动超额/行业暴露） | 算法 | P1 | 1 天 | T2 |
| T10 | 参数敏感性矩阵 | 算法 | P1 | 2 天 | T9 |
| T11 | 纸面组合跟踪 | 算法 | P1 | 2 天 | T5、T9 |
| T12 | 统一设计系统与涨跌语义 token | 视觉 | P0 | 1 天 | — |
| T13 | 骨架屏替换 spinner | 视觉 | P2 | 0.5 天 | T12 |
| T14 | 表格内嵌 30 日 sparkline | 视觉 | P2 | 0.5 天 | T12 |
| T15 | Ctrl+K 命令面板 | 视觉 | P1 | 1 天 | T12 |

**执行顺序：T1 → T2 → T12 → T3 → T4 → T5 → T6 → T7 → T9 → T13 → T14 → T8 → T15 → T10 → T11。**
把 T12（设计系统）提前到视觉首位、其余穿插，是因为涨跌语义与 token 一旦定型，后面每个组件都会引用；T10/T11 最重且依赖上游，放最后。

**每完成一个任务必须停下**：汇报「改了什么 / 自测结果 / 遗留风险」，等确认再继续。不得连续实现多个任务。

---

## 4. 任务详述

### 阶段一 · 市场行情呈现

#### T1 指数多周期对比条

**目标**：一屏看清各指数在 1 日 / 5 日 / 20 日 / 60 日 / 年初至今 的表现，并给出"相对沪深300 强弱"，让"今天谁在领涨"变成零思考。

**后端改动**

1. `app/core/constants.py` 的 `INDEX_MAP` 增加 `"sh000905": ("中证500", True)`、`"sh000852": ("中证1000", True)`。`collect_indices()` 遍历 `INDEX_MAP`，无需改采集逻辑。
2. **必须回填这两个指数的历史**（否则 20d/60d/YTD 全空）：写一次性脚本 `backend/scripts/backfill_indices.py`，按 `INDEX_MAP` 全量历史入库（`market_indices` 非分区表，直接 upsert），完成后更新 `meta_data_quality` 的 `index_daily` 行。分批提交 + checkpoint，参照 §1.3 纪律。
3. 新增纯函数 `app/engine/index_perf.py`：
   ```python
   def window_returns(closes: list[tuple[date, float]], as_of: date) -> dict[str, float | None]
   # 返回 {"ret_1d","ret_5d","ret_20d","ret_60d","ret_ytd"}
   ```
   - 周期按**交易日**计数（用 `market_indices` 自身的历史行，缺失即视为数据不足 → null），不是自然日。
   - `ret_ytd` = 相对**当年首个交易日前一日**收盘；若当年历史不足则 null。
   - 历史长度不足该周期时返回 **null**，不返回 0。
4. `GET /api/v1/market/dashboard` 的 `indices` 每一项**增列**（原有字段一个不动）：
   `ret_1d ret_5d ret_20d ret_60d ret_ytd rel_300_20d rel_300_ytd`
   其中 `rel_300_*` = 本指数该周期收益 − 沪深300 同周期收益（百分点，百分数口径）。`us_indices` 只给绝对收益，`rel_300_*` 一律 null（跨市场比较无意义）。
   一次 SQL 取回 `days` 覆盖的最长窗口（YTD 可能 240 个交易日）后在 Python 侧切片，别做 N 次查询。
5. 同步补 `GET /market/indices?days=1` 的 `items`，两个接口共用同一构造函数。

**前端改动**

6. `components/IndexCard.tsx` 的 `IndexCardData` 增加上述字段（全部可选，缺数据渲染 `—`）。
7. 布局改为**对比条**：每行一个指数，行内 5 个小格显示各周期百分比 + 末列"相对300"徽章；红涨绿跌走 `priceColorClass`（T12 之前先用现有 `priceColorClass` 保持一致）。
8. 增加一列**同期排名**（同周期内 A 股指数间的名次，1/7 这种形式），比绝对数字更快传达强弱。
9. `pages/DashboardPage.tsx` 里 `toIndexCard` 适配器补齐新字段；A 股区与美股区继续分块，美股不显示相对强弱。

**硬性要求**

- 中证500/1000 回填完成前，前端允许它们出现但所有窗口显示 `—`，并在卡片底部注明"该指数历史回补中"。
- 不改 `market_indices` 表结构（本任务不需要迁移；若 T4 需加列则一并处理）。

**验收**

- `curl localhost:8000/api/v1/market/dashboard` 的 `indices` 每项含 7 个新字段；7 个 A 股指数的 `ret_ytd` 与手算 `(末日close/年初前收盘-1)*100` 一致（误差 <0.01pp）。
- 挑一个创业板指 60 日窗口，用 `/market/indices?days=250` 的 `series` 手工核对 `ret_60d`。
- 浏览器打开 `/dashboard`：新增两指数显示完整，缩放无换行错位。

---

#### T2 个股 K 线升级为真正的蜡烛图

**目标**：把 `StockDetailPage` 的收盘价折线换成可交互蜡烛图 —— 缩放、平移、十字光标、成交量副图、MA/BOLL 叠加、回测买卖点标记。这是"专业感"性价比最高的一处。

**依赖**

1. 新增依赖 **仅** `lightweight-charts`（TradingView 开源，Apache-2.0）。装完立刻 `npm run build` 记录 gzip 体积增量并在汇报中给出（预期 ~45KB gz 量级）。除它以外不加任何包。
2. 确认它是纯 ESM、Vite 5 可直接打包、**不依赖任何浏览器外网请求**。若 `npm run build` 报 worker/CORS 类问题，停下来汇报，不要改用 CDN。

**后端改动**

3. `GET /api/v1/stocks/{code}/kline` 的返回项**增列**：`boll_mid boll_upper boll_lower`（来自 `fact_feature_daily`）。`KlineItem` 现有 OHLC/ma5/ma20/ma60 保持不变，前端类型同步扩展。
4. 明确并在响应里加一个字段说明复权口径：`adjust_basis: "raw" | "hfq"`。**先读 `app/api/v1/stocks.py` 与 `app/engine/features.py` 确认现在 kline 与 MA 到底算在哪种价格上**，把结论写进接口注释和 `V5-API-CONTRACT.md`；这一步不许猜。
5. 新增 `GET /api/v1/backtest/{bt_id}/trades?code=`（复用 `backtest_run.trades`，按 code 过滤后返回，字段与 trades 元素一致，附 `bt_id`/`algo_code`）。**不重新计算回测。**

**前端改动**

6. 新建 `components/KlineChart.tsx`（**导出**，取代 `StockDetailPage.tsx` 里那个未导出的局部 `KlineChart`），实现：
   - `CandlestickSeries`（红涨绿跌，颜色取 CSS 变量）+ `HistogramSeries` 成交量（独立价格轴/`priceScale: { scaleMargins }` 压在下方 20%）。
   - 叠加开关：MA5/MA20/MA60、BOLL 三线（`LineSeries`，勾选状态存 localStorage，默认 MA 开、BOLL 关）。
   - 十字光标 + 跟随提示条（OHLC、涨跌幅、成交量、成交额），数据缺失显示 `—`。
   - 交互：滚轮缩放、拖拽平移、双击复位、`timeScale().fitContent()` 初始化；周期按钮 60/120/250 根。
   - 标记：`series.setMarkers()` 画买卖点（买=向上三角，卖=向下），点击标记高亮对应 K 线并在提示条展示 `price/shares/amount/fee/reason`。
7. **价格轴一致性（本任务最容易出错的点）**：标记价来自回测成交（T+1 开盘价，与图表同源口径）。若图表切到复权显示，markers 必须按同一 `adj_factor` 换算后落位；实现方式：图表数据与标记都走同一个换算函数，禁止一边换算一边不换算。UI 角落固定标注当前口径（未复权/后复权）。
8. `reason ∈ limit_up/limit_down/suspended/no_data` 且 `shares=0` 的**被拒单**不得画成成交，用灰色小圆点 + tooltip 说明"信号命中但无法成交"。
9. 生命周期：`useEffect` 里创建图表、卸载时 `remove()`；容器用 `ResizeObserver` 自适应宽度。Vite dev 下 React.StrictMode 会双挂载，**必须验证不出现两个叠加的 chart 实例**（这是 StrictMode 下的经典症状）。
10. 数据加载中用骨架占位（T13 前先用 `.skeleton` 类），不要 spinner；`recharts` 在本页彻底移除。

**范围纪律**

- **不要**从 `package.json` 移除 recharts —— `AlgoRadarChart.tsx`、`AlgoBarChart.tsx`、`ScoreRanking.tsx`、`BacktestPanel.tsx` 仍在用。
- **不要**在本任务里做指标计算前端化，MA/BOLL 一律来自后端。

**验收**

- `/stocks/600000`：蜡烛方向与 `pct_chg` 符号一致；十字光标数值与 tooltip 与后端 JSON 逐字段相符。
- 叠加 BOLL 后切换 60/250 根、缩放平移不出现空白/错位；StrictMode 下 DOM 中只有一个 canvas 容器。
- 选一个已完成的回测，标记落在正确的 K 线上（抽查 3 笔：买入价 = 信号次日 open）。
- `npm run build` 通过并给出体积 delta。

---

#### T3 市场温度计

**目标**：一个 0–100 的"温度分"回答"现在市场能不能做"。全部由现有日线 + 特征表现算，不依赖任何新数据源。

**输入指标（六项，先落成子项、再加权）**

| 子项 | 定义 | 取数 |
|---|---|---|
| 上涨占比 | `pct_chg > 0` 的家数 / 有效家数 | `fact_stock_daily` |
| 涨停家数 | `is_limit_up` 计数（含 20cm 差异，见下） | `fact_stock_daily` |
| 跌停家数 | `is_limit_down` 计数 | `fact_stock_daily` |
| 站上 MA20 比例 | `close` > 同日 `fact_feature_daily.ma20` 的家数 / 有效家数 | 两表 join |
| 量能比 | 当日全市场 `amount` / 20 个交易日全市场 `amount` 均值 | `fact_stock_daily` 窗口 |
| 指数波动率 | `BENCHMARK_INDEX` 的 `pct_chg` 20 日标准差（年化，×√252） | `market_indices` |

**后端改动**

1. **复用，不要重写 SQL**：`app/engine/report_input.py` 的 `_load_breadth()` 已产出 `{up,down,flat,limit_up,limit_down,median_pct,total}`。把它抽到公共模块（建议 `app/engine/breadth.py`），`report_input.py` 改为 import 它 —— 注意这是**搬移 + 保留原调用点行为**，日报输出必须逐字段不变。
2. 新建 `app/engine/market_temperature.py`：
   ```python
   async def compute_temperature(session, trade_date) -> TemperatureResult
   # components: 六个子项的原始值 + 各自 0-100 归一分 + 说明
   # score: 加权合成，0-100，保留 1 位小数
   ```
   - **归一化必须是显式、单调、可解释的分段映射**（例：上涨占比 50%→50 分、70%→80 分、30%→20 分），权重与断点全部写成 `app/core/constants.py` 里的常量并加注释说明依据。禁止 `min-max` 逐日自缩放（会让"温度"失去跨日可比性）。
   - 跌停/高波动是**降温**项：权重可为负或在合成后扣减，代码里明确写出。
   - 任一子项数据不足（新股期、特征覆盖率 < `0.5` 沿用 `_assert_data_ready` 同阈值）→ 该子项返回 null，**总分同时返回 `confidence: "partial"` 并给出参与合成的子项数**。禁止用 0 代入。
   - 涨跌幅统计需剔除停牌（`is_suspended`）；涨停判定不自行重算、直接用 `is_limit_up`（`limit_prices()` 已处理主板/创业/科创/北交所差异）。
3. 新建表 `market_temperature_daily`（alembic revision）：
   ```
   trade_date DATE PRIMARY KEY
   up_count INTEGER, down_count INTEGER, flat_count INTEGER, total_count INTEGER
   limit_up_count INTEGER, limit_down_count INTEGER, median_pct NUMERIC(8,3)
   above_ma20_ratio NUMERIC(6,4)      -- 0-1 小数，与 coverage 同口径，注释写明
   amount NUMERIC(24,2)               -- 元
   amount_vs_ma20 NUMERIC(8,3)        -- 倍数
   index_vol_annual NUMERIC(8,3)      -- 百分数
   temp_score NUMERIC(5,1)
   confidence VARCHAR(8)              -- ok | partial | insufficient
   components JSONB                   -- 子项原值/得分/权重/说明
   src VARCHAR(16), updated_at TIMESTAMPTZ
   ```
4. 接进管线：`app/pipeline.py` 的 `DEFAULT_DAILY_STEPS` 追加 `"temperature"`（位置在 `features`/`scores` 之后，因为要读 MA20），并在 `TASK_DATASET` 加 key **`market_temperature`**；`_dispatch()` 用惰性 import。`worker.py` 无需新增 cron（跟着 `daily_pipeline` 15:36 跑即可）。
5. 历史回填：`backend/scripts/backfill_temperature.py --start --end --batch-days`，逐日 upsert、分批提交、落 checkpoint；回填完成后用 `GET /market/temperature?days=60` 抽查连续性。
6. 新接口 `GET /api/v1/market/temperature?trade_date=&days=`：
   - `days=1` → `{trade_date, ...当日全字段, freshness}`
   - `days>1` → `{trade_date, series:[{trade_date, temp_score, above_ma20_ratio, amount_vs_ma20, ...}]}`
   - 不传日期 → 最新有数据的交易日；全无数据 → 按契约返回 `DATA_NOT_READY`（409）而不是空对象。

**前端改动**

7. 新建 `components/MarketTemperature.tsx`：一个大号数字 + 半圆仪表（**纯 SVG 手动画弧**，`stroke-dasharray`，不引图表库）+ 六个子项细条（每项显示原值、得分、方向箭头）+ 底部 60 日 `temp_score` sparkline（内联，T14 抽象成公共组件时回收）。
8. 温度区间语义色带：`0-20 极冷 / 20-40 偏冷 / 40-60 中性 / 60-80 偏热 / 80-100 极热`，阈值常量放 `frontend/src/lib/format.ts` 或新 `lib/market.ts`，并导出 `temperatureLabel()` 供复用。
9. 挂到 `pages/DashboardPage.tsx` 顶部（指数条之上或同排），新增 `src/api/market.ts` → `getTemperature()` + `hooks/useTemperature.ts`（复用 10 分钟 `refetchInterval` 节奏，与 `useDashboard` 一致）。
10. `confidence !== "ok"` 时组件顶部显示浅色提示条，说明是哪几项缺数据 —— **这是可信度的关键，不许省略**。

**验收**

- 手算校验：挑 3 个交易日，用 SQL 直接数 `pct_chg>0` 家数与接口 `up_count` 一致；`above_ma20_ratio` 与手工 join 结果一致（±0.0001）。
- 除权除息密集日抽查 `median_pct` 与日报文本里的宽度数字一致（证明抽公共模块没改行为）。
- 断掉 `fact_feature_daily`（临时用一个特征未生成的历史日）→ 接口返回 `confidence: insufficient`、`temp_score` 为 null，前端不出现 `NaN` 或 0 分。
- 温度分跨日可比：连续 60 日曲线平滑合理，无"某天突然 0 分"。

---

#### T4 板块热力图升级为 treemap

**目标**：从方块列表升级为面积=市值、颜色=涨跌幅的 treemap，叠加资金流与 20 日相对强度，一眼看出主线。

**数据准备（本任务一半以上的工作量在这里）**

1. 板块市值、板块资金流**都没有现成列**，必须新建。给 `fact_sector_daily`（非分区表，迁移安全）**增列**：
   ```
   market_cap NUMERIC(24,2)      -- 板块成分股市值合计，元，按当日 SCD2 名单
   float_cap  NUMERIC(24,2)
   main_net_inflow NUMERIC(24,2) -- 板块成分股 main_net_t0 合计，元
   rs_20d NUMERIC(8,3)           -- 板块 20 交易日收益 − 沪深300 同窗口收益，百分点
   member_count INTEGER          -- 当日名单成分数（用于"名单不完整"判定）
   ```
2. 新建 `app/engine/sector_agg.py`：`aggregate_sector_metrics(session, trade_date)`，一次 SQL 按 SCD2 名单聚合 `fact_stock_daily.market_cap` 与 `fact_moneyflow.main_net_t0` upsert 入库，只写有数据的列（NULL 保持 NULL）。
   - 名单来源严格走 SCD2 时间条件；成分数 < 3 的板块 `market_cap`/`main_net_inflow` 置 NULL（噪声太大）。
   - `rs_20d` 需要板块 `close` 序列与 `sh000300` 同窗口 close，两者任一不足 21 个交易日 → NULL。
3. 接进管线：`DEFAULT_DAILY_STEPS` 追加 `"sector_agg"`（放在 `snapshot`/`moneyflow` 之后、`sector_scores` 之前），`TASK_DATASET` 增 key `sector_metrics`。历史回填脚本 `backend/scripts/backfill_sector_metrics.py`，按日分批 + checkpoint。
4. `GET /api/v1/market/sectors` 的 items **增列**：`market_cap float_cap main_net_inflow rs_20d member_count`（原字段一个不动）。

**前端改动**

5. 新建 `frontend/src/lib/treemap.ts`：**手写 squarified treemap 布局纯函数**
   ```ts
   export type TreeInput = { id: string; value: number; }[]
   export function squarify(items: TreeInput, box: {x,y,w,h}): { id,x,y,w,h }[]
   ```
   禁止引 d3-hierarchy / echarts（d3 目前只是 recharts 的传递依赖，**不允许**直接 import 传递依赖）。必须有单测（`frontend/src/lib/treemap.test.ts` 或就近测试）：面积守恒、不重叠、比例退化（单元素、全等值、含 0/负值输入被过滤）。
6. `components/SectorHeatmap.tsx` 重构为 treemap：
   - **面积 = `market_cap`**（缺失/非正的板块剔除并计数，在角落显示"已剔除 N 个（数据不足）"）。
   - **颜色 = `pct_chg`**：把现有硬阈值 `heatColor()` 改成**以 0 为中轴的对称插值**（红涨绿跌），插值函数放 `lib/`，供 treemap 与个股格子复用；色标两端固定映射到 ±5%（超过则饱和），并在图例上写明"色阶 ±5% 饱和"。
   - 格子内标签：板块名（截断）+ 涨跌幅；面积小于阈值的格子只显示色块，hover 出 tooltip 展示 `pct_chg / 主力净流入 / rs_20d / 涨跌家数 / 龙头 / 三面评分`。
   - 提供**切换维度**：`industry` / `concept`（走现有 `sector_type` 参数）；面积维度可切 `market_cap` ↔ 等权（市值缺失时自动回退等权并标注）。
   - `up_count`/`down_count` 为 NULL 的板块（同花顺源）：tooltip 显示"涨跌家数不适用"，**不得**渲染成 0。
   - 点击板块 → 进入现有板块详情（`/market/sector/{code}` 数据已在用，沿用现有交互路径；若暂无详情页则弹出内嵌抽屉，别新建路由）。
   - 保留 `framer-motion` 入场，但**去掉逐格 stagger**（方块数上百时动画会拖累首屏）；只在容器层做一次淡入。尊重 `prefers-reduced-motion`。
7. 规模控制：默认只渲染前 N 大（N 初始 80）板块，其余折叠进"其他"并给出"显示全部"开关；`member_count` 与 `sector_type` 计数在标题栏展示。**必须实测 concept 全量时的渲染耗时**，超过 ~100ms 交互就继续降 N。

**验收**

- 数据库层：某板块 `market_cap` 与手工 SQL（SCD2 名单 join 当日 `fact_stock_daily.market_cap`）一致；`main_net_inflow` 与个股 `main_net_t0` 合计一致。
- 视觉层：面积可加性目测成立；同日切换 industry/concept 不残留旧格子。
- 相对强度：抽 1 个行业板块，`rs_20d` 与手算（板块 20 日收益 − 沪深300 20 日收益）一致。
- 数据不完整时页面明确标注剔除数与"不适用"，无 0 假象；`npm run build` 通过。

---

### 阶段二 · 算法中心

#### T5 策略天梯

**目标**：每天统计各策略信号在 T+1 买入后 5/10/20 日的前瞻收益与胜率，滚动展示。回答"这周 B1 和 B2 哪个能用"，而不是只给 7 张卡片。

**核心口径（不许自行改，改了必须回来更新本节）**

- 与回测引擎完全一致：**T 日收盘出信号 → T+1 交易日开盘买入**。
- 买入价 = T+1 的 `open × adj_factor`；H 日收益 = 自 T+1 起第 H 个交易日收盘（`close × adj_factor`）/ 买入价 − 1。
- 超额 = 个股 H 日收益 − 基准 `sh000300` 同窗口收益（基准用 `market_indices.close`，指数无需复权）。
- **成熟度**：5/10/20 日窗口需要各自走完。未走完的窗口该条记录记 `mature_5=false` 等，**统计口径只计已成熟样本**，同时返回 `immature_count` 让 UI 说明"最近一批还在路上"。
- 停牌/一字板：信号后 T+1 若 `is_limit_up` 或 `is_suspended` → 标记 `fillable=false`，**排除出胜率与均值**（否则天梯会高估），单独返回 `unfillable_count`。

**后端改动**

1. **放在 `app/engine/`，不要放 `app/strategy/`** —— 后者被前视静态扫描（`.shift(-`）拦，且前瞻收益属于事后统计，不该出现在出信号的路径里。新建 `app/engine/signal_perf.py`，模块 docstring 写明"仅供事后统计，禁止被 strategy 层 import 为特征"，且**全文件不得出现 `.shift(-`**。
2. 新建表 `stg_signal_perf`（alembic revision）：
   ```
   algo_code VARCHAR(16), signal_date DATE, code VARCHAR(6)   -- 联合主键
   rank INTEGER, score NUMERIC(12,4)
   entry_date DATE, entry_price NUMERIC(16,6)
   ret_5 NUMERIC(10,4), ret_10 NUMERIC(10,4), ret_20 NUMERIC(10,4)
   excess_5 NUMERIC(10,4), excess_10 NUMERIC(10,4), excess_20 NUMERIC(10,4)
   mature_5 BOOLEAN, mature_10 BOOLEAN, mature_20 BOOLEAN
   fillable BOOLEAN, industry_l1 VARCHAR(64)
   computed_at TIMESTAMPTZ
   ```
   索引：`(algo_code, signal_date)`、`(algo_code, mature_20, signal_date)`。
3. 计算逻辑：`stg_signal ⋈ stg_run` **且 `is_backtest IS FALSE`**（§2.7），`upsert` 幂等（同一 `(algo_code, signal_date, code)` 重跑结果一致）。历史一次回填到 `config.HISTORY_START`，之后每日增量：回填/增量同一函数，靠 `signal_date` 区间参数区分。
4. 每日**重算最近 25 个交易日**窗口（覆盖新成熟样本与除权后的复权因子更新），不要只算昨天 —— 断点丢数据、增量漏成熟是这类任务的典型故障。
5. 接入管线与调度：`DEFAULT_DAILY_STEPS` 追加 `"signal_perf"`（在 `algos` 之后）；由于 `algos` 是 16:30 独立 cron，`worker.py` 新增 `CronTrigger(hour=17, minute=10)` 的 `signal_perf` 任务，同样经 `_with_lock` 走 advisory lock。
6. 新接口 `GET /api/v1/algo/ladder?window=60&horizon=20&trade_date=`：
   ```json
   { "trade_date":"2026-10-27","window":60,"horizon":20,
     "items":[{ "algo_code":"B1","algo_name":"…",
                "sample_signals":412,"sample_stocks":260,
                "avg_ret":3.41,"median_ret":2.10,"win_rate":0.573,
                "avg_excess":1.88,"best":28.4,"worst":-19.2,
                "immature":35,"unfillable":12,
                "spark":[{"trade_date":"…","win_rate":0.52}, …] }],
     "benchmark":"sh000300","freshness":{…} }
   ```
   - 胜率 = `ret_H > 0` 的占比（**与回测的 FIFO 回合胜率不是同一口径，UI 必须标注"信号级 5/10/20 日胜率"**，别让用户混淆）。
   - 样本数 < 30 的策略标注 `low_sample: true`，UI 弱化显示。
   - `window` 支持 20/60/120，用于观察"近期是否失效"。

**前端改动**

7. 新建 `pages/LadderPage.tsx`（路由 `/ladder`，加入 `NAV_ITEMS`）或作为 `AlgoCenter.tsx` 顶部首屏 —— **二选一，默认做成 `/algo-center` 顶部区块**，因为用户决策场景就在那里；若该页 DOM 已很重则独立路由。
8. 天梯表格：列 = 排名徽章 / 策略名 / 5d / 10d / 20d 三组（每组 平均超额 + 胜率 + 样本数）/ 近 20 期胜率 sparkline（复用 T14 组件）；点击行进入现有策略详情。窗口切换 20/60/120 与 horizon 切换在同一控件组。
9. 明确显示"数据截至 T 日，最近 N 条信号尚未成熟"。

**验收**

- 抽 1 个策略、1 条信号，用 SQL 手工算 `ret_20` 与接口一致（含复权）。
- 制造未成熟场景（最新交易日）→ `immature` 非零且 `win_rate` 未被稀释。
- 重复执行同一日计算两次，结果完全一致（幂等）。
- 确认 `stg_signal_perf` 里没有来自回测 run 的行：`select count(*) … where (algo_code,signal_date,code) in (select …where is_backtest)` 应为 0。
- 全量回填耗时与行数在汇报中给出。

---

#### T6 多策略共识榜

**目标**：同一只票被几个策略同时命中，按共识数排序。只需 join 现有信号表，成本极低、决策价值很高。

**后端改动**

1. `app/strategy/runner.py` 或新建 `app/engine/consensus.py`（**纯读查询，放 `app/engine/` 更合适**）：
   ```python
   async def consensus_signals(session, trade_date, *, min_count=2, sector_type=None, limit=100)
   ```
   - 取**最新交易日**（沿用 `_latest_trade_date` 惯例）当日全部成功 run（`stg_run.status='success'` 且 `is_backtest IS FALSE`）的 `stg_signal`；
   - `GROUP BY code` → `hit_count = count(distinct algo_code)`、`algos = jsonb_agg(jsonb_build_object('algo_code',…,'rank',…,'score',…))`、取 `max(rank)` 最小值作为"最优名次"、`avg(score)` 无意义故**不返回跨策略平均分**（各策略 score 量纲不同，见 `_SORT_SPEC`，混算会误导）；
   - 附 `dim_stock.industry_l1/board/ownership`、当日 `pct_chg`、`composite_score`、命中策略各自的 `metrics` 摘要（前 3 个键值，避免响应爆炸）。
2. 新接口 `GET /api/v1/algo/consensus?trade_date=&min_count=&limit=`；`min_count` 取值 2–7 且必须校验（非法 422）。返回 `items` + `total_algos_evaluated` + `freshness`。
3. **性能**：单条 SQL 完成，禁止 N×策略循环查询。当日 7 个策略全量信号约千级，`limit=100` 下响应必须 <100ms。

**前端改动**

4. `AlgoCenter.tsx` 新增"共识榜"区块（默认折叠在策略卡片之下，因为天梯 T5 才是首要信息）：
   - 表格列：代码/名称 / **命中数徽章（●●●●○○○）** / 行业 / 当日涨跌 / 综合分 / 命中策略列表（可点开跳转各策略该票的 `metrics`）/ 加入自选（复用 T8 的 store）。
   - 命中数用色阶，7/7 满命中视觉最重；`min_count` 滑块 2–5。
   - 明确文案："各策略评分量纲不同，榜单按命中策略数排序，不做跨策略分数平均。"

**验收**

- 与手工 SQL 结果行数一致；把 `min_count=1` 时 `hit_count` 分布合理（不可能出现某票 hit_count=8 > 策略总数）。
- 某策略当日 run 失败（临时置 `status='failed'` 测试）→ 该策略不出现在共识计数里。
- 响应时间 <100ms（附实测数字）。

---

#### T7 信号逐条解释

**目标**：回答"为什么是它"。B 系列逐条列出触发的每个条件（EMA 金叉、放量、J 值超卖…），A3 展示分项评分贡献。

**设计原则：单一事实源 —— 这是本任务的全部难点**

> 现在 `strategy_mask()` 是硬编码 pandas 掩码，而 `condition.py` 的 DSL 闲置。**绝对不允许**新增一份"解释用的条件列表"与掩码并行维护 —— 一旦两者漂移，解释就是在撒谎，比没有解释更糟。
> 正确做法：把每策略的条件抽成 `ConditionGroup` 声明，**掩码由它生成**，逐条命中明细也由它评估得到。

**后端改动**

1. 在 `app/strategy/strategies.py` 里为 B1–B5（以及 B 类未来扩展）各定义一份与 `SPECS` 并列的 `CONDITION_SPECS: dict[str, ConditionGroup]`，条件语义**逐字对照现有 `strategy_mask()` 的实现**（把掩码里的每个比较搬出来，不要凭 `full_desc` 文案重写）。
2. `strategy_mask()` 改为：**先由 `CONDITION_SPECS` 产出整体布尔掩码 + 每条件列**，再与原有的少量非 DSL 可表达逻辑（如 `_market_flag(df,"b4_ok")` 这类大盘门控、行业中性化）组合。若某条件确实无法用现有 ops 表达（如跨列相关、top_pct 依赖截面），在 `condition.py` 里扩展算子而不是绕过 DSL；扩展必须补单测。
3. **行为等价性是硬门槛**：改造前后，对同一 `trade_date` + 同一参数，每个策略的 `signal_count` 与 `code` 集合必须**逐一相同**，`rank` 顺序亦相同（tie-break 仍是 `code`）。写一个临时对比脚本 `backend/scripts/compare_mask_refactor.py` 跑最近 20 个交易日，把逐策略 `old vs new` 集合差异作为交付证据；有差异一律停止并汇报。
4. 每条件在 `stg_signal.metrics` JSONB 下新增子对象（**不加表不加列**）：
   ```json
   "triggered": [
     {"key":"ema_cross_3d","label":"EMA5 近 3 日金叉 EMA20","actual":true,"required":true,"passed":true,
      "detail":"ema5=8.26 ema20=7.37"},
     {"key":"volume_ratio","label":"放量","actual":1.94,"op":"ge","threshold":1.5,"passed":true}
   ]
   ```
   `label` 用中文可读文案（来自 `CONDITION_SPECS`），`detail` 只放该条件用到的原始值。**必须全部字段可 JSON 序列化**（沿用 `strat.jsonable()`，Decimal→float，date→isoformat，禁止 numpy 类型泄漏进 JSONB）。
5. 大盘门控类条件（`b4_ok`/`b5_ok`）单独标 `"scope":"market"`，UI 分组显示"市场环境要求"。
6. **历史 run 的兼容显示**：缓存键是 `(algo_code, trade_date, params_fingerprint, data_version)`，改了 `metrics` 内容**不会**自动重算历史 run。因此：
   - 本任务上线后，对**最新交易日**全部策略调用 `POST /api/v1/algo/{code}/refresh` 强制重跑（作为部署步骤写进文档）；
   - 前端在 `triggered` 缺失时显示"该 run 为旧版本快照，无逐条依据"，不得渲染空列表或假装全部通过。
7. A3 解释：不新增计算，直接读 `fact_stock_score_daily.detail`（已存"原始因子与分位"）→ `GET /api/v1/algo/{algo_code}` 或信号明细接口里带出 `score_breakdown`：
   ```json
   {"composite":6.82,"parts":[{"key":"value","name":"价值","score":7.1,"z":0.62,"weight":0.25,"raw":{"pe_ttm":8.1}}]}
   ```
   权重与 `z` 的口径以 `app/engine/stock_score.py` 为准，**读代码确认后如实呈现，不许臆造权重**。

**前端改动**

8. 新建 `components/SignalExplanation.tsx`：按 `scope` 分两组（个股条件 / 市场环境），每条一行 `✓/✗ + label + 实际值 vs 阈值`，通过项正常色、未通过项灰（信号命中说明整体成立，个别子项显示为区间内即可，不要显示成错误）。
9. 接入现有信号表（`AlgoCenter.tsx` 的信号行）：展开行 → 解释面板；A3 行 → 分项评分贡献条（0–10 分制，用 `scorePercent()`）+ 原始因子值。
10. 每个解释面板底部固定一行小字引用策略文档（`full_desc`）与"参数快照来自 run.params"，让用户能追溯到当时的参数。

**验收**

- **等价性脚本 20 日全部策略零差异**（这是 PASS/FAIL 判据）。
- `POST /algo/B1` 返回的 signals 每项含非空 `triggered`，无 numpy/Decimal 序列化报错。
- 前端：B1 命中票的解释条目数与 `CONDITION_SPECS["B1"]` 条件数一致；旧 run 显示降级提示。
- A3 分项之和与 `composite_score` 的关系在 UI 上可自洽解释（加权/截断规则写明）。

---

#### T8 自选股 + 站内信号提醒

**目标**：命中自选股时在导航栏出红点，轮询即可，**不引入 WebSocket**。

**存储决策（已定，按此实现）**

> 项目**没有任何认证与用户体系**（backend 无 `user_id`/登录/token，前端无 watchlist/favorite 代码，唯一 localStorage 用法是 `gfna-theme`）。本任务**不建服务端用户表、不做登录**：自选股名单存 `localStorage`（key `gfna-watchlist`），提醒状态每次由服务端按提交 codes 计算。后续若引入账号，再把这套 store 换成服务端 API，接口形状保持不变。

**前端改动**

1. `frontend/src/lib/watchlist.ts`：`getWatchlist(): string[]` / `toggleWatch(code)` / `MAX_WATCHLIST = 100`（超限提示并拒绝写入）；写入包 try/catch（隐私模式 localStorage 会抛），失败时降级为内存态并在 UI 提示"本浏览器不保存自选"。
2. `hooks/useWatchSignals.ts`：`refetchInterval: 60_000`（复用 `client.ts` 的 GET 重试语义），只在页面可见时轮询（`document.visibilityState`，切后台停轮询，省电省 QPS）。
3. 自选管理 UI：个股详情页 `/stocks/:code` 头部"☆ 加入自选 / ★ 已自选"按钮；`/dashboard` 与共识榜行内快捷按钮；新建 `components/WatchlistPanel.tsx`（列表 + 移除 + 跳转 + 最近一次命中摘要）。
4. 导航栏红点：`components/Navbar.tsx` 在"自选"入口（新增 `NAV_ITEMS` 项 `/watchlist`）上渲染 badge —— 命中数（>9 显示 `9+`）。红点色用语义 token（T12 定义的 `--color-alert`），**不要与"上涨红"混淆**；`aria-label="自选股有 N 条新信号"`，键盘可达。
5. 命中判定：以 `algo_code` + `signal_date` 为去重键，只提示"上次已读之后"的新命中；已读水位存 localStorage（`gfna-watchlist-seen`），进入 `/watchlist` 页即更新水位。

**后端改动**

6. 新接口 `POST /api/v1/algo/hits`，body `{"codes": ["600000", …], "days": 10}`：
   - 校验 `codes` 为 1–6 位数字串、去重、长度 ≤ 100（超出 422），`days` ∈ [1, 60]；
   - 返回该批票近 N 个交易日的命中：`{items:[{code, name, algo_code, algo_name, signal_date, rank, hit_id:"algo_code:signal_date"}], latest_signal_date, freshness}`；
   - **必须排除回测 run**（`is_backtest IS FALSE`）；
   - 用一条 SQL（`stg_signal ⋈ stg_run` + `code = ANY(:codes)`）完成，禁止逐票循环。
   - 用 POST 而非 GET 是因为 codes 列表可能长；实现里注明这是"只读查询，语义上等价 GET"。

**验收**

- 加自选→刷新→名单持久化；清 localStorage 后功能整体降级但不报错。
- 自选票今日命中 B2/B3 → 导航栏红点 = 2；进入 `/watchlist` 后消失。
- 切到后台标签页时网络面板无轮询请求。
- `codes` 传 200 个 / 传 `"'; drop"` 分别返回 422 与校验失败，无 SQL 注入面（参数绑定，不拼字符串）。

---

#### T9 回测面板增强

**目标**：月收益热力图、回撤曲线、滚动超额收益、持仓行业分布 —— 全部可由 `backtest_run` 已存的 JSONB 派生，**不重新回测**。

**后端改动**

1. 新建 `app/engine/backtest_view.py`：`build_backtest_extras(curve, trades, daily_rows_by_code) -> dict`，从已有 `equity_curve`/`trades` 派生：
   - `monthly_returns`: `[{"ym":"2024-03","ret":4.2,"benchmark":1.1,"excess":3.1}]`；月收益用当月**首个与末个** `nav` 点相除（含期间分红已体现在后复权 nav 里），不足两点 → null。
   - `drawdown_curve`: `[{"date","nav","peak","drawdown"}]`，`drawdown = nav/运行最大峰值 − 1`（≤0，百分数）。
   - `rolling_excess_20d`: 20 交易滚动窗口 `(nav 收益 − benchmark 收益)`，样本不足 → null。
   - `industry_exposure`: 由 `trades` 重建每个调仓日的持仓（买入累加、卖出核销、`shares=0` 的被拒单不建仓），按 `dim_stock.industry_l1` 汇总市值权重 → `[{"ym 或 date","industries":[{name,weight}]}]`；行业为 NULL 归入"其他"并计数。
2. `GET /api/v1/backtest/{bt_id}` **增列**上述四块 + `extras_available: bool`；旧记录若 `trades`/`equity_curve` 为空则 `extras_available=false`。
3. `GET /api/v1/backtest/{bt_id}/export.csv` 同步加月度与回撤两个 block（导出与界面口径必须一致，避免"看到的和下载的不是同一份数"）。

**前端改动**

4. `components/BacktestPanel.tsx` 新增四个区块：
   - **月收益热力图**：年 × 12 网格，色阶 ±5% 中轴对称（复用 T4 的插值函数），单元格右上角小字显示超额；右侧末列年度合计、底部行月均。
   - **回撤曲线**：面积向下填充（recharts `AreaChart` 即可，本页已有 recharts，别为它再加依赖）。
   - **滚动超额**：折线 + 0 轴参考线。
   - **行业分布**：堆叠条（按调仓日取样，最多 12 个时点，其余合并）或 treemap 单时点快照 —— 与 T4 组件复用。
   - 四个区块各自可折叠，默认折叠"行业分布"以控制首屏 DOM。
5. 与 T2 打通：从成交明细点"在 K 线查看"跳 `/stocks/{code}?bt={bt_id}`，`StockDetailPage` 读取该 query 自动加载 T2 的买卖标记。

**验收**

- 月度收益逐月之和与 `total_return` 自洽（`(1+r1)(1+r2)…-1 ≈ total_return`，误差 <0.1pp；不一致要能解释原因，通常是区间未覆盖首尾）。
- `drawdown` 最小值的绝对值 = `max_drawdown` 指标（同一口径，若不等必须查明并说明）。
- 行业权重每期之和 = 100%（±0.1pp，剩余为现金时显式列出"现金"栏，不许悄悄抹平）。
- CSV 导出与界面数值一致（抽 2 个数）。

---

#### T10 参数敏感性矩阵

**目标**：`rebalance_days` × `max_positions` 的收益/回撤热力图。提醒用户"这个策略不是随便调参都赚钱"，顺带防止过拟合。

**后端改动**

1. **不发明新参数名**：读 `app/strategy/backtest.py` 的 `BacktestParams.from_dict` 别名表（约 `:180-213`），沿用 `rebalance_days` / `max_positions`（用户口中的"持仓天数 × 持仓数"即此二者）。矩阵轴、接口字段、CSV 表头全部用同一组名字。
2. 迁移：给 `backtest_run` **增列** `grid_id VARCHAR(36) NULL`、`grid_cell JSONB NULL`（`{"axis":"rebalance_days","value":20}` 之类）+ 索引 `ix_backtest_run_grid`。一次矩阵 = 一个 `grid_id`，每格 = 一条独立 `backtest_run`（复用全部现有引擎，零特殊路径）。
3. 新建 `app/strategy/grid.py`（此处**只调用**回测，不产生前视写法；仍要避免 `.shift(-`）：
   - `POST /api/v1/backtest/{algo_code}/grid`，body `{start_date, end_date, rebalance_days:[10,15,20,30,40], max_positions:[10,20,30]}`；
   - **格数硬上限 24**，超过 422 并说明原因（回测单格可达分钟级，N×M 不受控会拖垮 DB 连接池 —— pool_size 10 + overflow 10）；
   - 立即返回 `{grid_id, cells:[{bt_id, params, status:"queued"}], elapsed_ms:0}`，实际执行走后台任务（沿用 `admin.py` 的 `HEAVY_TASKS` 自动转后台模式 + `session_scope()`）；
   - **并发上限 2**（信号量），串行排队亦可接受；逐格提交（一格一事务），单格失败只写该格 `status='failed'`，不中断整张矩阵；
   - 断点续跑：以 `(algo_code, start, end, params)` 命中 `backtest_run` 已有成功记录时**直接复用 bt_id 不重算**（回测结果确定，重算是纯浪费）；
   - `GET /api/v1/backtest/grid/{grid_id}` → 矩阵单元格状态 + 每格 `metrics` 摘要（`total_return excess_return max_drawdown sharpe win_rate trade_count`）+ `progress {done,total,failed}`。
4. **同区间对齐校验**：所有格必须用同一 `start_date/end_date` 与同一数据版本；若某格因日历/数据变化落到不同区间，标记 `aligned=false` 并从热力图剔除。

**前端改动**

5. `components/SensitivityMatrix.tsx`，挂在 `BacktestPanel.tsx` 内"参数敏感性"标签页：
   - 双轴下拉/多选 + "开始扫描"按钮；扫描中每格显示状态点（排队/运行/完成/失败），**用轮询 3s**（复用 react-query `refetchInterval`，页面不可见时停）。
   - 完成后渲染热力图：面积格 = 超额收益（红正绿负），格内两行 = `超额 / 回撤`；同一矩阵可切换显示的指标（超额收益、最大回撤、Sharpe、胜率、交易笔数）。
   - **过拟合提示**：找出默认参数格与最优格的差值，若"默认参数明显劣于最优且邻近格方差大"，在矩阵下方固定提示：默认参数为约定口径，矩阵仅用于观察稳健性，**不建议把最优格当作实盘参数**；并显示"邻域稳健度"= 与默认格相邻 4 格指标的极差。
   - 每格点击 → 展开该 `bt_id` 的完整回测（T9 面板），让用户能看单格明细而不是只看数字。
6. 明确展示单格耗时与总计耗时，避免用户误以为"点一下就免费"。

**验收**

- 2×2 小矩阵端到端跑通，每格 `bt_id` 可独立 GET；中断后重发同参数矩阵 → 命中复用（`elapsed_ms=0` 且无新回测计算，日志可证）。
- 单格人为制造失败（临时改参数使数据未就绪）→ 其余格继续，矩阵状态正确显示 failed。
- 默认参数格（`rebalance_days=20, max_positions=20`）的指标与 T9 单次回测结果完全一致（同一格就是同一次回测，不允许两套代码路径）。

---

#### T11 纸面组合跟踪

**目标**：按当日选股自动建纸面组合，逐日跟踪净值 vs 基准。把"回测 ≠ 实盘"这句话变成可验证的事实。

**设计决策：整体重算，不做增量状态机**

> 每天把 `start_date → 今天` 整段重算一次并覆盖净值，而不是"读昨天的持仓 + 打今天的补丁"。理由：增量会累积状态漂移（除权、停牌跨日、数据回补、名单变更），漂移一旦进入净值就再也解释不清；而本引擎单段回测已是分钟级以内，每日重算成本完全可接受。**这是本任务的硬要求，不许改成增量。**

**后端改动**

1. 迁移新建表：
   ```
   paper_portfolio: portfolio_id VARCHAR(36) PK, algo_code VARCHAR(16) UNIQUE NOT NULL,
     name VARCHAR(64), start_date DATE, params JSONB, rebalance_days INTEGER, max_positions INTEGER,
     initial_capital NUMERIC(20,2), status VARCHAR(16), created_at TIMESTAMPTZ
   paper_nav: (portfolio_id VARCHAR(36), trade_date DATE) PK, nav NUMERIC(16,6), benchmark NUMERIC(16,6),
     drawdown NUMERIC(8,3), holdings JSONB, cash_ratio NUMERIC(6,4),
     pending_orders JSONB, vs_backtest JSONB, updated_at TIMESTAMPTZ
   ```
   索引 `(portfolio_id, trade_date DESC)`。
2. 新建 `app/engine/paper.py`：
   - `sync_paper_portfolio(session, algo_code)`：以该策略的**默认参数快照**（`SPECS[...].defaults` + `config.BacktestConfig`）调既有回测引擎，`start_date = portfolio.start_date`、`end_date = 最新交易日`，把结果写 `paper_nav`（**每行一个交易日、幂等 upsert**）与 `holdings`/`pending_orders` 快照；同时写 `vs_backtest` = 同一区间最新一次人工回测的 `total_return/excess_return/max_drawdown` 差值，便于直接回答"实盘跟踪比回测差多少"。
   - **尚未成交的下一步**：把 `pending_orders`（次日应买/应卖清单，含原因 = T+1 开盘、涨跌停/停牌标记）单独返回，让用户收盘后就知道明天做什么。
   - 引擎不产出 `pending_orders` 时，允许由"最新信号 + 当前持仓 + 再平衡规则"派生，但派生逻辑必须复用 `simulate_portfolio` 的判定（涨跌停/停牌不可成交、整手向下取整），**不许另写一套成交规则**。
3. 调度：`worker.py` 新增 `CronTrigger(hour=17, minute=30)` 的 `paper` 任务（必须在 `algos` 16:30 与 `signal_perf` 17:10 之后），经 `_with_lock` 走 advisory lock；单组合失败只记录 `status='failed'` 不影响其余。若当日非交易日（`trade_calendar.is_open`）→ 跳过，不写行。
4. 接口（全部走既有错误码约定）：
   - `GET /api/v1/paper/portfolios` → 列表 + 最新净值/累计收益/超额/回撤/跟踪天数
   - `GET /api/v1/paper/portfolio/{algo_code}?days=` → 净值序列 + 当前持仓 + pending_orders + vs_backtest + freshness
   - `POST /api/v1/paper/portfolio/{algo_code}/sync` → 手动触发重算（`HEAVY_TASKS` 风格转后台）
   - 组合由策略**自动创建**：某策略首次完成 paper 同步时插入 `paper_portfolio`（`start_date` = 该策略首个成功 run 的交易日）。提供 `config.PAPER_ENABLED_ALGOS`（默认全部现存策略），env 可覆盖。
5. 净值口径与回测一致（后复权估值、佣金/印花税/滑点、整手），差异只在起始日 = 建仓日；在接口里返回 `caliber` 字段说明这一点。

**前端改动**

6. 新建 `pages/PaperPage.tsx`（路由 `/paper`，加入 `NAV_ITEMS`）：
   - 卡片网格：每策略一组合，大数字 = 累计净值，下方 超额 / 最大回撤 / 跟踪交易日数 / **与回测的差值徽章**（正=跑赢回测口径，负=跑输，用色阶）。
   - 点入：净值 vs 基准双线（recharts）+ 回撤副图 + 持仓表（代码/名称/权重/成本/浮盈）+ **"明日待执行"清单**（放在最显眼位置，这是纸面组合的日用价值）。
   - 数据未就绪时显示 freshness，而非空图。
7. `/algo-center` 的策略卡片上加"纸面跟踪"入口链接，与天梯 T5 相邻，形成"该用哪个策略 → 它跟踪得怎样"的动线。

**验收**

- 同一策略、同一区间：paper 最新净值与手工发起的回测 `total_return` 差异 <0.1pp（差异只应来自区间末点），否则说明有第二套成交规则 —— 必须查明。
- 连续两个交易日跑完后，`paper_nav` 行数 = 期间交易日数，无缺日、无重复。
- 非交易日执行任务 → 不写新行、日志显示 skip 原因。
- 停牌/一字板场景：对应 `pending_orders` 标记 `fillable=false` 并显示原因。

---

### 阶段三 · 视觉系统

#### T12 统一设计系统与涨跌语义

**目标**：深色终端底色、等宽数字、细网格/辉光、A 股红涨绿跌语义全站一致。**这是纯 token 层任务，不改任何业务逻辑。**

**改动**

1. `frontend/src/index.css` 的 `@theme` 增补语义 token（**保留全部现有 token 与 `@custom-variant dark`**）：
   ```css
   --color-up / --color-up-strong      /* A 股涨：红 */
   --color-down / --color-down-strong  /* A 股跌：绿 */
   --color-flat
   --color-alert                       /* 提醒红点：与"涨"用不同红，避免混淆 */
   --color-grid, --color-glow          /* 细网格线与辉光 */
   --font-mono / --font-sans           /* 数字必须等宽 */
   ```
   `.dark` 块同步给出深色取值；浅色模式同样必须是**红涨绿跌**（只调明度/饱和，不许反转语义）。
2. **涨跌色单一事实源**：`frontend/src/lib/format.ts` 的 `priceColorClass()` 改为基于新 token 的唯一入口，并新增 `pctColor(value)` / `colorVar(value)`（返回 `var(--color-up)` 供 SVG/canvas 用，图表必须走这个而不是 class）。全站 grep `text-red-|text-green-|bg-red-|bg-green-|#[0-9a-f]{6}.*涨` 逐个替换为 token；`SectorHeatmap` 的 `heatColor()` 阶梯换成插值函数（T4 已要求，两处共用同一个 `lib/color.ts`）。
3. 数字排版：新增工具类 `.num`（`font-variant-numeric: tabular-nums; font-family: var(--font-mono)`），所有价格/百分比/计数/表格数值列改用 `.num`，右对齐；字号阶梯收敛到 5 档（如 11/12/14/18/28）。
4. 卡片与网格：统一 `.card`（背景 `--color-card`、1px `--color-border`、`--radius-md`、可选极细网格底纹与 hover 时 1px 辉光）。**辉光只做 `box-shadow` 且强度 ≤ 一层**，禁止 blur 动画与大面积 `backdrop-filter`（性能与可读性代价大）。
5. 深色设为**默认主题**（金融终端惯例），`ThemeProvider` 首次访问写入 `dark`，用户手动切过则尊重其选择；切换后重绘图表（CSS 变量传色值的图表必须能在 class 切换后取到新值 —— 用 `useTheme()` 触发重渲染，别只靠 CSS）。
6. 无障碍：所有语义色配**非颜色冗余**（涨 ↑ / 跌 ↓ 符号、温度条分段文字），色对比度 ≥ 4.5:1（浅底深字/深底浅字各验一次）；交互控件键盘可达、focus 可见（`--color-ring`）。

**验收**

- `frontend/src` 内除 `index.css` 外，不再有硬编码涨跌红绿 hex/tailwind 颜色 class（`grep` 结果为 0，或逐条说明保留理由）。
- 明/暗两套：任意数据页涨跌颜色方向都正确；切换主题无残留旧色（尤其 canvas 图表）。
- 表格数字全部等宽对齐，无跳动。
- 首屏无新增 >50ms 的布局抖动（CLs 目测 + Performance 面板抽查）。

---

#### T13 骨架屏替换 spinner

**目标**：加载态从"转圈"改成"形状占位"，消除内容到达时的跳变。复用已有 `.skeleton` + `@keyframes shimmer`。

**改动**

1. `components/StateViews.tsx`（现有 loading/error/empty 三态的集中处）新增骨架原语：
   `SkeletonLine / SkeletonCard / SkeletonTable(rows, cols) / SkeletonChart(h) / SkeletonKpiRow`，全部只是带 `.skeleton` 的尺寸占位。
2. 把 §1.1 列出的 8 个文件里全部 9 处 `animate-spin` 换成**对应形状**的骨架（不是简单塞一条线）：
   - `DashboardPage` → KpiRow 卡骨架 + 图表高度占位
   - `DetailPage` / `HomePage` → 文本块骨架（行数按内容规模估）
   - `AlgoParamPanel` / `BacktestPanel` / `RunMetaPanel` / `JobsPage` → 表格/表单行骨架
3. shimmer 动画 `@media (prefers-reduced-motion: reduce)` 下停用（改为静态占位）。骨架只在 >150ms 的请求上出现（避免快请求闪一下），实现方式：局部 `useDelayedFlag(ms)` hook（自研，不加依赖）。
4. 错误态与空态同时统一：错误显示 `ApiError` 的 `message` + `detail.next_action`（若有）+ 重试按钮；空态说明"为什么是空"（数据未就绪 vs 真没结果）。
5. **`request` 的重试逻辑不许动**（`client.ts` 的 GET+5xx 一次重试是既有契约）。

**验收**

- `grep -r "animate-spin" frontend/src` 结果为 0。
- 慢网络（DevTools 4x throttling）下 `/dashboard` 加载：布局在骨架阶段即已定型，数据到达时**只有文字变化、无位移**。
- 关网络 → 全部页面显示错误态带重试按钮，无 spinner 卡死。

---

#### T14 表格内嵌 30 日 sparkline

**目标**：在数字旁边放一条 30 日走势微型线，纯 SVG、零依赖、极轻。

**改动**

1. 新建 `components/Sparkline.tsx`：
   ```ts
   props: { values: (number|null)[]; days?: number; width?; height?; color?; baseline?: number;
            fill?: boolean; label?: string }
   ```
   - 纯 `<svg>` + `<polyline>` / `<path>`，**不用 canvas、不引任何库**；`viewBox` 自适应容器宽。
   - 稀疏数组直接跳过（长度 0/1 → 渲染平线并置灰 + title 说明数据不足）；**null 断点**按"跳过该点"处理，并在 `aria-label` 说明"N 个点缺数据"。
   - 颜色默认由末值 vs 首值方向决定（走 `colorVar()`，红涨绿跌）；可显式覆盖。
   - hover 显示末值与区间变化（复用 tooltip 逻辑时保持轻量，别引浮层库）。
   - 每点成本受控：默认只画 30 点，>200 点时降采样。
2. 接入位置（每处都是"多传一个数组"，不新增查询）：
   - T5 天梯表：近 20 期胜率 sparkline
   - T1 指数条：30 日收盘价走势
   - T3 温度计：60 日 `temp_score`（把 T3 内联实现替换为本组件）
   - T6 共识榜：个股 30 日收盘走势
   - `ScoreRanking.tsx`：综合分 30 日走势（需要数据源时，用既有 `/market/dashboard`/`/stocks/search` 返回里的字段；**若必须新增后端字段才能拿到，就地打一个 TODO 并在汇报中说明，不要为 sparkline 单独发明重接口**）
3. 数据来自 react-query 缓存优先：同一 `code` 的 30 日线不该发 N 个请求。若需批量，用既有 `/market/indices?days>`/`/stocks/{code}/kline` 拼；确需新增批量端点时，先改 `V5-API-CONTRACT.md`。

**验收**

- 单页 100 个 sparkline 时 `Performance` 面板记录首帧与交互耗时（给出实测数字，要求无明显掉帧）。
- 含 null/单点/全等值三种退化输入有渲染快照测试或手工验证记录。
- 缩放窗口宽度变化时线不拉伸变形（`viewBox` 生效）。

---

#### T15 Ctrl+K 命令面板

**目标**：一个弹窗 + 现有搜索接口，换来"这站很专业"的评价。性价比极高，**范围必须守住**。

**改动**

1. 新建 `components/CommandPalette.tsx`（自研，**不引 cmdk / fuse.js / downshift**）：
   - `Ctrl/Cmd + K` 全局唤起、`Esc` 关闭、`↑↓` 选择、`Enter` 进入、`Tab` 切换分组；焦点陷阱（打开时锁在面板内）、`aria-modal`、打开时禁止背景滚动。
   - 三个数据源：
     a. **股票** —— 复用既有 `GET /api/v1/stocks/search`（`src/api/stock.ts`），输入 ≥1 字符后 **debounce 250ms** 再请求，最多 8 条，请求带 `AbortController`（取消上一次，避免乱序覆盖）。
     b. **页面** —— 静态列表：`NAV_ITEMS` + `/paper` + `/ladder` 等本页新增路由。
     c. **策略** —— 复用既有 `GET /api/v1/algo/list`（首次唤起时懒加载并缓存进 react-query，不重复请求）。
   - 结果分组显示 + 图标（lucide-react，已有）；查询词命中部分高亮（**只做子串高亮，不做模糊算法**）。
   - 面板顶部展示最近 3 次跳转记录（存 localStorage，key `gfna-palette-recent`，上限 10）。
2. **高亮必须转义**：查询词由用户输入，渲染高亮时先 HTML 转义或用 React 节点切分，禁止 `dangerouslySetInnerHTML` 拼接（XSS 面）。
3. 挂载位置：`components/Layout.tsx`，全局一次；`Navbar.tsx` 搜索框右侧显示 `⌘K` 键位提示并可点击唤起。**保留 Navbar 现有搜索行为不变**（不删、不改），面板只是新增入口。
4. 输入法组合期（`compositionstart/end`）不触发搜索，避免中文输入逐字打请求。

**验收**

- 中文输入"贵州"两次字符内不发多余请求（网络面板可证）；快速连打时旧请求被 abort、结果不串。
- 键盘全流程可完成"打开 → 搜索 → 选策略 → 跳转"；焦点正确归还到唤起元素。
- `<img onerror>` / `<script>` 形态的查询词被当作纯文本渲染（做一次实际注入尝试并记录结果）。
- 面板打开时背景不滚动、Tab 不逃逸。

---

## 5. 验收与提交纪律

**每个任务的完成定义（DoD），六项缺一不算完成：**

1. `cd backend && ../.venv/Scripts/python.exe -m pytest tests -q` 全绿；仓库根纯逻辑套件同样全绿。新增逻辑（`market_temperature`、`index_perf`、`treemap`、`signal_perf`、`consensus`、`condition` 扩展、`backtest_view`、`paper`）**必须补单测**，含退化输入（空、单点、NULL、数据不足）。
2. `cd frontend && npm run build` 通过（`tsc -b` 无错，禁止 `as any` 绕过类型）。
3. 涉及新表/新列：alembic revision 已生成、`upgrade head` 与 `downgrade -1` 均可执行（分区表禁止 `create_all` 改结构）。
4. **数据正确性抽查**：每个新数字都要有一个手工 SQL 或独立计算对照，误差写在汇报里。不接受"看起来合理"。
5. **浏览器实测**（不是只跑通接口）：`npm run dev` + 真实后端 + PostgreSQL，走一遍 golden path 与至少 2 个数据缺失/异常场景，监控 Console 无 error、Network 无 5xx、无重复请求风暴。同时抽查相邻页面未被拖坏（尤其 T12 换 token、T13 换骨架之后的 `ScoreRanking` / `BacktestPanel` / `JobsPage`）。
6. 文档同步：`docs/V5-API-CONTRACT.md`（新端点/新字段/口径说明）、`docs/CHANGELOG-V5.md`（本任务条目）、涉及数据源的更新 `docs/architecture.md` 相关段落。

**汇报格式（每任务结束时，≤20 行）**

```
T<n> <任务名> — 完成 / 阻塞
改动：<文件清单，标注 迁移/新表/新依赖>
数据验证：<抽了什么数、误差、结论>
性能：<接口 P95 或本次批量耗时；前端体积/耗时增量>
口径与陷阱处理：<复权/单位/NULL/SCD2/排除回测 run/前视扫描 —— 逐条说明>
遗留：<没做的、需要人决策的>
下一步：<等待确认，不自行继续>
```

**阻塞即停**：任何"事实基线（§1）与实际代码不符"、"依赖装不上"、"T7 等价性脚本有差异"、"数据口径需要人拍板" → **立刻停下来汇报，不要自行选择一种口径继续做**。

---

## 6. 禁止事项

- 不新增除 `lightweight-charts` 之外的任何依赖；不直接 import recharts 的传递依赖（d3-shape/d3-scale 只是内部件，不是公共依赖）。
- 不引 CDN、不从外网取字体/脚本/瓦片。
- 不 resurrect A1/A2 策略；不改 `@custom-variant dark`；不移除 `WindowsSelectorEventLoopPolicy`。
- 不在 `app/strategy/*.py` 里写 `.shift(-`；不把前瞻收益统计写成策略特征。
- 不统计/展示任何未过滤 `is_backtest IS FALSE` 的信号聚合。
- 不把 NULL 当 0 参与求和、排名、均值、颜色映射。
- 不用今天的板块名单去算历史某日（SCD2 时间条件不许省）。
- 不做与任务无关的重命名、格式化、目录调整、"顺手清理"。
- 不为演示效果写假数据、mock 兜底、随机数占位。数据缺失就显示缺失。
- 不引入 WebSocket 做提醒（轮询已足够）；不引入服务端用户体系/登录。
- 不把纸面组合改成"增量维护持仓"（T11 明确：整体重算）。
- 不动 `client.ts` 的重试与超时语义。
- 不在 PowerShell 里写长脚本；跨小时批量任务禁止前台 sleep 轮询（后台运行 + 完成通知 + 单次状态检查）。

---

## 7. 已定的默认假设（如需改动，请先告知 AI）

| 议题 | 默认决定 | 理由 |
|---|---|---|
| 自选股存储 | `localStorage`，不建服务端表 | 项目无认证；引入账号的成本远大于该功能价值 |
| 提醒通道 | 60s 轮询 + 页面可见性门控 | 不引 WebSocket，服务端零推送负担 |
| treemap 布局 | 手写 squarify + 单测 | 不新增依赖，且需要可控的退化输入处理 |
| K 线库 | `lightweight-charts` | 缩放/十字光标/成交量/标记开箱可用，体积可控 |
| 天梯胜率口径 | 信号级 5/10/20 日胜率（≠ 回测 FIFO 回合胜率） | 与"策略天梯"的问题域匹配，UI 必须标注口径 |
| 参数敏感性 | 每格复用 `backtest_run`，仅加 `grid_id` | 避免第二套回测路径；同格可幂等复用 |
| 纸面组合 | 每日整体重算 | 拒绝增量状态漂移，代价是每日重算一次（可接受） |
| 温度权重 | 常量表 + 固定断点，跨日可比 | 逐日 min-max 会让"温度"失去比较意义 |
| 主题默认值 | 深色为默认 | 金融终端惯例，用户可切且记住选择 |
| 新指数历史 | 中证500/1000 必须回填至 `HISTORY_START` | 不回填则 T1 的新列长期为 null，等于没做 |
