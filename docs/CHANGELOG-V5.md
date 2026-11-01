# GFNA V5 变更清单（CHANGELOG）

> 范围：`backend/**`、`frontend/**`、`sql/`、根目录脚本与文档
> 原则：**能被 V5 取代的直接删；能修的不重写；必须重写的按方案分层**
> 日期：2026-10-21（V5.0）｜ 2026-10-22（V5.1 增补）

---

## 0. V5.1 增补（2026-10-22）

本轮解决三件事：**容器首次启动空数据**、**历史估值缺失**、**板块/资金流无兜底**。

### 0.1 容器与 Playwright（解决"启动后没数据"）

| 变更 | 文件 |
|---|---|
| 安装 Chromium 及系统依赖（`playwright install --with-deps chromium`，`PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`） | `backend/Dockerfile` |
| 入口脚本：`bootstrap_db` → 空库灌数据 → uvicorn | `backend/docker/entrypoint.sh`（新增） |
| 首次初始化脚本（空库才执行、幂等、不覆盖已有数据） | `backend/scripts/first_run.py`（新增） |
| compose 增 `SEED_DEMO_ON_EMPTY`（默认 1）、PG 端口改 5433 避免与本机 5432 冲突 | `docker-compose.yml` |
| 本地一键启停（独立进程，脱离会话生命周期） | `start-local.ps1`（新增） |

> **诊断澄清**：Playwright 只影响新闻爬虫，**不影响行情加载**。
> "启动后页面空白"的真因是"只建表、无数据"，已由入口脚本修复。

### 0.2 历史估值源（打通 A1/A4/A3 真实选股）

方案里没有这一层，`stock_a_indicator_lg` 在 akshare 1.19.1 已不存在，百度/东财估值均不可达。
最终方案：**财报与分红缓存进库 + 本地推算**，日常计算零网络。

| 新增 | 说明 |
|---|---|
| 表 `dim_stock_financial` | 财报关键指标（EPS/BPS/ROE/分红率/EPS_TTM），`announced_date = 报告期+90天` 作为市场可知日 |
| 表 `dim_stock_dividend` | 现金分红明细（`dps`=派息比例/10，按**除权日** point-in-time） |
| `app/ingest/valuation.py` | 纯函数（`normalize_financials` / `eps_ttm_series` / `build_valuation_frame` / `dv_ttm_series`）+ 三阶段 async：`sync_financials` → `sync_dividends` → `compute_valuation`（零网络） |
| pipeline/CLI/API | 新增任务 `valuation`（`--start` 走区间回补），`/admin/ingest/valuation` |
| 口径 | `PE=close/EPS_TTM`（允许负值）、`PB=close/BPS`、`股息率=近12月 DPS/close`；**BPS 用「调整前」**（实测 688981「调整后」为脏值 594.86） |

**实测校验**（2026-10-21 真实数据）：中国平安 PE 5.62 / PB 0.64 / 股息率 7.19%；
中国建筑 PE 5.93 / PB 0.22 / 6.03%；招商银行 PE 7.30 / 4.77%；中国石化 PE 18.25 / PB 0.65 / 4.00%。
→ A4 从"1 条演示"变为 **4 条真实信号**；A3 的 6 条中 **5 条来自真实数据**。

### 0.3 板块与资金流兜底（同花顺）

| 变更 | 文件 |
|---|---|
| 同花顺板块资金流兜底源（行业+概念，`fetch_net_inflow_map_sync` 同步版 + 异步采集 + 跳过东财已有行） | `backend/app/ingest/ths_sector.py`（新增，1022 行） |
| 板块采集自动降级：东财为空时自动调同花顺 | `app/pipeline.py` |
| 板块情绪资金流兜底：东财失败 → 同花顺**合并表**（实测两个接口会交替可用，合并后两类板块都能覆盖） | `app/engine/sector_analysis.py` |

**实测**：`collect_sector_fallback` 写入 **477 个板块**（90 行业 + 387 概念），
`fetch_net_inflow_map_sync` 返回 477 个中文名→净额(元)；
板块评分 477 行全部落库，资金流子项 **477/477 可得**（150 个不同分档），来源如实标记 `ths`。

### 0.4 本轮顺带修掉的问题

| # | 问题 | 修法 |
|---|---|---|
| 1 | 板块资金流来源被误标为 `akshare`（实际来自同花顺） | 返回值改为 `(mapping, source)`，如实标记 |
| 2 | 任务统计 `rows=None`（返回 FetchResult 时丢失行数） | 统一走 `_summary()` |
| 3 | 巨潮分红对个别标的报 `KeyError: 实施方案公告日期`（接口内部解析失败） | 已容错跳过并记 warning（不阻断其它标的） |
| 4 | `quality.DATASET_LABELS` 缺 `valuation`（前端徽标显示英文） | 补中文标签 |
| 5 | `q.10jqka.com.cn` 域名不可解析 → 行业代码表失败 | 自动退化为 `THS_<行业名>` 作为 `sector_code`，功能不受影响 |

### 0.5 验证（V5.1）

- `pytest tests`（backend）：**166 passed**；根目录新闻 API：**9 passed, 3 skipped**
- 前端生产构建：`tsc -b && vite build` 通过
- 真实网络实跑：财务缓存 451 行 / 分红缓存 388 行 / 估值更新 2750 行（11 只 × 250 日）
- 同花顺板块：477 行板块日线 + 477 行板块评分
- Playwright：本地 `playwright install chromium` 成功（chromium-1105 + ffmpeg），证明镜像内同命令可行

---

## 1. 缺陷修复对照（对应方案 §3 的 20 条）

