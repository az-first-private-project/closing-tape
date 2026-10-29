# GFNA V5 架构说明

> 本文描述**当前代码的真实结构**（不是规划稿）。方案原文见
> [`GFNA_V5_改造升级方案.md`](GFNA_V5_改造升级方案.md)，接口契约见
> [`V5-API-CONTRACT.md`](V5-API-CONTRACT.md)，逐文件变更见
> [`CHANGELOG-V5.md`](CHANGELOG-V5.md)。

---

## 1. 分层与依赖方向

```
api/  ──────────────►  strategy/  ──►  engine/  ──►  db/
 │                        │              │
 │                        └──────────────┴──────►  core/
 └──► pipeline.py  ──►  ingest/  ──►  db/
```

**硬规则**：
1. `ingest/` 只依赖 `core` + `db` + pandas/numpy；**不 import `api`/`strategy`**；
2. `engine/` 只依赖 `core` + `db` + `indicators`；**不联网**（除 `sector_analysis` 的资金流失效降级，受 `INGEST_NETWORK_ENABLED` 控制）；
3. `strategy/` 只读数据库，**不 import `ingest`**；
4. `api/` 不做任何计算，只做参数校验 + 读库 + 组装响应；
5. 定时任务与手动触发都走 `pipeline.run_task`，保证"命令行能跑的，接口也能跑"。

---

## 2. 数据模型（21 张表 + 2 视图）

### 2.1 维度

| 表 | 主键 | 关键点 |
|---|---|---|
| `dim_stock` | `code` | 含 `delist_date`（**退市股必须保留**，否则回测有幸存者偏差）、`board`（涨跌停幅度不同）、`ownership`（A3 输入） |
| `dim_sector_member` | `(sector_code, stock_code, effective_from)` | **SCD2**：成分调整用区间表达，回测取"当日有效"成分，避免用到未来信息 |
| `dim_stock_financial` | `(code, report_date)` | **估值的事实源**：EPS/BPS/ROE/分红率/EPS_TTM；`announced_date = 报告期 + 90 天`（市场可知日，防未来函数）。季度更新，落库后估值计算零网络 |
| `dim_stock_dividend` | `(code, ex_date)` | 现金分红明细；`dps = 派息比例/10`（元/股），按**除权日**做 point-in-time |

### 2.2 事实

| 表 | 主键 | 分区 | 说明 |
|---|---|---|---|
| `fact_stock_daily` | `(code, trade_date)` | **按年 RANGE**（2021–2027 + default） | 未复权价 + `adj_factor`；`volume`=股、`amount`=元；`is_limit_up/down/suspended` 是回测"不可成交"依据 |
| `fact_sector_daily` | `(sector_code, trade_date)` | — | 原 `sector_daily` 更名 |
| `market_indices` | `(index_code, trade_date)` | — | A 股四指数 + 沪深300 + 可选美股 |
| `margin_trading` | `trade_date` | — | 两融（T+1 披露，按已披露日落库）；北向可为 NULL |
| `fact_feature_daily` | `(code, trade_date)` | — | 预计算指标（MA/RSI/MACD/BOLL/ATR/动量/波动/量比/金叉/多头） |
| `fact_stock_score_daily` | `(code, trade_date)` | — | 六因子 0–10 + `detail`（原始值与权重） |
| `fact_moneyflow` | `(code, trade_date)` | — | 主力 T0–T4 与 5 日合计（元） |
| `sector_score` | `(trade_date, sector_code)` | — | 三面评分 + 两类龙头 + `detail` 子项 |
| `daily_report` | `trade_date` | — | AI 日报（Markdown + 结构化 结论/依据/风险） |

### 2.3 策略与治理

| 表 | 说明 |
|---|---|
| `stg_run` | 每次运行：`run_id`、参数快照、universe/signal 数、`data_version`、耗时、状态 |
| `stg_signal` | 信号明细（rank/score/metrics JSONB） |
| `backtest_run` | 回测：参数、13 项指标、净值曲线、逐笔成交 |
| `meta_data_quality` | 每个数据集的最后成功时间/行数/覆盖率/异常数 —— **前端新鲜度唯一来源** |
| `ingest_job` | 采集任务运行记录（含 checkpoint，断点续传） |
| `trade_calendar` | 真实交易日历（休市日也有行，`is_open=false`） |
| `algo_meta` | 策略元信息（可选持久化；代码内 `SPECS` 是事实源） |

### 2.4 视图

- `v_stock_latest`：最新交易日的个股行情 + 维度字段（看板/搜索用）；
- `v_stock_features`：行情 + 指标 + 因子评分（个股详情用，避免三次 join）。

---

## 3. 关键流程

### 3.1 每日盘后流水线（`worker.run_daily_pipeline_job`，15:36）

```
is_trade_day?  ──否──► 跳过（不写任何数据）
   │是
   ▼
calendar → universe → index → sector(东财,空则同花顺兜底) → sector_members(SCD2)
   → snapshot(全市场) → moneyflow → margin
   → valuation(财报/分红缓存 → PE/PB/股息率)   ← 仅"最新可得"增量，慢阶段走夜间任务
   → features(指标) → scores(个股因子) → sector_scores(板块评分)
   → report(AI 日报) → algos(A1–A4)
```
> 顺序硬约束：**日线 → 估值 → 指标 → 因子 → 选股**。估值必须在因子之前，
> 否则 `value/quality` 因子拿不到 PE/PB；而估值又依赖日线收盘价。

