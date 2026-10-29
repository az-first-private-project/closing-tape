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