| # | 方案里的缺陷 | 本版处置 | 位置 |
|---|---|---|---|
| 1 | 关键依赖缺失（asyncpg/pandas/numpy/akshare） | 已补齐并锁定版本；`requirements.txt` 重写 | `backend/requirements.txt` |
| 2 | `yfinance==1.5.2` 不存在导致 pip 失败 | **删除 yfinance**（美股非主链，且版本是整条 pip 崩的根因） | `requirements.txt` |
| 3 | 无个股级行情表 | 新增 `dim_stock` / `fact_stock_daily`（按年分区）/ `fact_feature_daily` / `fact_stock_score_daily` / `fact_moneyflow` | `app/db/models/{dim,fact}.py` |
| 4 | 选股是 117 行 mock CLI | **整体退役 `westock-tool/`**，改为本地横截面计算 | 删除 `westock-tool/`；`app/strategy/*` |
| 5 | 无历史 → 技术面恒 5.0 | 真实历史回补 + 指标预计算；数据不足时返回 5.0 **且带 `reason=insufficient_history`** | `engine/sector_analysis.py` |
| 6 | 历史取数方向反（`asc().limit(30)`） | 全部改为 `ORDER BY trade_date DESC LIMIT n` + reverse | `engine/*`、`strategy/*` |
| 7 | 评分未回传前端（`_enrich_with_scores` 空函数） | 仪表盘直接 join `sector_score`，返回 `technical/sentiment/news/composite` | `api/v1/market.py` |
| 8 | 数据只进不出（每天只插当天一行） | 采集幂等 upsert + 历史回补 + 序列查询 | `ingest/service.py` |
| 9 | 历史回补不覆盖板块 | `collect_sector_daily` 支持任意区间 + `dim_sector_member` SCD2 | `ingest/service.py` |
| 10 | 龙头识别 223 行零引用 | 接入评分链路，写入 `strength_leaders/sentiment_leaders`，前端展示 | `engine/leaders.py`、`engine/sector_analysis.py` |
| 11 | 论坛情绪恒中性 | **删除论坛热度子项**（无稳定数据源，属虚假精度）；情绪四子项改为 融资/北向/资金流/**新闻**，缺项按权重再分配 | `engine/sentiment.py` |
| 12 | 资金流匹配用 BK 代码比中文名 | 改为中文名精确匹配 + 成分股资金流聚合两条路径 | `engine/sector_analysis.py` |
| 13 | 算法参数不生效 | 参数进入计算、缓存键（含参数指纹+数据版本）、run 快照 | `strategy/strategies.py`、`strategy/runner.py` |
| 14 | AI 日报塞进第一个板块 `ai_summary` | 独立表 `daily_report`（含结构化 结论/依据/风险） | `db/models/fact.py`、`services/report_generator.py` |
| 15 | 日报读取 `limit(1)` 可能取到板块短评 | 按 `trade_date` 主键精确读取 | `api/v1/market.py` |
| 16 | 美股指数写死空数组 | 保留采集（可选、非阻断），`us_indices` 真正返回（取最近 3 日） | `api/v1/market.py` |
| 17 | 交易日判断只看周末 | 真实交易日历 `trade_calendar` + `is_trade_day`，节假日不跑、不写脏 | `ingest/calendar.py`、`app/worker.py` |
| 18 | `amount` 用总市值兜底 | 采集入口统一：`volume=股`（手×100）、`amount=元`，二者不互相兜底 | `ingest/akshare_provider.py` |
| 19 | import 路径不一致（`from backend.scripts...`） | 统一为包内绝对导入；`sys.path` 只在入口处注入 | 全仓库 |
| 20 | `algo_meta` 空时裸 import 兜底 → 500 | 策略元信息内置在 `strategy/strategies.SPECS`，`algo_meta` 只是可选持久化 | `api/v1/algo.py` |

工程债（§3.3）：
- **新增 Alembic**（`alembic/`，0001 为 V5 基线）→ 终止裸 `create_all` 的"改模型就删库"；
- **不再 shell 拼接执行 CLI**（CLI 已删除），无注入面；
- **测试从 0 到 97 项**（纯逻辑 + API 集成），含"回测未来函数检测"；
- **`seed-demo` 移出生产**：仅 `ENV=demo` 可用，且改为确定性生成（不再用 `hash()` 造价格）；
- **静态目录 404 源头删除**（不再挂 `frontend/css`、`frontend/js`）。

---

## 2. 新增模块

### 2.1 采集层 `app/ingest/`
| 文件 | 职责 |
|---|---|
| `base.py` | `FetchResult`、`RateLimiter`（令牌限频）、`retry`（指数退避+抖动）、`Provider` 协议、`ProviderChain`（**按能力降级**）、`dedupe_rows`/`chunked` |
| `akshare_provider.py` | AKShare 实现：股票池/快照/日线（raw+hfq 算 `adj_factor`）/指数/板块/资金流/两融/日历 |
| `baostock_provider.py` | Baostock 兜底：日线（`tradestatus`→停牌、`preclose`/`pctChg`→涨跌停、`adjustflag`→复权）、行业、指数 |
| `calendar.py` | 交易日历同步与查询（`is_trade_day`/`latest_trade_date`/`prev`/`next`） |
| `quality.py` | 行数阈值、缺失率、涨跌幅越界、价格跳变、重复行、OHLC 逻辑；写 `meta_data_quality` |
| `service.py` | 编排：`sync_stock_universe` / `collect_daily_snapshot` / `backfill_stock_history`（断点续传）/ `collect_sector_daily` / `collect_sector_members`（SCD2）/ `collect_moneyflow` / `collect_indices` / `collect_margin` / `run_daily_ingest`；PG advisory lock |

### 2.2 分析层 `app/engine/`
`indicators.py`（MA/RSI-Wilder/MACD/BOLL/ATR/动量/波动/量比/金叉/多头）、
`factors.py`（六因子分位）、`cross_section.py`（行业中性 z-score/winsorize/分位）、
`stock_score.py`（六因子 0–10 + 明细）、`features.py`（批量算指标落库）、
`technical_score.py`（板块技术分 40/30/30）、`sentiment.py`（四子项 + 缺项再分配）、
`leaders.py`（实力/情绪龙头）、`sector_analysis.py`（全量板块评分落库）、
`report_input.py`（日报结构化输入）。

### 2.3 选股层 `app/strategy/`
`condition.py`（条件树 AND/OR/between/in/top_pct/行业约束）、`strategies.py`（A1–A4 + 参数校验）、
`runner.py`（run_id/参数指纹/数据版本/缓存/`list_runs`）、`backtest.py`（T+1/费用/涨跌停/后复权/未来函数检测）、
`metrics.py`（13 项绩效）。

### 2.4 服务与接口
`api/v1/market.py`（仪表盘/板块）、`stocks.py`（个股/ K 线/基准对比）、`algo.py`（选股 + CSV）、
`backtest.py`、`meta.py`（新鲜度/日历/任务/分区）、`admin.py`（手动触发/演示数据）、
`app/pipeline.py`（任务编排）、`app/worker.py`（定时任务 + DB 锁）、
`services/freshness.py`、`services/report_generator.py`（重写）。

### 2.5 数据库
新增 13 张表 + 2 张视图，`fact_stock_daily` 按年 RANGE 分区（2021–2027 + default）。
详见 `docs/architecture.md`。

### 2.6 前端
`api/`（client + 6 个域模块）、`types/`（6 个类型文件，字段与契约一致）、
`hooks/`（8 个，含 `useMutation` 语义的选股/回测）、
`components/`（`StateViews`/`DataTable`/`FreshnessBadge`/`RunMetaPanel`/`BacktestPanel`/安全 `Markdown`）、
`pages/`（`DashboardPage` 重写、`AlgoCenter` 改造、**新增** `StockDetailPage`/`JobsPage`/`NotFoundPage`）。

---

## 3. 删除清单（**不可回退**，仓库已无这些文件）

### 后端
| 删除 | 原因 |
|---|---|
| `app/db/models.py` / `models_v3.py` / `models_v4.py` | 被 `app/db/models/` 包取代（四表+两表结构已不适用） |
| `app/api/v1/analysis.py` | 被 `api/v1/market.py` 取代（同时修掉空评分函数） |
| `app/services/scoring.py` | 被 `engine/sector_analysis.py` 取代（含排序反向 bug） |
| `app/services/sentiment.py` | 被 `engine/sentiment.py` 取代（含资金流匹配 bug、论坛假数据） |
| `app/services/leader_identifier.py` | 被 `engine/leaders.py` 取代（原文件零引用） |
| `app/services/technical_analysis.py` | 被 `engine/indicators.py` + `engine/technical_score.py` 取代 |
| `app/services/market_data.py` | 被 `ingest/` 取代（原 `amount` 语义错位、无个股接口） |
| `app/services/algo_service.py` | mock CLI 调用层，整体退役 |
| `scripts/init_v3_db.py` / `init_v4_db.py` / `fetch_daily_data.py` / `run_analysis.py` | 被 `scripts/bootstrap_db.py` + `scripts/pipeline_cli.py` 取代 |
| `run_stdlib.py` / `test_server.py` / `test_waitress.py` / `test_socket.py` | Windows 事件循环排障期的一次性脚本（历史遗留，已无引用） |
| `crawl_now.py` | 与 `worker`/admin 重复 |

### 根目录
| 删除 | 原因 |
|---|---|
| `westock-tool/`（整个目录） | **mock CLI**（117 行 `SAMPLE_DATA`），方案明确退役 |
| `package.json` / `package-lock.json` / `node_modules/`（根） | 一次性课程论文脚本的依赖（`jszip`/`docx`），与项目无关 |
| `generate-paper.js` | 论文生成脚本，写死 `OneDrive` 绝对路径，全仓零引用 |
| `sql/init.sql` | 仅建新闻三表且与 ORM 漂移；改为 Alembic + `bootstrap_db.py` 为唯一事实源 |

### 前端
| 删除 | 原因 |
|---|---|
| `src/pages/DashboardV3.tsx` | 合并进新的 `DashboardPage`（`/dashboard-v3` 路由已删） |
| `src/pages/DashboardPage.tsx`（旧 V2 新闻统计） | 被 V5 盘后分析页覆盖；新闻统计已并入首页 |
| `src/lib/api.ts` / `algo-types.ts` | 被 `api/*` 与 `types/*` 取代（字段口径已变） |
| `public/sw.js` + `index.html` 的 SW 注册 | cache-first 且不更新，发版后永远拿旧 bundle |
| `axios` 依赖 / `lint` 脚本 / `__probe*.ts` | 0 引用；`lint` 引用未安装的 eslint |
| 无效 CSS 类（`prose`/`scrollbar-thin` 原引用） | 未安装对应插件；已改为自定义实现 |

---

## 4. 破坏性变更

1. **旧接口整体移除**：`/api/v1/analysis/*`（dashboard / run-analysis / sector / seed-demo）
   → `/api/v1/market/*`、`/api/v1/admin/*`。新闻接口完全兼容。
2. **字段口径统一**：`close_price→close`、`change_pct→pct_chg`、`technical_score` 等评分统一 0–10；
   `amount` 一律**元**、`volume` 一律**股**。
3. **表结构重建**：旧 `market_indices`/`sector_daily`/`sector_score`/`algo_results`/`algo_meta`
   被替换或重塑；`sector_daily` 更名为 `fact_sector_daily`（语义等同，命名统一到 `fact_*`）。
   **迁移方式**：全新库用 `bootstrap_db.py`；已有库先备份，再 `--recreate` 或
   `alembic stamp base && alembic upgrade head`（基线会重建对象）。
4. **`seed-demo` 语义变更**：需要 `ENV=demo`，且生成的是 1 年确定性序列（非单日假数据）。
5. **A3 口径变更**：`composite_score` 改为本地六因子模型（0–10），不再是腾讯四维评分（0–100）。

---

## 5. 已知限制（如实记录，不粉饰）

| 限制 | 影响 | 现状/后续 |
|---|---|---|
| 东财（push2）接口在部分网络被重置 | 快照/历史/板块/资金流的 akshare 路径不可用 | 降级链自动转 baostock（日线/指数可用）；板块与资金流在该网络下为空 |
| 北向资金 2024-08 起暂停披露 | 情绪子项之一长期缺失 | 已按"缺项权重再分配"处理，不拉低总分 |
| 央企/所有制为规则推断 | A3 可能漏选 | `core/soe.py` 白名单+名称规则，未知不误判；权威来源应用实控人数据 |
| 板块成分为快照式 SCD2 | 历史早期区间可能不准 | 每次采集开新区间，回测按"当日在市"过滤 |
| 回测不含分红再投资/融资融券/冲击成本 | 与实盘有差距 | 已计佣金/印花税/滑点；metrics 标 `adjusted` |
| 新闻链路依赖外网与 DeepSeek | 情绪/日报在无网时降级 | AI 日报降级为数据摘要；新闻缺失不阻断选股 |

---

## 6. 验证记录（本次实跑）

| 项 | 结果 |
|---|---|
| `pytest tests -q`（纯逻辑 + API 集成） | **103 passed** |
| 前端生产构建 | `tsc -b && vite build` 通过（2778 模块，dist gzip 254 KB） |
| 演示数据全链路 | demo → features 42 行 → scores 42 行 → sector_scores 10 行 → A1=2 / A2=10 / A3=6 / A4=1 |
| **真实数据回补** | 22 只 × 243 个交易日 = **5346 行真实日线**（akshare 失败→baostock 成功，含 `adj_factor`） |
| **真实回测** | A1 +1.03%（超额 +11.23%）/ A2 -9.69% / A3 -12.00% / A4 -0.39%；均 `calendar_source=db`、`adjusted=true`、243 个净值点；证据见 `docs/evidence/` |
| AI 日报 | 真实 DeepSeek 调用成功（1327 字）；无 Key/无网时降级模板 |
| 事务一致性 | API 触发的 `stg_run`/`backtest_run` 已能持久化，缓存命中同一 `run_id` |
| 数据库 | 21 张表 + 8 个分区（2021–2027 + default）+ 2 视图 |
| 区间裁剪 | 回测起点自动裁剪到数据可用日（`start_trimmed=true`），避免"个别调仓日无数据导致整体失败" |

### 6.1 实现过程中发现并修复的**方案未提及**的真实缺陷

1. **`get_db` 不提交事务** → `/algo/*` 产生的 `stg_run` 随请求回滚，缓存与复现失效（已改为成功即 commit）；
2. **`ProviderError` 未导入** → 只在真实联网调用时暴露（`NameError`），已修；
3. **15:05 盘后任务与 `SNAPSHOT_READY=15:30` 冲突** → 采集层会拒绝盘中数据，任务空跑；改为 15:36；
4. **测试客户端与 httpx 版本不兼容**（starlette 0.36 + httpx 0.28）→ 锁定 httpx 0.27.2；
5. **回测遇到"无特征/无评分"的早期调仓日会整体 409** → 改为自动裁剪起点并标注；
6. **`DEFAULT_DAILY_STEPS` 漏了 `sector_members`** → 板块成分 SCD2 不随每日流水线更新，已补；
7. **东财接口在部分网络被重置** → 降级到新浪（快照）与 baostock（历史），已实测可用。

### 6.2 未完成 / 明确不做

- **历史估值字段**（PE/PB/股息率）未采集 → 价值类策略无法在纯真实历史数据上运行；
  需要新增 `stock_a_indicator_lg` 之类的估值源（下一步首要事项）；
- **北向资金**：2024-08 起无数据，保留字段但恒为空；
- **板块/成分**：仅东财源，东财不可达时为空（不影响个股选股）；
- **概念板块全量成分**（400+）每日采集约 5–10 分钟，未纳入默认每日任务。

---

## 7. V6 产品升级记录（2026-10-29 起）

> 任务书：`docs/GFNA_V6_产品升级任务书.md`。按「一次只做一个任务、汇报后等确认」推进。

### 7.1 T1 指数多周期对比条（2026-10-29，commit `0524105`）

| 变更 | 文件 |
|---|---|
| `/api/v1/market/indices` 增返回多周期收益（5/20/60/120/250 日）与相对沪深300 字段；`days>1` 序列模式保持原字段 | `backend/app/api/v1/market.py` |
| 多周期收益纯函数（基于真实收盘序列，窗口不足返回 null，不用 0 兜底） | `backend/app/engine/index_perf.py`、`backend/app/core/constants.py` |
| 中证500/1000 历史回补（可断点续传） | `backend/scripts/backfill_indices.py` |
| 前端指数对比条（手写 sparkline，不引新图表库）与仪表盘接入 | `frontend/src/components/IndexCard.tsx`、`frontend/src/pages/DashboardPage.tsx`、`frontend/src/types/market.ts` |
| 契约与测试 | `docs/V5-API-CONTRACT.md`、`backend/tests/test_index_perf.py`、`backend/tests/test_api_v5.py` |

### 7.2 T2 个股 K 线升级为 lightweight-charts 蜡烛图（2026-10-29）

**新依赖（唯一）**：`lightweight-charts@5.2.1`（Apache-2.0，纯 ESM，无 CDN/外网请求）。
`KlineChart` 懒加载为独立 chunk：`194.84 kB / gzip 63.46 kB`（含图表库）；
主包 `864.66 kB / gzip 254.14 kB`（本页移除 recharts 后比 T1 基线 `886.46/257.54` 反而小 2.7 kB gz）。
`recharts` 仍保留在 `package.json`（雷达图/柱图/回测面板仍在用），仅本页移除。

**后端**

| 变更 | 文件 |
|---|---|
| kline 增列 `boll_mid/boll_upper/boll_lower`，响应加 `adjust_basis:"raw"` 与 `count` | `backend/app/api/v1/stocks.py` |
| 新增 `GET /api/v1/backtest/{bt_id}/trades?code=`：直接读 `backtest_run.trades` 过滤返回，**不重算回测**；不存在返回 404 `NOT_FOUND` | `backend/app/api/v1/backtest.py` |
| 契约 4.2 补 BOLL/复权口径、新增 6.3 成交查询 | `docs/V5-API-CONTRACT.md` |
| 集成测试：kline 口径与 BOLL 字段、成交查询（含 code 过滤与 404） | `backend/tests/test_api_v5.py` |

**复权口径（读码确认，非猜测）**：`fact_stock_daily.close` 为未复权原价；
`features.py` 的 `_DAILY_COLUMNS` 不含 `adj_factor`，MA/BOLL 全部算在同一组未复权收盘价上；
回测估值使用后复权，但 `trades[].price` 存的是**未复权成交价**（`_buy_price(bar.open)` / `_sell_price(bar.open)`）。
因此图表与买卖标记天然同口径，不做二次换算；UI 固定显示「未复权」徽标。

**前端**

| 变更 | 文件 |
|---|---|
| 导出 `KlineChart`：蜡烛 + 成交量（下 20%）、MA5/20/60 + BOLL 开关（localStorage，默认 MA 开/BOLL 关）、十字光标 tooltip（OHLC/涨跌幅/成交量/成交额）、60/120/250 根、滚轮缩放/拖拽/双击复位、`fitContent`、ResizeObserver、`chart.remove()`、主题切换重读 CSS 变量 | `frontend/src/components/KlineChart.tsx`（新增） |
| 回测标记：按交易日聚合；买=向上三角、卖=向下三角；`shares=0`=灰色圆点「拒」；点击标记高亮并在下方展示 price/shares/amount/fee/reason（中文原因）与当日 OHLC 口径说明 | 同上 |
| 个股页接入：`?bt=<bt_id>` 自动加载成交并叠加标记、显示回测横幅（算法/成交数/清除标记）、懒加载 + `.skeleton` 骨架、移除本页 recharts | `frontend/src/pages/StockDetailPage.tsx` |
| 类型与 Hook：`KlineItem` 增 BOLL、`KlineResponse.adjust_basis`、`BacktestTradesResponse`、`useBacktestTrades` | `frontend/src/types/stock.ts`、`frontend/src/types/backtest.ts`、`frontend/src/api/backtest.ts`、`frontend/src/hooks/useBacktest.ts` |

**验证（2026-10-29，真实 PG + 演示/真实数据）**

- `pytest tests -q`：**181 passed**；`npm run build`（`tsc -b && vite build`）通过。
- K 线 250 根 OHLC 与接口 JSON **逐根一致**（000593/600000/601658，mismatch=0）；MA5/20/60 与 BOLL 三线 120 根逐点一致（bad=0）。
- 手工对照：用 `fact_stock_daily` 未复权收盘价重算 000593/601658 的 MA/BOLL，
  与 `fact_feature_daily` 最大误差 **3.3e-05**（NUMERIC 4 位小数存储舍入）。
- 成交查询：`e1b201d6…`（B3，262 笔）接口 `items` 与库内 JSON **完全相同**；`code=000593` 返回 4 笔。
- 标记落位：000593 的 4 笔成交（2025-12-24 买 10.26 / 2025-12-31 卖 11.22 / 2026-02-13 买 13.92 / 2026-03-02 卖 15.68）
  与当日未复权开盘偏差 0.07%–0.13%（滑点内）；601658 的 `shares=0` 拒单正确显示灰点「拒」，点击显示「可用资金不足」。
- StrictMode：DOM 中仅 1 个 `.tv-lightweight-charts` 实例（v5 内部分层共 7 个 canvas，属单实例正常结构）。
- 区间切换 60/120/250 根、MA/BOLL 开关及刷新持久化、暗/亮主题重绘均实测通过；Console 无 error（仅既有 react-router/react-query warning）。

**已知口径差异（不是 bug）**：蜡烛颜色按「收盘 vs 开盘」着色（标准 K 线定义），
`pct_chg` 按「收盘 vs 前收盘」计算。跳空日两者符号可能不同——600000 近 250 日中 28 天、
000593 中 22 天出现该差异（如 600000 2025-10-24：开 12.99 / 收 12.98 → 阴线，但相对前收 12.97 的 `pct_chg=+0.077%`）。
tooltip 同时给出 `pct_chg`，不会把涨跌幅显示错；若产品要求颜色严格跟随 `pct_chg`，需另行确认（会与 K 线实体方向矛盾）。

### 7.3 T12 统一设计系统与涨跌语义 token（2026-10-29）

**目标**：深色金融终端基底、等宽数字、细网格/微光、全站 A 股「红涨绿跌」语义；纯 token 层改造，无业务逻辑变更。

**Token 层（`frontend/src/index.css`）**

| 新增/调整 | 值（亮 / 暗） |
|---|---|
| `--color-up` / `--color-up-strong` | `#dc2626` / `#991b1b`；`#f87171` / `#fca5a5` |
| `--color-down` / `--color-down-strong` | `#067647` / `#05603a`；`#4ade80` / `#86efac` |
| `--color-flat` | `#64748b` / `#94a3b8` |
| `--color-alert`（与「涨」红区分） | `#be123c` / `#fb7185`（rose） |
| `--color-success` / `--color-warning` | `#0f766e` / `#92400e`；`#2dd4bf` / `#fbbf24` |
| `--color-grid` / `--color-glow` | 细网格线 / 卡片 hover 单层微光 |
| `--color-chart-1..5` | 图表分类色（不参与涨跌语义） |
| `--font-sans` / `--font-mono` | 中文系统字体栈 / 等宽数字栈 |

- 亮色也保持红涨绿跌，仅调整明度/饱和度，不反转语义；`html.dark { color-scheme: dark }`。
- 新增 `@utility num`（等宽 + `tabular-nums`）、`.card`（1px 边框 + `--radius-md` + 单层 hover glow）、`.card-grid`（可选细网格纹理）、全局 `:focus-visible` 2px ring；`.glass` 移除 `backdrop-filter`（避免大范围模糊）。

**单一语义源（`frontend/src/lib/`）**

- `format.ts`：`pctColor()` / `priceColorClass()` / `priceBgClass()` / `trendGlyph()`（↑/↓/—）/ `colorVar()`（SVG/canvas 用 `var(--color-up|down|flat)`）。
- 新增 `color.ts`：`trendHeat()` 用 `color-mix()` 连续插值（±8% 归一化，不再是 5 档阶梯），`CHART_PALETTE` / `chartColor()` 供 Recharts 使用。
- 全站替换硬编码 `text-red-* / text-green-* / bg-red-* / bg-green-*` 与涨跌色 hex；`KlineChart` 的 MA/BOLL 指标线与新闻源品牌色不属于涨跌语义，保留原 hex。

**排版与容器**

- 所有价格/涨跌幅/计数/表格数字列加 `.num`；表格右对齐列由 `DataTable` 的 `align=right` 自动加 `.num`。
- 字号阶梯收敛为 11 / 12 / 14 / 18 / 28px；页面 H1 统一 28px。
- 卡片统一 `.card`；AlgoCard 类型标签改为「色相底 + 前景文字」以保证对比度。

**主题**

- 首次访问默认深色：`index.html` 预渲染脚本在 CSS 前写 `dark` + `localStorage.gfna-theme`，避免首屏闪白；用户手动选择优先并持久化。
- `KlineChart` 通过 `useTheme()` 触发 `colorEpoch`，主题切换后重读 CSS 变量并 `applyOptions` 重绘 canvas；保留 `MutationObserver` 兜底。

**验证（2026-10-29）**

- 验收 grep：`frontend/src` 内（除 `index.css`）涨跌红/绿 Tailwind 类与语义 hex = **0**；`dark:` 硬编码变体 = 0。
- `npm run build` 通过：主包 `861.48 kB / gzip 253.59 kB`，Kline chunk `194.96 kB / gzip 63.49 kB`，CSS `35.61 kB / gzip 7.06 kB`。
- 浏览器（StrictMode + 真实 PG）：清空 localStorage 首访 = `dark` 且写入 `gfna-theme=dark`；切换后 `--color-up/down` 亮 `#dc2626/#067647`、暗 `#f87171/#4ade80`；刷新保持选择。
- Canvas 像素扫描：600519 K 线暗色 token 命中 1974/1851 px；切亮后亮色 token 1974 px、暗色残留 0（约 1.2s 内重绘完成）。
- 热力图插值：-5.93% / -2.17% / +2.13% 背景依次变浅/转红，文本恒为前景色，涨跌幅带 ↑/↓，空值显示 `—`。
- 对比度（前景/背景）：亮色 up 4.83、down 5.69、flat 4.76、alert 6.29、success 5.47、warning 7.09；暗色卡片 up 5.29、down 8.40、flat 5.71、alert 5.44、success 7.86、warning 8.76；均 ≥4.5:1。
- 键盘 Tab 可见焦点 `2px solid rgb(26,115,232)`；Dashboard buffered layout-shift **CLS=0**；Console 仅既有 react-router v7 warning。
- 已知：回测接口在本机 20s 超时（既有后端性能问题，非 T12 引入）；`.card` 为非分层样式，会覆盖部分 Tailwind hover 边框工具类（有意）。

### 7.4 T3 市场温度计（2026-10-29）

**目标**：用 0–100 温度分回答「现在市场能不能做」，只读既有日线/特征表，不引入新数据源。

**后端**

- 新增 `app/engine/breadth.py`：`load_breadth()` 从 `report_input._load_breadth` 原样抽取，日报改为复用（默认不剔除停牌，输出逐字段不变）；温度计传 `exclude_suspended=True`。
- 新增 `app/engine/market_temperature.py`：六个子项（上涨占比 / 涨停 / 跌停 / 站上 MA20 / 量能比 / 指数 20 日年化波动率），每项输出原值、0–100 归一分、权重、方向、说明；固定分段线性归一化，**不做当日 min–max**，跨日可比。
- 权重与断点集中在 `app/core/constants.py`（`TEMP_WEIGHTS` / `TEMP_BREAKPOINTS` / `TEMP_COOLING_COMPONENTS`）；跌停与波动率为降温项（`direction=-1`，断点反向）。
- 缺失子项存 `null` 不补 0：`ok`=6/6；`partial`=4–5 项，权重在可用项上重新归一；`insufficient`=关键项（上涨占比 / 站上 MA20）缺失或可用项 <4，总分 `null`。MA20 覆盖率 <50% 视为该项不可用。
- 新表 `market_temperature_daily`（迁移 `0002_market_temperature`），列按任务书；`confidence` 因枚举值 `insufficient`（12 字符）无法装入任务书的 `VARCHAR(8)`，放宽为 `VARCHAR(16)`（模型与迁移同步注明）。
- 管线新增 `temperature` 步骤（排在 `features/scores/sector_scores` 之后），`TASK_DATASET` 映射 `market_temperature`，无新增 cron。
- 新接口 `GET /api/v1/market/temperature?trade_date=&days=`：单日返回全部字段 + freshness，`days>1` 返回升序 `series`；无记录 409 `DATA_NOT_READY`。

**前端**

- `lib/market.ts`：五档温度带（0–20 极冷 / 20–40 偏冷 / 40–60 中性 / 60–80 偏热 / 80–100 极热），边界归入较热一档；`temperatureLabel()` / `temperatureColor()`。
- `MarketTemperature.tsx`：纯 SVG 半圆仪表（`pathLength=100` + `stroke-dasharray`，无图表库）+ 六个子项细条（原值 / 归一得分 / ↑↓ / 降温徽标 / 说明）+ 60 日温度迷你曲线（固定 0–100 域，虚线 50，空值断线）。
- `confidence !== ok` 时顶部显示浅色告警并列出缺失子项；总分缺失显示「—」，不渲染 0；无记录（409）显示独立空状态。
- 挂载在 DashboardPage 顶部；`useTemperature` 仅在最新交易日视图每 10 分钟刷新，与 `useDashboard` 一致。

**回填与验证（2026-10-29）**

- `scripts/backfill_temperature.py --start 2025-10-01 --end 2026-10-26`：254/254 天完成，跳过 0、失败 0，用时 1.8 分钟；断点文件 `backend/var/temperature_backfill_progress.json`。
- 手算 2026-10-26：上涨 2082 / 有效 5217 = 0.3991 → 34.9；涨停 65 → 60.0；跌停 34 → 41.0；站上 MA20 1120/5211 = 0.2149 → 12.2；量能比 16114.32 亿 / 16729.38 亿 = 0.963 → 45.4；指数波动率 15.11% → 84.6；加权 = 41.28 → **41.3**，与库/接口一致。
- SQL 对账 3 个交易日（2026-10-26 / 09-30 / 06-30）上涨、下跌家数完全一致；MA20 手工 JOIN 得到 1120/5211 = 0.2149（±0.0001 内）；成交额与 `sum(fact_stock_daily.amount)` 相同。
- 日报口径：`build_daily_report_input` 的 breadth 与温度计在 10-26 / 09-30 完全一致；06-30 日报 flat=84、温度计 flat=83，差异来自一条 `close IS NULL` 且未标记停牌的行——日报保持旧口径（不剔除），温度计按任务书剔除停牌，属预期差异。
- `insufficient` 实测：事务内删除 2026-09-30 特征后回滚，`temp_score=null`、`confidence=insufficient`、`above_ma20` 标记不可用并提示覆盖率 0%；前端显示「温度数据不足，暂不给出总分。缺少：站上 MA20」，仪表为「—」，页面无 `NaN`。
- 60 日曲线连续（`?days=60` 共 60 点，24.3–79.8，无 0 分断点）；`pytest` 179 passed / 15 skipped（新增 `tests/test_temperature.py` 13 项）；`npm run build` 通过（主包 868.95 kB / gzip 255.74 kB，CSS 37.19 kB）。

### 7.5 T4 板块热力图升级为 treemap（2026-10-29）

**目标**：面积 = 市值、颜色 = 涨跌幅的 treemap，叠加资金流与 20 日相对强度，一眼看出主线板块。

**后端**

- 迁移 `0003_sector_metrics` 给 `fact_sector_daily` 增列：`market_cap / float_cap / main_net_inflow NUMERIC(24,2)`、`rs_20d NUMERIC(8,3)`、`member_count INTEGER`；只 `ADD COLUMN`，不动既有列与索引。
- 新增 `app/engine/sector_agg.py`：一条 CTE SQL 按当日 SCD2 名单（`effective_from <= d AND (effective_to IS NULL OR effective_to > d)`）聚合 `fact_stock_daily.market_cap/float_cap` 与 `fact_moneyflow.main_net_t0`，`member_count < 3` 时三项置 `NULL`；`rs_20d` 取板块与 `sh000300`（表 `market_indices`）各 21 个收盘价，任一侧不足 → `NULL`。
- 写库用 `UPDATE ... FROM jsonb_to_recordset(...)` 一次提交；空值保持 `NULL`，不写 0。管线新增 `sector_agg` 步骤（`moneyflow` 后、`sector_scores` 前），`TASK_DATASET` 映射 `sector_metrics`；历史回填脚本 `scripts/backfill_sector_metrics.py`（按日分批 + checkpoint，幂等）。
- `GET /api/v1/market/sectors` items 增列上述 5 个字段，原字段不变（契约 3.5）。

**前端**

- 新增 `lib/treemap.ts`：手写 squarified treemap 纯函数（不引 d3/echarts），过滤 0/负值/非有限值，面积守恒、不重叠；`treemap.test.mjs` 6 项 Node 原生单测（`npm run test:treemap`）。
- `color.ts` 色阶饱和点由 ±8% 改为 ±5%；`SectorHeatmap.tsx` 重写为 treemap：面积 = `market_cap`（缺失/非正剔除并在角落显示「已剔除 N 个（数据不足）」），等权模式每板块同面积；市值全缺时自动回退等权并提示。
- 顶部切换 `industry / concept`（走 `sector_type`）与 `市值 / 等权`；默认前 80 大，其余折叠为「其他」（点击或「显示全部」展开）；标题栏展示板块数、市值覆盖、成分合计。
- 格子标签按面积阈值显示板块名 + 涨跌幅（0 附近归一为 `0.0%`，不出现 `-0.0%`）；hover tooltip 展示 `pct_chg / 主力净流入 / rs_20d / 涨跌家数 / 龙头 / 三面评分`；`up_count/down_count` 同为 `null` 时显示「不适用」，不渲染 0。
- 点击板块打开内嵌抽屉（`useSectorDetail` → 3.2 接口），不新增路由；抽屉含行情、三面评分、龙头与成分股，Esc/遮罩关闭。
- 仅容器层 framer-motion 淡入（无逐格 stagger），`useReducedMotion` 下跳过动画。

**回填与验证（2026-10-29）**

- `scripts/backfill_sector_metrics.py --start 2025-10-01 --end 2026-10-26`：254/254 天、115,119 行、0 失败，用时 1.0 分钟；断点 `backend/var/sector_metrics_backfill_progress.json`。
- SQL 手算 2026-10-26 `THS_融资融券`：`member_count=391`、`market_cap=39,456,931,625,631.39`、`main_net_inflow=18,690,765,741.97`，与库内/接口完全一致；`THS_银行` 的 `rs_20d=10.391`（板块 1369.643→1389.251，基准 4507.393→4103.543）与手算一致。
- 接口实测 2026-10-26：industry 90 个全部有市值/资金流/rs；concept 375 个中 252 个有市值与资金流、374 个有 rs、88 个无名单、35 个为不完整名单。
- 浏览器实测：行业 ↔ 概念切换后旧格子不残留（概念模式 sample 均为概念名）；等权 90 个格子面积完全相等（1314.1 px²），面积和/容器面积 = 0.99958（市值模式 0.99978，差异为像素取整）；概念全量 375 格一帧渲染 **9.4 ms**（含 React 提交与首帧），低于 ~100 ms 阈值，N 保持 80。
- tooltip 对 `参股银行`（同花顺源）显示「涨跌家数 不适用」；抽屉实测打开/关闭正常；亮/暗主题均通过，Console 仅既有 react-router v7 warning。
- `pytest` 179 passed / 15 skipped；`npm run test:treemap` 6 passed；`npm run build` 通过（主包 884.01 kB / gzip 260.36 kB，CSS 38.70 kB）。

**成分名单补全与详情名称（2026-10-30）**

- 迁移 `0004_sector_member_partial` 给 `dim_sector_member` 增列 `is_partial`；THS `pages > 10` 的名单只含前后各 5 页（概念 100 只 / 行业 200 只），标记后不参与市值、资金流与涨跌家数聚合，只提供龙头与评分。
- 浏览器 harness 补抓概念板块：新增 82 个板块（52 个完整、30 个不完整），其中 81 个来自本轮抓取、1 个为此前被不完整文件覆盖的完整名单；SCD2 已入库并推进断点，累计 377/465 个板块有名单（342 完整、35 不完整）。
- 全区间重跑 `backfill_sector_ths.py --phase aggregate --start 2022-09-01 --end 2026-10-26`：板块日线更新 289,832 行，35 个不完整名单板块仅回填龙头（30,074 行）；`backfill_sector_metrics.py --start 2025-10-01 --end 2026-10-26 --force` 254/254 天、115,119 行、0 失败。
- SQL/接口核对：`THS_煤炭概念` 89 家、市值 3,465,356,066,240.66、主力净流入 1,063,167,591.25、涨跌家数 50/33、龙头林州重机；`THS_锂电池概念`（不完整）市值/资金流/家数均为 `null`，龙头德尔股份 +12.543%、`rs_20d=-3.077`。
- 3.2 板块详情的 `members` 联表 `dim_stock` 返回 `name`（缺失为 `null`），每板块最多 200 条；抽屉实测 `THS_银行` 42 只、`THS_锂电池概念` 100 只均带名称。

**已知**

- 概念板块仍有 88 个无 SCD2 名单（`member_count=0`）、35 个仅有 THS 前后各 5 页的 100 只样本（`member_count=null`）：市值/资金流为 `null`；市值模式剔除并计数，等权模式仍展示，涨跌家数显示「不适用」。
- 剩余名单受数据源限制未能补齐：THS 在补入 82 个板块后触发 IP 级 403（冷却 600s 仍拦截）；新浪 `gn_/chgn_` 节点返回空；东财连接被重置。WAF 解除后重跑 harness → `import_ths_members.py` → 聚合/指标回填即可继续，断点已记录 377 个已完成板块。
- 不完整名单的抽屉成分股只展示已抓取的 100 只，未额外标注「名单不完整」；tooltip 的「成分 —」与 3.5 的 `member_count=null` 已可区分。

### 7.6 T5 策略天梯（2026-10-30）

**目标**：每天统计各策略信号 T+1 买入后 5/10/20 日的前瞻收益与胜率，回答"最近哪个策略能用"。

**后端**

- 迁移 `0005_signal_perf`（新 head，`down_revision=0004_sector_member_partial`）新建 `stg_signal_perf`：联合主键 `(algo_code, signal_date, code)`，索引 `(algo_code, signal_date)` 与 `(algo_code, mature_20, signal_date)`；ORM 同步 `StgSignalPerf`。
- 新增 `app/engine/signal_perf.py`（**不放 strategy 层**，全文件无 `.shift(-`，docstring 标注仅供事后统计）：T 日信号 → T+1 开盘买入，价格 `open × adj_factor`；H 日收益 `close × adj_factor / 买入价 − 1`；超额对 `sh000300` 同窗口（指数不复权）。成熟度以本地行情最后一天 `fact_stock_daily.max(trade_date)` 截断，避免交易日历中的未来日期把在路上信号算成熟；T+1 停牌/一字板或缺 K 线记 `fillable=false`，统计整体排除。
- 同一 `(algo, signal_date)` 可能有多条参数 run：按 `created_at DESC NULLS LAST, run_id DESC` 取**最近一次成功且 `is_backtest=false`** 的 run 作为唯一来源（无 `run_id` 列时保证幂等的口径）。
- `recompute_signal_perf(start, end)` 先算后删再插，区间幂等；外部 session 只 flush，事务由管线/API 负责。管线新增 `signal_perf` 步骤（`algos` 之后），每次重算**最近 25 个交易日**；worker 新增 17:10 `signal_perf` cron（advisory lock）；admin 任务白名单与 `TASK_DATASET`/新鲜度 `signal_perf` 数据集同步。
- 新增 `scripts/backfill_signal_perf.py`：按交易日分段（默认 60 日）、每段提交后写断点并与库内行数对账，支持 `--force/--dry-run/--algos`。
- 新接口 `GET /api/v1/algo/ladder?window=20|60|120&horizon=5|10|20&trade_date=`（契约 5.4）：返回 5/10/20 三组统计、互斥分桶（`immature/unfillable/no_data`）、`low_sample`、近 20 期胜率 sparkline 与 `signal_perf` 新鲜度；顶层字段等于所选 horizon。

**前端**

- `StrategyLadder` 放在选股中心顶部：排名 / 策略 / 5d·10d·20d（平均超额 + 胜率 + 样本）/ 近 20 期胜率 sparkline（手写内联 SVG）；窗口 20/60/120 与 horizon 同屏切换；低样本行弱化并标注；点击行选中策略并滚动到参数/结果区。
- 明确显示"数据截至 T 日、未成熟 N 条、不可成交 M 条已剔除"，并注明胜率是信号级 `ret_H > 0` 占比，**与回测 FIFO 回合胜率不同**。

**回填与验证（2026-10-30）**

- 全量回填 `2021-01-01 ~ 2026-10-30`：本地日历 925 个交易日、16 段、**316 行**、0 失败，用时 < 1 秒；`207` 行可成交、`182` 行 `mature_20`。
- 抽样 SQL 复算：`b1 / 688143 / 信号 2026-08-17` → T+1 `2026-08-18` 开盘 154.0，20 日后 `2026-09-15` 收盘 190.0，`ret_20=23.3766%`；库内/接口一致（差异 0.000023 pp 为 4 位小数存储舍入）。超额手算 29.2121%，差异 -0.000015 pp。
- 幂等：`backfill_signal_perf.py --force` 重跑后整表校验码（除 `computed_at`）不变（`e072558ed7c2cafd6740b2384ad802ef`）。
- 未成熟：最新 25 日窗口 134 条信号、`mature_20=0`；`b5` 共 14 条、9 条未成熟，有效样本 5 条，`win_rate=0.2` 未被未成熟样本稀释。
- 回测隔离：`stg_signal_perf` 中**找不到非回测成功 run 来源**的行 = 0；7 个启用策略的最新非回测信号缺失行 = 0。注意任务书里的 `(algo_code,signal_date,code) in (select … where is_backtest)` 会返回 51：回测 run 与真实信号共享同一批键，该 SQL 只能证明键重叠、不能证明来源；来源校验以上述"无来源行=0"为准（本次回填按构造只读非回测 run）。
- `pytest` 202 passed / 15 skipped（新增 `test_signal_perf.py` 23 项）；`npm run build` 通过；浏览器实测 7 行、20/60/120 与 5/10/20 切换、行点击选中 B1 并滚动到参数面板，Console 无新增错误。

**已知**

- 真实库中非回测信号仅 **13 个不同交易日**（最新 2026-10-26），因此窗口内多数策略有效样本 < 30（`low_sample=true`），`b2`/`b3` 当前 20 日样本为 0、`b4` 窗口内无信号：这是真实数据积累不足，不是计算口径问题；随每日 16:30 选股与 17:10 统计会逐步增加。
- 最新信号 T+1 尚未落地（本地行情截至 2026-10-26）时 `fillable=false` 且计入 `immature`，不会计入胜率；行情补齐后 25 日窗口会自动重算修正。

### 7.7 T6 多策略共识榜（2026-10-30）

**目标**：把同日被多个策略同时选中的个股聚合为一份共识榜，回答"哪些票是多个策略的共同选择"。

**后端**

- 新增 `app/engine/consensus.py`（纯读，无写库）：取最新行情日（或指定 `trade_date`）**当日全部成功且非回测的 run**（`status='success'`、`is_backtest=false`），限定 7 个启用策略；同一 `(algo_code, code)` 保留最佳 rank 的命中；`hit_count = count(distinct algo_code)`，**不做跨策略分数平均**（各策略分数量纲不同）。
- 单条 SQL 完成 run→signal→dim_stock/fact_stock_daily/fact_stock_score_daily 左联，按 `GROUP BY` 之外的方式在内存中聚合，无 N×策略循环；`dim_stock` 附 `industry_l1/board/ownership`，行情附当日 `pct_chg`，另附 `composite_score` 与每个命中策略的 `metrics` 前 3 个键值。
- `dim_sector_member` 以 SCD2 `EXISTS` 生成 `in_sector` 标记（`sector_type` 可选），不进入 `WHERE`，因此过滤只影响榜单条目、不影响 `total_algos_evaluated`。
- 排序：`hit_count` 降序 → 最佳 rank 升序 → `composite_score` 降序（NULL 置后）→ `code`；`limit` 默认 100。无行情数据时抛 `DataUnavailableError`（接口 503）。
- 新接口 `GET /api/v1/algo/consensus?trade_date=&min_count=&limit=`（契约 5.5）：`min_count` 2–7、`limit` 1–500，越界 422；返回 `items`、`total_algos_evaluated`、`freshness`（`algo_run`）与固定 `note` 提示语。

**前端**

- `AlgoCenter` 在策略卡片下方、`#algo-detail` 上方新增 `ConsensusBoard`，**默认折叠**（折叠时不发请求）；展开后请求 `min_count=2`。
- 表格列：代码+名称（含板块/央企标注）、命中徽章（实心/空心圆 `●●○○○○○ 2/7`，按命中率给主色深浅，7/7 最深）、行业、当日涨跌、综合评分、命中策略 chips（点击展开各策略 rank/score/metrics 前 3 项，可"选中策略"跳转详情）、自选星标（复用 `frontend/src/lib/watchlist.ts`，localStorage `gfna-watchlist`，上限 100）。
- `min_count` 滑杆 2–5；固定展示"各策略评分量纲不同，榜单按命中策略数排序，不做跨策略分数平均。"

**验证（2026-10-30，数据截至 2026-10-26）**

- SQL 对账：全量分布 `hit_count=1 → 209 只`、`hit_count=2 → 12 只`，最大命中 2 ≤ 启用策略 7；`min_count=2` 返回 12 行，与手写 SQL 一致。
- 失效 run 排除：临时将 `b1` 当日 run 置为 `failed`，`total_algos_evaluated` 7→6、`b1` 命中项消失；恢复后回到 7 / 20 条 b1 信号，库内状态已还原。
- 性能：引擎进程内 10–13 ms；HTTP 冷启 43 ms、稳定 15–17 ms（< 100 ms 要求）。
- 测试：新增 `test_consensus.py` 14 项纯逻辑单测、`test_api_v5.py` 2 项接口用例；全量 `pytest` **233 passed**（GFNA_TEST_DB=1）。
- 前端：`npm run build` 通过（index 901.52 kB / gzip 265.91 kB）；浏览器实测默认折叠零请求、展开 12 行、徽章 `●●○○○○○ 2/7`、601838 详情 A4 rank 3 / A3 rank 45 各带 3 项 metrics、滑杆 3 空态、2 恢复、自选增删、选中策略跳转详情；Console 无新增错误，Jobs/Dashboard/个股页无回归。

**已知**

- 当日真实数据只有 `a3/a4/b1/b2/b3/b5` 产生非回测信号（`b4` 0 条），因此 `hit_count` 目前最大为 2；`min_count≥3` 为空是数据积累问题，不是口径错误。
- 同一策略当日的多个参数 run 都参与命中判定（任务书要求"当日全部成功 run"），若后续同一策略频繁多 run，`hit_count` 只按 `distinct algo_code` 计，不会因多 run 重复计数。
- 共识榜未接入自选页（T8 才建完整自选面板），当前星标仅落 localStorage。

### 7.8 T7 信号逐条解释（2026-10-30）

**目标**：回答"为什么是它"——B1–B5 逐条列出触发条件，A3 展示分项评分贡献，且解释与选股共用同一份条件声明，绝不并行维护。

**后端（单一事实源）**

- `condition.py` 扩展：`FieldRef` 支持跨列比较（`close > ma120 × 1.02` 之类），新增 `notna` / `isna` 算子；`Condition` / `ConditionGroup` 增加 `key/label/scope/detail_fields` 元数据。新增 `explain()` 逐条求值与 `triggered_row()` 逐行输出，值经 `_plain()` 转换（numpy / Decimal / NaN / 日期全部 JSON 安全）。
- `strategies.py` 新增 `CONDITION_SPECS: dict[str, ConditionGroup]`（B1–B5），条件从重构前的 `strategy_mask()` 逐字搬运；`strategy_mask()` 改为 `_evaluate_strategy()`：由 `CONDITION_SPECS` 生成掩码与逐条明细，再叠加 `b4_ok` / `b5_ok` 市场门槛（`scope="market"`）。A3/A4 保持原有专用口径，不受影响；B5 的 `boll_lower_dev_pct` 仅在求值副本上物化，不改库表。
- 每个信号在 `stg_signal.metrics` 内写 `triggered`（无新表/新列）：`key/label/scope/required/passed/actual/op?/threshold?/detail?`，条件组（阳线或收涨、趋势支撑）折叠为一条整体布尔值，市场门控单独成项。
- A3 新增 `metrics.score_breakdown`：直接读 `fact_stock_score_daily.detail` 的 `percentile/weights/raw`，按 `FACTOR_WEIGHTS` 顺序输出 6 个分项，不新增评分计算；同时按 `composite_factor` 的逐行归一口径给出 `weight_sum` 与每项 `contribution = score × weight / weight_sum`，逐项贡献之和与 `composite` 一致（±0.01）；无 detail 的旧 run 返回 `null`。
- CSV 导出跳过 `triggered` / `score_breakdown` 等结构化字段，避免把 dict/list 的 repr 写进单元格。

**行为等价性（硬门槛）**

- 新增临时脚本 `backend/scripts/compare_mask_refactor.py`，内嵌重构前掩码逐字副本，对最近 20 个交易日（2026-09-21 ~ 2026-10-26）× 7 个策略比较逐行掩码与 Top20 顺序：**140 次对比全部零差异**。逐日信号数示例：b1 122→…→57、b5 112（09-29）/199（10-13）/9（10-26）、b4 全 0（市场门槛未通过），`old/new` 计数与顺序完全一致。

**部署刷新（已执行）**

- 对 2026-10-26 全部 7 个策略执行 `POST /api/v1/algo/{code}/refresh`（默认参数、limit=20）：a3 20 / a4 20 / b1 20 / b2 9 / b3 2 / b4 0 / b5 9。B1 20 行全部带 9 条 `triggered`；再走缓存命中（`from_cache=true`）仍带 9 条，证明 JSONB 已落库。b4 当日沪深300 收盘低于 MA20，按设计 0 信号。

**前端**

- 新增 `components/SignalExplanation.tsx`：B 系列按 `scope` 分组（个股条件 / 市场环境要求），每条 `✓/✗ + label + 实际值 vs 要求`，通过项正常色、未通过项弱化为灰色；`detail` 以等宽小字展示原始字段。旧 run 显示"该 run 为旧版本快照，无逐条依据"，不渲染空列表。
- A3 面板展示 6 条 0–10 分贡献条（`scorePercent()`），附权重、分位、归一化贡献与原始因子值；标题显示可用权重合计。底部写明"综合分 = 10 × Σ(分位 × 权重) / Σ(当日可用权重)，四舍五入到 2 位；贡献 = 分项得分 × 权重 / Σ(当日可用权重)，逐项贡献之和与综合分一致（±0.01）"。
- `DataTable` 增加可选行展开（chevron 列、`renderExpanded`、展开行不触发行点击）；`AlgoDataTable` 将 `triggered/score_breakdown` 从动态指标列中排除，并接入展开面板；面板底部固定展示策略 `full_desc` 与"参数快照来自 run.params"（实际列出 run 快照键值）。
- A3 实测：综合分 8.15，可用权重 100%，六项贡献 1.00 / 1.39 / 2.34 / 1.46 / 1.46 / 0.50，加总 8.15；条形宽度 50% / 92.9% / 93.6% / 97.5% / 97.1% / 50%。

**验证**

- 全量 `pytest`（`GFNA_TEST_DB=1`）**241 passed**；新增 `test_condition_dsl.py` 7 项与 A3 拆解 1 项。
- `npm run build` 通过：index 907.90 kB / gzip 268.04 kB，CSS 42.65 kB。
- 浏览器（数据截至 2026-10-26）：B1 展开 9 条 = `CONDITION_SPECS["b1"].children`；B5 展开 10 条（9 个股 + 1 市场环境），分组标题正确；选择旧 run `32dabfe8…` 展开显示降级提示；亮/暗主题切换正常；Dashboard/Jobs 无 NaN/undefined，Console 仅既有 react-router v7 warning。
- API 契约 5.2 已补充 `triggered`、`score_breakdown`、旧 run 兼容与部署刷新说明。

**已知**

- 缓存键不含代码版本，重构前的历史 run 不会自动补 `triggered`；需按上述部署步骤手动 refresh 最新交易日。更早历史 run 只能显示降级提示。
- b4 当前因市场门槛无真实信号，市场条件分组用 b5 验证；门槛由单元测试覆盖（`test_b4_market_scope_and_gate`）。
- A3 某因子全市场无分位时不计入 `weight_sum`、`contribution` 为 `null`，但分项条仍显示中性 5.0 分；面板已写明该口径，逐项贡献之和仍与综合分一致。`raw` 为空的因子显示"因子输入缺失"。

### 7.9 B4/B5 run 级市场门槛提示（2026-10-31）

- 响应顶层新增 `market_gate`（仅 b4/b5）：`index_code / available / close / ma20 / pct_chg / passed / rule / threshold`，用于 0 信号时解释原因。
- 不新增表/列：新 run 直接取 `build_candidates` 的 `attrs["market"]`；缓存 run 与 run 详情按 `trade_date` 调用 `load_market_context` 现算，旧 run 同样可显示。
- 前端在门槛未通过或指数缺失时，于结果表上方显示原因（b4：收盘 < MA20 × 0.995；b5：当日跌幅 ≤ -2%），空态文案同步为“市场环境未满足”。
- 验证：b4 2026-10-26 显示“沪深300 收盘 4103.54 < MA20 × 0.995 = 4254.29，本次不产生信号”；缓存回读一致；pytest / build / 浏览器通过。

### 7.10 T8 自选股 + 站内信号提醒（2026-10-31）

**目标**：自选股命中策略时导航栏出红点；轮询实现，不引入 WebSocket；无账号体系，名单与已读水位存**服务端单租户行**，localStorage 仅作本机缓存。

**后端**

- 新增 `engine/watch_hits.py`：单条 SQL（`stg_signal ⋈ stg_run` + `code = ANY(:codes)`）查询近 N 个交易日命中，参数全部绑定；过滤当前 `SPECS`、`status='success'`、`is_backtest IS FALSE`；同一 `(algo_code, date, code)` 只保留名次最优 run。窗口以最近一个非回测成功 run 的交易日为锚点，不依赖 `CURRENT_DATE`。
- 新增 `POST /api/v1/algo/hits`（契约 5.6）：`codes` 去重后 ≤100、每项 1–6 位数字（统一错误体 `INVALID_PARAM`），`days` 1–60；返回 `items[{code,name,algo_code,algo_name,signal_date,rank,hit_id}]`、`latest_signal_date`、`freshness`、`days`。POST 仅为容纳长代码列表，语义是只读查询。
- 新增单行状态表 `app_watchlist_state`（Alembic `0006_watchlist_state`）：`id=1` 检查约束、`codes`/`seen_hits` JSONB、`updated_at`；`engine/watch_state.py` 提供读取与单行 upsert（部分更新、last-write-wins）。
- 新增 `GET/PUT /api/v1/watchlist`（契约 5.7）：`codes` 复用 5.6 校验（≤100），`seen_hits` 校验 `algo_code:YYYY-MM-DD` 与真实日期（≤500），非法统一 422 `INVALID_PARAM`；响应始终返回完整状态。
- 测试：`tests/test_watch_hits.py` 13 项、`tests/test_watch_state.py` 17 项纯校验；`test_api_v5.py` 增加 5 项接口用例（命中、排除回测、越界/注入 422、状态 roundtrip 与部分更新、状态校验）。

**前端**

- `lib/watchlist.ts`：服务端单行为事实源，localStorage `gfna-watchlist` / `gfna-watchlist-seen` 仅缓存，`gfna-watchlist-pending` 标记未同步改动；启动 hydrate（本地 pending 优先推送；服务器为空时迁移本地名单；已读取并集），写入 400ms 防抖、失败 15 秒重试、`online` 立即重试；版本号防止在途 PUT 覆盖并发改动；隐私模式降级内存态，但刷新后可从服务器恢复。
- `hooks/useWatchSignals.ts`：60 秒轮询，仅 `document.visibilityState === 'visible'` 时启用（`refetchIntervalInBackground: false`）；`retry: false`（`client.ts` 已含一次 5xx/网络重试）；已读按 `hit_id = algo_code:signal_date` 去重，同一策略同一天多只股票只算一条提醒；新增 `useWatchSyncStatus` 供面板显示同步状态。
- 导航栏新增“自选”入口与 `bg-alert` 红点（>9 显示 `9+`，`aria-label="自选股有 N 条新信号"`，键盘可达）；新增 `/watchlist` 页（进入即写已读水位），列出自选、最近命中与明细表。
- 个股详情头部“☆ 加入自选 / ★ 已自选”；盘后分析龙头股旁快捷星标与自选区；新增 `WatchButton`、`WatchlistPanel` 组件。共识榜沿用 T6 星标，现与导航栏同步。面板与星标提示改为“名单保存在本机缓存并同步到服务器”，底部显示 `syncing / synced / offline`。

**验证（数据截至 2026-10-26）**

- 实测命中：`000739` → b2（rank 2）、`000560` → b3（rank 1），红点 = 2；进入 `/watchlist` 后已读水位写入两条，红点消失。
- 服务端同步：清理全部 `gfna-watchlist*` 后刷新，从服务器恢复 2/100 与已读水位、红点不误报；移除/新增一只后 GET 分别返回 `["000560"]` 与 `["000560","000739"]`。
- 隐私模式：仅让 `gfna-watchlist*` 的 storage 抛错，页面仍从服务器恢复 2/100；增删照常落库，刷新后恢复，`pending` 为空。
- 后台轮询（真实双标签页）：headless Chrome 156 + CDP，标签 A `/watchlist` 为 `visible`，切到真实标签 B 后 A 为 `hidden`；65 秒内 `/algo/hits` 新增 **0** 次；切回 A 恢复 `visible` 后 65 秒内恢复 **1** 次。
- 接口校验：200 个 codes、`"'; drop"`、`days=0/61` 均 422；参数绑定无注入面。
- `pytest` 全量 **277 passed**；`npm run build` 通过（index 923.76 kB / gzip 272.60 kB）；浏览器 Console 无新增错误，Dashboard/个股页无回归。

**已知**

- 单租户共享：所有浏览器访问同一后端即共享同一份名单，无用户隔离与鉴权；公网/多用户前需加用户维度（接口加 `user_id` 或独立表）。写入为 last-write-wins，两个浏览器同时编辑以最后提交为准，单机自用可接受。
- `days` 越界由 FastAPI 参数校验返回默认 422 结构（非统一错误体），与现有接口一致；`codes` 业务校验走统一错误体。

### 7.11 T9 回测面板增强（2026-10-31）

**目标**：月度收益、回撤、滚动超额、行业暴露四个视图全部从已落库的 `equity_curve` / `trades` 派生，不重跑回测；历史记录缺数据时明确降级。

**后端**

- 新增 `app/engine/backtest_view.py: build_backtest_extras()`（纯函数）：`monthly_returns` / `drawdown_curve` / `rolling_excess_20d` / `industry_exposure` + `extras_available`。
- 月度收益以「上月末净值」为基准串联（首月用月内首个净值点），按 `∏(1+ret/100)-1` 复利后与 `metrics.total_return` 一致；基准不足时 `benchmark`/`excess` 为 null。
- 回撤 `(nav/peak-1)×100`，最小值与 `metrics.max_drawdown×100` 对齐；滚动超额为 20 个交易区间，前 20 点为 null。
- 行业暴露按成交重建调仓日持仓（卖先于买），当日收盘价估值，缺失退化为最近收盘价/最近成交价；`dim_stock.industry_l1` 为空归入「其他」并计 `other_count`；末尾补「现金」桶，权重和恒为 100（±0.1pp）。
- `GET/POST /api/v1/backtest` 增列上述五项；`export.csv` 追加月度与回撤两个 block（`#` 注释 + 空行分隔），与界面同源同单位。

**前端**

- 新增 `components/BacktestExtras.tsx` 四个可折叠区块：月度热力图（年 × 12，±5% `trendHeat` 插值，格内超额小字、年合计、底部月均行）、回撤面积图、滚动超额折线（0 轴参考线）、行业堆叠条（最多 12 期，超出合并为「其余 N 期」均值；行业 Top4 + 其他行业 + 现金，行业块默认折叠）。
- `BacktestPanel.tsx` 成交明细新增「在 K 线查看」，跳 `/stocks/{code}?bt={bt_id}` 复用 T2 买卖标记；行点击同跳转。
- 数值统一走 `signed()` 归一化，避免极小负数显示「-0.0」。
- `api/client.ts` 默认请求超时由 20s 调整为 30s（`DEFAULT_TIMEOUT_MS = 30_000`），避免 A3 一年期回测（实测 19–21s）被前端先截断；重试策略不变（仅 5xx/网络错误，GET 默认 1 次、其余 0 次）。

**验证（数据截至 2026-10-26）**

- 实测 UI run `c8061575…`：`total_return 9.2889%`，月度复利差 **9.8e-05pp**；回撤最低 **-15.5522%** 与 `max_drawdown` 完全一致；行业权重每期合计 100%。
- CSV 抽查：2025-11 `0.28,-2.72,3.0`、2026-10 `6.55,-6.28,12.83`；最差回撤 `-15.55` 与界面副标题一致。
- 浏览器：四区块渲染，行业块默认折叠；热力图 24 格（12 格有值）、12 根堆叠条、回撤 1 条面积、滚动 1 条折线 + 1 条参考线；亮/暗主题切换正常；Console 无新增错误。
- K 线跳转：601857 标记为 `buy-2025-11-04`、`sell-2026-01-29`、`buy-2026-09-28`。
- `pytest` 全量 **286 passed**；`npm run build` 通过。

**已知**

- 本地个股行情截至 2026-10-26，而交易日历到 2026-10-30，直接用默认结束日期会报「2026-10-29 无个股行情数据」；需回补行情或把结束日选到数据就绪日。

### 7.12 T10 参数敏感性矩阵（2026-11-01）

**目标**：`rebalance_days × max_positions` 二维扫描，每格一次独立回测（复用同一引擎），热力图观察参数稳健性；同参数重发命中复用，不重复计算。

**后端**

- `backtest_run` 新增 `grid_id VARCHAR(36)`、`grid_cell JSONB` 与 `ix_backtest_run_grid`（Alembic `0007_backtest_grid`）；`grid_cell` 保存两轴坐标、区间与 `reused` 标记，矩阵可由行重建、不另建表。
- 新增 `app/strategy/grid.py`：两轴去重升序、范围校验（1–250 / 1–100）、**24 格硬上限**（422 带 `detail.max_cells`）；按 `(algo,start,end,参数子集)` 命中 success 记录直接复用；未命中先落 `queued` 行，响应后由后台任务逐格执行。
- `run_backtest` 增加可选 `bt_id`：复用调用方预建记录（同一条引擎代码路径，无特殊回测分支）；每格独立会话/事务，单格异常只标记该格 failed。
- 后台并发信号量 2，按事件循环惰性绑定（TestClient/多循环安全）；格内 `limit` 抬到 `max(limit, max_positions)`，与落库快照同口径，避免“执行参数 ≠ 复用比对参数”导致永远不复用。
- `POST /api/v1/backtest/{algo_code}/grid` 立即返回（`elapsed_ms=0`）；起始日早于数据起点自动裁剪并回传 `requested_start/effective_start/start_trimmed`；区间上限 6 年。
- `GET /api/v1/backtest/grid/{grid_id}` 返回每格 `metrics` 六项摘要、`progress{done,total,failed}`、`total_elapsed_ms` 与 `aligned`（起止日 + 日历来源多数派）；不重算回测；未知 `grid_id` 404。

**前端**

- `BacktestPanel` 改为「单次回测 / 参数敏感性」两个标签页；T9 结果视图抽为 `BacktestResultView`，单次结果与矩阵单格详情共用。
- 新增 `SensitivityMatrix.tsx`：结束日默认取 `/health.latest_trade_date`（避免行情落后自然日）；两轴多选预设 + 24 格超限提示；扫描中 3s 轮询、标签页隐藏或矩阵完成即停止；`grid_id`、进度、失败数与计算耗时合计。
- 热力图：超额/回撤双向色阶（A 股红涨绿跌），可切 Sharpe/胜率/交易笔数；格内第二行固定对照回撤（或超额）；复用格标记「复用」；点击格展开完整 T9 回测视图。
- 过拟合提示：默认参数格 vs 矩阵最优格，叠加四邻域超额极差；默认明显落后且邻域波动 ≥2pp 时给出「不建议把最优格当实盘参数」警告。
- `vite.config.ts` 为 `/health` 增加 dev 代理（nginx 早已单独反代），否则 dev 下取不到最新交易日、矩阵默认结束日会落在无行情日期。

**验证（数据截至 2026-10-26）**

- 后端 2×2（`[10,20]×[10,20]`，2025-11-01~2026-10-26）：4 格独立 `bt_id` 均可 GET；后台并发实测进度 0→2→4；默认格 (20,20) 与 T9 单次回测六项指标完全一致（`total_return 0.130864` / `excess_return 0.249027` / `max_drawdown -0.103659` / `sharpe 0.8692` / `win_rate 0.3694` / `trade_count 242`）。
- 重发同一矩阵：`reused=4, queued=0, elapsed_ms=0`，`bt_id` 不变、`backtest_run` 行数不增；失败格（未来无交易日）与区间/日历不一致格的隔离与 `aligned=false` 均有集成测试覆盖。
- 浏览器（`Chrome/156.0.8078.48`，`--remote-debugging-pipe` 双真实标签页，反节流开关开启）：起始页 3 次请求（前置窗口），第二标签页激活后 `visibilityState` 由 `visible→hidden`、8s 后台窗口 **0 次**请求（`intervalsCovered=2.67`），回前台后 `hidden→visible` 并恢复 **3 次**（`gapS` 13.2/4.1/3.0）；矩阵完成后 7s 内 **0 次**请求，Console 无错误，退出码 0。
- 同一轮浏览器跑默认 2×2（`[20,40]×[10,20]`，2025-01-02~2026-10-26）：`grid_id e828bbf1…` 4/4 完成、失败 0、耗时合计 1 分 28 秒（36.81/6.93/37.08/7.26 s）；热力图按超额红涨绿跌着色、回撤第二行、过拟合提示（默认 (20,20) 超额 +18.17%，邻域极差 20.47 pp），无 `NaN/undefined/-0.00%`。此前一轮已验证五种指标切换、复用标记、单格完整 T9 视图与亮/暗主题。
- `pytest` 全量 **300 passed**（含 `test_grid_unit.py` 10 项与 `test_api_v5.py` T10 集成测试）；`npm run build` 通过（index 959.32 kB / gzip 282.12 kB，CSS 46.44 kB，KlineChart 194.96 kB，仅 chunk 体积告警）。

**已知**

- 复用会把历史记录 `grid_id` 改挂到最新矩阵，旧 `grid_id` 随即 404；矩阵链接不持久化，单机自用可接受。要保留历史需增加 `backtest_grid` 主表 + 关联表（约 1 张表 + 2 个接口，成本约半天）。
- 后台执行是进程内 `BackgroundTasks`，服务重启会留下 `queued/running` 行且不会自动续跑；当前由用户重发矩阵触发复用/重算兜底。要可靠续跑需引入任务队列（成本高，暂不做）。
- 并发上限 2 是进程内信号量；若未来多 worker 部署，实际并发为 2×worker 数，应改为数据库/队列级并发控制。

### 7.13 T11 纸面组合跟踪（2026-11-01）

**目标**：每个启用策略自动建一条纸面组合，从首个实盘 run 的建仓日至今整段重算净值，口径与回测一致；17:30 自动更新，非交易日跳过。

**后端**

- 新增 Alembic `0008_paper_portfolio`：`paper_portfolio`（一策略一组合：建仓日、参数快照、初始资金、状态）+ `paper_nav`（每交易日一行：nav/benchmark/drawdown/holdings/cash_ratio/pending_orders/vs_backtest），主键 `(portfolio_id, trade_date)`，索引 `(portfolio_id, trade_date DESC)`。
- 新增 `app/engine/paper.py`：**全区间重算，不做增量**；逐日 `INSERT ... ON CONFLICT DO UPDATE`，重复同步幂等。净值/基准/回撤/持仓/现金/待执行均由回测引擎产出，后复权、佣金/印花税/滑点、整手取整与回测同口径。
- `simulate_portfolio` 增加 `snapshots_out`（收盘持仓、现金占比、下一交易日待执行）；待执行订单复用 `_order_fillable`，停牌/一字涨停/跌停写 `fillable=false + block_reason`。**已知卖不出的持仓仍占名额**，不再生成对应买入计划（修复一处会虚增仓位的逻辑）。
- `vs_backtest` 只在存在**参数快照完全一致**（调仓周期/持仓数/基准/费率/滑点/最低佣金/初始资金/ST 与新股过滤）的同区间人工回测时写入，否则为 null，避免不同参数回测算出的“超额”误导。
- API：`GET /api/v1/paper/portfolios`（含 pending 虚拟卡）、`GET /api/v1/paper/portfolio/{algo}?days=250`、`POST /api/v1/paper/portfolio/{algo}/sync`（后台执行）；未知策略 404，禁用策略 422。17:30 cron 排在 algos/signal_perf 之后，非交易日只记日志不写库。

**前端**

- 新增 `/paper` 页面：组合卡片（净值/收益/超额/回撤/跟踪天数/vs 回测徽章/freshness）、明细（指标卡、同参数回测差值、净值 vs 基准双线、回撤面积、持仓表）、“明日待执行”高亮区块（可成交 / 不可成交 + 原因）。
- 导航新增「纸面跟踪」；`/algo-center` 每个启用策略卡片加「纸面跟踪」入口（携带 `?algo=`）直达明细；窄屏导航改为横向滚动并隐藏搜索框，`/paper` 与 `/algo-center` 工具栏不再横向溢出。

**验证（数据截至 2026-10-26）**

- 行数与交易日历逐一核对：a3 563/563、a4 679/679、b1 45/45、b3 27/27、b5 45/45，缺失/越界/非交易日行均为 0。
- 对账：b3 与同参数人工回测 `70b3dec3…`（2026-09-10~2026-10-26）`total_return -0.6648%`、`excess 9.1155%`、`max_drawdown -0.6699%`，纸面三项差值均为 **0.00pp**。
- 幂等：b3 重算 27→27 行、无重复、净值合计 26.984115 不变，仅 `updated_at` 前移。
- 非交易日实跑（2026-11-01）：日志「2026-11-01 非交易日，跳过纸面组合」，`paper_nav` 1359→1359 行；调度器下次触发 `2026-11-02T17:30:00+08:00`。
- 待执行阻塞样本（历史行）：b1 2026-09-14 买入 002531/002585、a3 2026-02-25 买入 600522 均因一字涨停 `fillable=false`；当前最新交易日无调仓，界面为空态。
- 浏览器（Chrome/156.0.8078.48，MCP 视口 311px）：7 张卡片、明细标题/参数/freshness、净值+回撤 2 图 4 个 recharts 面、3 条线/面积路径、a3 20 行持仓；卡片点击切换 `?algo=b1/a3` 正常；`/algo-center` 7 个「纸面跟踪」入口可见且点击可达；亮/暗主题 `--color-card` `#ffffff` ↔ `#1e293b` 切换后图表仍渲染；禁用文案扫描 0 命中；Console 仅 5 条基线（2 debug + 1 info + 2 router warning），无新增 error。
- 窄屏修复后 `/paper` 与 `/algo-center` 均为 `scrollWidth=305 <= innerWidth+1`；`/paper` 持仓表在自身 `overflow-x-auto` 内滚动（402px 内容 / 272px 容器），`/algo-center` 仍显示 7 个「纸面跟踪」入口。
- `pytest` 全量 **318 passed**（含 paper 单测 12 + API 集成 6）；`npm run build` 通过（index 976.74 kB / gzip 286.72 kB，CSS 47.49 kB，KlineChart 194.96 kB，仅既有 chunk 体积告警）。

**已知**

- `vs_backtest` 要求人工回测与纸面参数快照完全一致才显示；当前仅 b3 命中，其余卡片显示「暂无同参数同区间人工回测」（防误导，非缺陷）。
- 最新交易日通常不是调仓日，待执行清单多为空；不可成交原因只在调仓信号日出现，验收以历史落库样本为准。
- 全区间重算耗时随跟踪期增长：a4 679 个交易日约 48.5s、a3 563 日约 49.6s；当前串行 17:30 任务可接受，策略数量大增时需并发或缓存（成本高，暂不做）。
- 内置 MCP 视口固定 311px，375/1440/1920 视口未在本轮验证。

### 7.14 T13 骨架屏替换 spinner（2026-11-01）

**目标**：用与最终布局同形的骨架屏替换全站 9 处 `animate-spin`，延迟 150ms 才出现且提前占位；统一错误态展示 `ApiError.message` + `detail.next_action` + 重试。

**前端**

- 新增 `useDelayedFlag(active, delay=150)`（自研，不引依赖）与 `StateViews` 骨架原语：`SkeletonLine / SkeletonCard / SkeletonTable(rows, cols) / SkeletonChart(h) / SkeletonKpiRow`。
- 9 处 `animate-spin` 全部移除（`grep -r animate-spin frontend/src` = 0）：`AlgoParamPanel`、`BacktestPanel`、`SensitivityMatrix`、`RunMetaPanel`、`SectorHeatmap`、`WatchlistPage`、`PaperPage`、`HomePage`、`DetailPage`；按钮内等待态改为静态图标 + 骨架点，不再旋转。
- 关键实现：加载时**始终渲染完整骨架布局**，150ms 前用 `visibility: hidden` 占位、之后才可见。避免“空白 → 骨架 → 内容”两次位移；Dashboard/Home/Detail/Paper/Watchlist/Jobs 六个页面统一此模式。
- `index.css` 在 `prefers-reduced-motion: reduce` 下关闭 `.skeleton` 的 shimmer 动画。
- `ErrorState` 从 `detail.next_action` 提取建议文案（类型收窄，缺失则不渲染）；`HomePage` 原内联“⚠️ 加载失败”改为 `ErrorState` + `refetch`，补齐重试入口。
- `main.tsx` 查询默认 `networkMode: 'always'`：默认 `online` 模式断网时查询被挂起（`isLoading=false`），页面会落到“暂无数据”且没有重试按钮；改为真实发请求、由 `client.ts` 统一报 `NETWORK_ERROR`。`client.ts` 重试语义未改动。

**验证（Chrome/156.0.8078.48，CDP pipe，真实网络节流）**

- 600ms 延迟下点击 `/dashboard`：首个**可见**骨架 **186ms**（>150ms 防闪烁生效），骨架节点 16 个，可见持续 5060ms；接口耗时 3719ms。
- 布局守恒：骨架 `minTop=89` 与终态 `h1Top=89` 一致；`PerformanceObserver('layout-shift')` **0 条 / CLS 0.0000**（修复前空白占位过渡 CLS 0.5559，来源为 FOOTER 与 Navbar 位移）。
- reduced-motion 模拟下骨架 `animation-name=none / duration=0s`；禁用文案扫描 `-0.0%`/`NaN` 命中 0；加载稳定后骨架与 spinner 计数均为 0。
- 断网（`offline:true`，`navigator.onLine=false`）：`/dashboard`、`/paper`、`/algo-center`、`/stocks/600519`、`/` 均显示 `ErrorState` + `NETWORK_ERROR` + 1 个重试按钮，骨架/spinner 均为 0；`/watchlist` 与 `/jobs` 强制刷新同样进入错误态并可重试；恢复网络后点重试，`/paper` 回到正常数据。
- `next_action` 真实样本：`/algo-center` 发起 2026-10-31~2026-11-01 回测 → `回测执行失败`、`DATA_NOT_READY`、「交易日数量不足，无法回测」、`建议：POST /api/v1/admin/ingest/calendar`，重试按钮可用。
- `pytest` 全量 **318 passed**；`npm run test:treemap` 6 passed；`npm run build` 通过（index 980.23 kB / gzip 287.65 kB，CSS 47.95 kB，KlineChart 194.96 kB，仅既有 chunk 体积告警）。

**已知**

- `/watchlist` 断网首次进入可能先显示导航栏缓存的命中结果（共享 `useWatchBadge` 查询，`staleTime` 5 分钟），需点“刷新命中”才会出现错误态；属缓存优先策略，非骨架缺陷。
- `networkMode: 'always'` 会让断网时真实发出请求并失败，比默认挂起多产生一次失败请求；换来的是明确错误态与重试入口。
- 按钮内等待态现为静态图标 + 骨架点，不再有旋转动画；视觉上不再暗示“正在进行”，长任务需配合文案。
- 验收脚本创建的失败回测行（2026-10-31~2026-11-01）留在 `backtest_run` 表；该行不计入策略统计（统计只取 `status='success'` 且 `is_backtest=false`）。
- 本轮浏览器视口为 758×482（CDP）与 311px（MCP 错误态），375/1440/1920 未验证。

### 7.15 T14 表格内嵌 30 日 Sparkline（2026-11-01）

**目标**：纯 SVG 微型走势线（零依赖、无 canvas、无浮层），同页 100 条不明显掉帧；0/1/全等值/含 null 均不崩，宽度变化靠 viewBox 自适应。

**前端**

- 新增 `src/lib/sparkline.ts`（纯函数）：清洗非有限值为 null、只取最后 `days` 点、>200 点等距降采样且保留首尾；null 断点分段；0/1 个有效点、全 null、全等值均输出全宽平线；支持固定/自适应纵轴与界内 baseline。
- 新增 `src/components/Sparkline.tsx`：`viewBox + preserveAspectRatio="none"` 自适应宽度，描边 `vectorEffect="non-scaling-stroke"` 防拉伸；默认按首末方向用 `colorVar()` 红涨绿跌，可显式覆盖；**有效点不足时无条件灰色**（不再被显式 color 覆盖）；hover 用原生 `<title>`（末值/区间/点数/缺失数），不引 tooltip 库；带 `role="img"` 与 `aria-label`。
- 接入：`StrategyLadder` 近 20 期胜率（0–1、baseline 0.5）、`MarketTemperature` 60 日温度（0–100、baseline 50、面积填充，替换原内联 SVG）、`IndexCard` 30 日收盘（新增 `useIndexSeries(60)` 复用 `/market/indices`，staleTime 5 分钟，60 自然日覆盖长假）、`ConsensusBoard` 个股 30 日收盘（逐股 `useKline(30)`，缓存 5 分钟，表格新增「30 日走势」列）。
- `ScoreRanking` 仅加 TODO：板块综合分无历史接口，不为此新增重接口；需 `/market/sectors` 增序列或独立端点，成本约半天。
- `package.json` 新增 `test:sparkline`；`sparkline.test.mjs` 12 项（断点、空/单点/全 null/全等值、截断、降采样、固定/自适应纵轴、baseline 越界、非有限值、等距 x）。

**验证（Chrome/156.0.8078.48，headless CDP pipe，可见 1400×1600）**

- 100 条基准 + 4 条退化同页：104 个 SVG / 3012 点；模块加载→挂载完成 **48.1ms**，DOM 稳定 **48.7ms**；启动阶段最长帧 **44.4ms**（dev，含首屏布局），无 ≥50ms 长任务；稳定后约 **5.6ms/帧**。
- 交互：点「重绘 100 条」`flushSync` 提交 **10.5ms**（另一轮 11.5ms），800ms 内 147 帧、最大帧 **5.8ms**、>50ms 帧 **0**；Performance 指标 Layout 9 次/23ms、RecalcStyle 11 次/11ms、Task 199ms。
- 退化：空/单点/全等值分别为 0/1/5 点，描边均为 `rgb(100,116,139)`（`--color-flat`）并带「数据不足」；含 2 个 null 的 6 点序列分段绘制、`data-missing=2`、红色。
- 主题：属性保持 `var(--color-up)`，计算值亮 `rgb(220,38,38)` ↔ 暗 `rgb(248,113,113)`；亮/暗 token 为 `#dc2626/#067647/#64748b` ↔ `#f87171/#4ade80/#94a3b8`。
- 响应式：容器 160→320px 时 SVG 142→302px，`viewBox="0 0 120 28"` 与高度 28 不变，点坐标不重算、无溢出；`/dashboard` 8 条（60 日温度 + 7×30 日指数、0 缺失、禁用文案 0、`scrollWidth 413 ≤ 419`），`/algo-center` 共 19 条（7 天梯 + 12 共识，共识每条 30 点/0 缺失）。
- `npm run test:sparkline` 12 passed、`test:treemap` 6 passed、`npm run build` 通过（index 983.53 kB / gzip 288.99 kB，CSS 48.02 kB，KlineChart 194.96 kB，仅既有 chunk 体积告警）。

**已知**

- 天梯 `spark` 当前仅 1 个已完成周期（多数策略 0 个，窗口内 14 信号 / 9 个未到期），故显示灰色「数据不足」；需等后续交易日成熟或回填历史胜率，不是组件缺陷。
- 共识榜按行逐股请求 kline（min_count=2 时 12 行 = 12 请求），有 5 分钟缓存；榜单扩到几十行时请求数线性增长，需批量端点（契约变更，约 1 个接口 + 查询，半天）。
- ScoreRanking 未接入（无板块评分历史端点），保持 TODO；补齐需契约变更 + 历史回填。
- 仅原生 `<title>` 悬浮，无 tooltip 库；触屏无 hover 反馈，需要时再加点击浮层（约 1 个组件 + 定位逻辑）。
- 100 条性能为 dev server + 临时 harness 实测（harness 已删），生产构建更快但未单独复测；375/1440/1920 三档未逐一验证（本轮为 419/1400 宽）。