- 每步独立 try/except：单步失败不阻断后续，但会写 `meta_data_quality.status=error`；
- 全程 `pg_try_advisory_lock('gfna:ingest:<task>')`，多实例部署安全；
- 采集层拒绝"未收盘/非交易日"数据（`SNAPSHOT_READY_*=15:30`），避免写脏；
- 板块采集若东财返回 0 行，**自动降级同花顺**（`ingest/ths_sector.py`），并跳过东财已有行，避免覆盖完整数据。

### 3.1.1 估值链路（新增）

```
[季度/月度] sync_financials  → dim_stock_financial（EPS/BPS/EPS_TTM，announced_date=报告期+90d）
            sync_dividends   → dim_stock_dividend（dps，除权日）
[每日/回补] compute_valuation → 读缓存 + fact_stock_daily.close
             PE = close / EPS_TTM；PB = close / BPS；股息率 = Σ(近12月 dps, ex_date<=d) / close
             UPDATE fact_stock_daily 仅三列（不碰价格/成交量）
```

- **零网络**：每日估值只是本地计算（实测 2750 行 <1 秒）；
- **point-in-time**：`build_valuation_frame` 用 `merge_asof` 只取 `announced_date <= 交易日` 的最新报告期；
- **诚实的空值**：财报只回约 3.5 年（14–15 期），更早日期留空不外推；
- BPS 取「调整前」（实测 `调整后` 在个别标的上是脏值，如 688981 的 594.86）。

### 3.2 一次选股（`strategy.runner.run_strategy`）

```
参数校验/补默认 → 参数指纹 + 数据版本 → 查缓存
   │未命中
   ▼
一次 SQL 横截面（fact_stock_daily ⟕ fact_feature_daily ⟕ fact_stock_score
                 ⟕ fact_moneyflow ⟕ dim_stock）
   ▼  纯 pandas 条件过滤 + 排序（tie-break: code）
写 stg_run(running) → 写 stg_signal → 更新 stg_run(success, elapsed)
   ▼
返回 {run:{run_id, params, data_version, elapsed_ms, universe_count},
      data:[{code,name,rank,score,composite_score,metrics}]}
```

**缓存键 = `algo_code + trade_date + params_fingerprint + data_version`**
→ 参数或数据变了必然重算；同参同数据必然命中同一个 `run_id`（可复现）。

### 3.3 回测（`strategy.backtest.run_backtest`）

```
调仓日序列（trade_calendar，每 N 个交易日）
   │ 对每个调仓日 T：
   ├─ run_strategy(T) 取信号（只用 ≤T 的数据）
   ├─ T+1 开盘成交（涨停/跌停/停牌/无数据 → 不可成交并记 reason）
   ├─ 费用：佣金 max(金额×万2.5, 5) + 印花税(卖) + 滑点
   ├─ 持仓市值：units × close × adj_factor（后复权）
   └─ 记录 nav / benchmark
   ▼
compute_metrics（总收益/年化/回撤/夏普/索提诺/卡玛/胜率/盈亏比/换手/超额/波动）
   ▼
写 backtest_run（净值曲线 + 逐笔成交，可导出 CSV）
```

**防未来函数**：禁止 `.shift(-`；`assert_no_lookahead()` 静态扫描本包；
成交只用 T+1 开盘；测试可用"篡改价格"验证检测有效。

---

## 4. 口径约定（全仓库统一，改这里之前先改文档）

| 类别 | 约定 |
|---|---|
| 时区 | 全系统 `Asia/Shanghai`；数据库会话 `timezone=Asia/Shanghai`；列一律 `TIMESTAMPTZ`（禁止 naive `utcnow`） |
| 价格 | 未复权原始价；收益计算用 `close × adj_factor`（后复权） |
| 金额 | `amount`、`market_cap`、主力净流入：**元**；展示层才转万/亿 |
| 成交量 | **股**（数据源是"手"时在 Provider 内 ×100，只做一次） |
| 比率 | `pct_chg`/`turnover_rate`/`dv_ttm`/`amplitude`：百分数（如 `-1.614` 表示 -1.614%） |
| 评分 | 一切 `*_score`：**0–10**；个股因子分位 → `10 × pct` |
| 缺失 | 返回 `null` + `reason`，**不允许用 5.0 冒充"没数据"**（板块兜底 5.0 必须带 `detail.reasons`） |
| 交易日 | 只查 `trade_calendar`；禁止 `weekday()<5` |
| 缓存 | 键必须含参数指纹与 `data_version` |
| 交易日判断 | 采集写库前必须 `is_trade_day`，且快照需过 `SNAPSHOT_READY` 时刻 |

---

## 5. 数据质量门（`ingest/quality.py`）

- 行数阈值（股票池 ≥4000、快照 ≥3500）；
- 覆盖率 ≥95%（当日有行情的标的 / 在市标的）；
- `|pct_chg| ≤ 30%`（北交所涨跌停上限）；
- 价格跳变（当日与前收偏差 >50% 且非停牌 → 异常）；
- OHLC 逻辑（`low ≤ open,close ≤ high`）、`volume/amount ≥ 0`、重复 `(code, date)`；
- 不合格 → `QualityBlockedError`，**阻断写入**并记 `meta_data_quality.status=quality_failed`。

---

## 6. 可观测性

- **结构化日志**：`LOG_JSON=1` 输出 JSON 行，含 `request_id` / `task` / `run_id` / `dataset`；
- **`/health`**：数据库、最新交易日、是否就绪；
- **`/api/v1/meta/freshness`**：每个数据集的 `status/last_success_at/coverage/rows`；
- **`/api/v1/meta/jobs`**：采集任务历史（含失败原因、行数、耗时）；
- **`/api/v1/meta/partitions`**：分区清单（跨年自动建分区的可视化证明）。

---

## 7. 部署形态

| 形态 | 说明 |
|---|---|
| 本地开发 | `start.sh`（venv → 依赖 → bootstrap_db → uvicorn）；前端 `npm run dev`；或 `start-local.ps1` 一键起/停（独立进程） |
| Docker Compose | postgres(宿主 5433) + backend + frontend(Nginx)；`TZ=Asia/Shanghai`；容器入口 `bootstrap → first_run → uvicorn`；镜像内含 Playwright Chromium；`backend` 带 healthcheck |
| 定时任务 | 单进程 APScheduler + PG advisory lock；量大再换 Celery/Arq（方案 F2） |

---

## 8. 与方案原文的差异（有意为之）

| 项 | 方案 | 实现 | 原因 |
|---|---|---|---|
| 表名 | `sector_daily` 保留 | 更名 `fact_sector_daily` | 命名统一到 `fact_*`；库是干净重建 |
| 情绪子项 | 融资/北向/论坛/资金流 | 融资/北向/资金流/**新闻** | 论坛无稳定数据源，属虚假精度；新闻才有真实输入 |
| A3 评分 | 沿用腾讯四维（0–100） | 本地六因子（0–10） | 腾讯评分无法获得，不能假装有 |
| 北向资金 | 情绪输入 | 保留但可为空、缺项不拖累总分 | 2024-08 起暂停披露 |
| `algo_meta` | 启动写种子 | 代码内 `SPECS` 为准，表为可选持久化 | 避免"表空就 500"（原缺陷 #20） |
| 估值历史 | 未提及 | **已实现**：财报+分红缓存进库，本地推算 PE/PB/股息率（point-in-time） | `stock_a_indicator_lg` 已下线、东财/百度不可达；自算最可靠 |
| 板块数据 | 仅 AKShare | 东财失败时**同花顺兜底**（少字段，如实置空） | 东财在本网络被重置 |
| 容器首启 | 未提及 | 空库自动灌演示数据（`SEED_DEMO_ON_EMPTY`），已有数据则跳过 | 解决"启动后无数据"的真实原因 |

---

## 9. 前端设计系统（V6 T12）

Token 定义唯一入口：`frontend/src/index.css` 的 `@theme` 与 `.dark`（CSS-first，Tailwind v4 自动生成 `text-up` / `bg-up/10` 等工具类）。**语义色只允许通过 token 使用，禁止在组件写死红/绿 hex 或 `text-red-*`。**

| Token | 语义 | 亮色 | 暗色 |
|---|---|---|---|
| `--color-up` / `--color-up-strong` | 上涨 / 上涨底色上的文字 | `#dc2626` / `#991b1b` | `#f87171` / `#fca5a5` |
| `--color-down` / `--color-down-strong` | 下跌 / 下跌底色上的文字 | `#067647` / `#05603a` | `#4ade80` / `#86efac` |
| `--color-flat` | 平盘 / 缺失 | `#64748b` | `#94a3b8` |
| `--color-alert` | 错误/失败（rose，与涨红区分） | `#be123c` | `#fb7185` |
| `--color-success` / `--color-warning` | 成功 / 警告 | `#0f766e` / `#92400e` | `#2dd4bf` / `#fbbf24` |
| `--color-chart-1..5` | 图表分类色（无涨跌语义） | 蓝/紫/青/琥珀/灰 | 对应亮化 |

- 语义入口：`lib/format.ts`（`pctColor` / `priceColorClass` / `priceBgClass` / `trendGlyph` / `colorVar`）；色阶入口：`lib/color.ts`（`trendHeat` 连续插值、`chartColor`）。
- 数字统一 `.num`（等宽 + tabular-nums）；容器统一 `.card`；字号阶梯 11/12/14/18/28px。
- 默认深色：`index.html` 预渲染脚本在首帧前设置 `.dark`；`ThemeProvider` 尊重并持久化用户选择（`gfna-theme`）。
- 图表（lightweight-charts / Recharts）不得直接读 class；canvas 用 `colorVar()` 或 `getComputedStyle` 读 token，并在 `useTheme()` 变化后重绘。
