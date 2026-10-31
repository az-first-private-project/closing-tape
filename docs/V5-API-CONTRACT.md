# GFNA V5 · 接口契约（API Contract）

> 本文件是**前后端并行的唯一事实源**。任何一端要改字段，先改这里。
> 版本：5.0 ｜ 基础前缀：`/api/v1` ｜ 编码：UTF-8 ｜ 时间：ISO-8601（含时区）

---

## 0. 通用约定

### 0.1 成功响应
直接返回业务对象，**不包 `data` 壳**（与 V2/V3 保持一致，减少前端改动）。

### 0.2 错误响应（统一）
```json
{
  "ok": false,
  "error": { "code": "DATA_NOT_READY", "message": "2026-10-20 的行情尚未采集",
             "detail": { "next_action": "POST /api/v1/admin/ingest/daily_snapshot" } },
  "request_id": "..."
}
```
错误码：`DATA_NOT_READY | DATA_STALE | DATA_QUALITY | NO_TRADE_DATE | BAD_REQUEST |
UNKNOWN_ALGO | INVALID_PARAM | NOT_FOUND | PROVIDER_UNAVAILABLE | LLM_UNAVAILABLE |
DB_UNAVAILABLE | INTERNAL | BUSY`

HTTP 语义：参数错 422，资源不存在 404，数据未就绪/质量问题 409，外部源不可用 503。

### 0.3 字段命名口径（V5 统一）
- 价格：`open / high / low / close / pre_close`（不再用 `close_price`）
- 涨跌：`pct_chg`（百分数，如 `-1.614` 表示 -1.614%）
- 金额：`amount` 单位为**元**；`volume` 单位为**股**
- 比率：`turnover_rate`、`dv_ttm`、`amplitude` 均为**百分数**
- 评分：`*_score` 统一 **0–10** 分制；`composite_score` 同为 0–10
- 标签：`is_*` 一律布尔

### 0.4 数据新鲜度对象（`Freshness`）
所有"展示数据"的接口都应能附带它：
```json
{ "dataset": "stock_daily", "display_name": "个股日线", "status": "ok",
  "is_critical": true,
  "last_success_at": "2026-10-21T15:42:11+08:00",
  "last_attempt_at": "2026-10-21T15:42:11+08:00",
  "last_trade_date": "2026-10-21",
  "rows": 5382, "coverage": 0.987, "anomalies": 0, "error": null }
```
`status ∈ ok | stale | quality_failed | error | unknown`；`coverage` 为 0–1 小数。
`is_critical=true` 的数据集缺失时，`/health.data_ready` 为 false。

已知 dataset 取值：`stock_universe`、`stock_daily`、`stock_features`、`stock_scores`、
`sector_daily`、`sector_members`、`moneyflow`、**`valuation`**、`index_daily`、`margin`、
`trade_calendar`、`news`、`algo_run`、`daily_report`。

> 形状说明：`/api/v1/meta/freshness` 返回 `{items: Freshness[], network_enabled, snapshot_ready_after}`；
> 业务接口内嵌的 `freshness` 字段直接是 `Freshness[]`（数组）。前端需同时兼容数组与包裹对象。

---

## 1. 健康与元信息

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | `{status, service, version, database, latest_trade_date, data_ready, detail}` |
| GET | `/api/v1/meta/freshness` | `{items: Freshness[], network_enabled, snapshot_ready_after}` |
| GET | `/api/v1/meta/trade-calendar?start=&end=` | `{start, end, days:[{cal_date,is_open}]}` |
| GET | `/api/v1/meta/jobs?limit=20&task=&status=` | `{items: Job[]}` |
| GET | `/api/v1/meta/partitions` | `{table, partitions:[{partition,bound}]}` |
| POST | `/api/v1/admin/ingest/{task}` | 手动触发采集，需 `X-Admin-Key`；`task ∈ calendar|universe|snapshot|backfill|sector|sector_members|sector_fallback|moneyflow|valuation|index|margin|features|scores|sector_scores|report|algos|all`；重任务（backfill/all/features/scores/valuation）自动后台执行（`{status:"accepted"}`） |
| POST | `/api/v1/admin/seed-demo` | 仅 `ENV=demo` 时可用（演示数据从生产接口移出） |

`jobs` 单项：
```json
{ "job_id": "…", "task": "daily_snapshot", "dataset": "stock_daily",
  "trade_date": "2026-10-21", "status": "success", "total": 5382, "done": 5382,
  "failed": 0, "rows_written": 5382, "started_at": "…", "finished_at": "…",
  "elapsed_ms": 41234, "error": null }
```

## 2. 新闻（保留，字段与 V2 完全兼容）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/articles?page=&page_size=&source_id=` | `PaginatedResponse` |
| GET | `/api/v1/articles/{id}` | `Article` |
| GET | `/api/v1/search?q=&page=&page_size=` | `PaginatedResponse` |
| GET | `/api/v1/sources` | `Source[]`（新增，前端不再写死来源） |
| GET | `/api/v1/stats` | `{total_articles,total_sources,articles_today,processed_count}` |
| GET | `/api/v1/stats/daily-trend?days=30` | `{days,data:[{date,count}]}` |
| GET | `/api/v1/stats/source-distribution` | `{data:[{name,count,percentage}],total}` |
| GET | `/api/v1/stats/keywords?limit=20` | `{keywords:[{word,count}]}` |
| GET | `/api/v1/stats/processing-status` | `{total,processed,unprocessed,failed,percentage}` |

`Article` 字段：`id,title,url,content,author,publish_time,source_id,source_name,summary,url_hash,crawl_time,is_processed,sentiment_score`。

## 3. 行情（替代旧 `/analysis/*`）

### 3.1 GET `/api/v1/market/dashboard?trade_date=`
```json
{
  "trade_date": "2026-10-21",
  "indices": [ { "index_code": "sh000001", "index_name": "上证指数",
                 "open": 3853.6, "high": 3861.0, "low": 3808.6, "close": 3814.2,
                 "pre_close": 3876.8, "pct_chg": -1.614, "volume": 50820390500,
                 "amount": null,
                 "ret_1d": -1.614, "ret_5d": -0.895, "ret_20d": -5.479,
                 "ret_60d": -3.059, "ret_ytd": -6.836,
                 "rel_300_20d": 3.480, "rel_300_ytd": 4.533 } ],
  "us_indices": [ { "index_code": "^DJI", "index_name": "道琼斯工业", "close": 42100.5,
                    "pct_chg": 0.31, "trade_date": "2026-10-20",
                    "ret_1d": 0.31, "ret_5d": 1.2, "ret_20d": -0.8,
                    "ret_60d": 4.1, "ret_ytd": 12.3,
                    "rel_300_20d": null, "rel_300_ytd": null } ],
  "sectors": [ { "sector_code": "BK1036", "sector_name": "半导体", "sector_type": "industry",
                 "close": 1234.5, "pct_chg": 3.2, "amount": 5.1e10, "turnover_rate": 2.4,
                 "up_count": 45, "down_count": 12, "leading_stock": "中芯国际",
                 "leading_stock_pct": 5.8,
                 "technical_score": 7.8, "sentiment_score": 6.4, "news_score": 5.5,
                 "composite_score": 6.9,
                 "strength_leaders": [ {"code":"688981","name":"中芯国际","change_pct":5.8,
                                        "rank_text":"市值第1 + 日均成交第1"} ],
                 "sentiment_leaders": [ {"code":"688256","name":"寒武纪","change_pct":8.1,
                                         "rank_text":"涨幅第1 × 换手率第2"} ] } ],
  "heatmap_data": [ { "sector_code": "BK1036", "sector_name": "半导体", "pct_chg": 3.2 } ],
  "top_sectors": [ /* 同 sectors 结构，按 pct_chg 降序前 10 */ ],
  "bottom_sectors": [ /* 升序前 10 */ ],
  "margin_data": { "trade_date": "2026-10-21", "margin_balance": 16356.16,
                   "short_balance": 174.23, "margin_change": 120.5, "northward_net": null },
  "report": { "content": "## 今日大盘综述…", "is_fallback": false,
              "model": "deepseek-chat", "created_at": "…" },
  "freshness": [ /* Freshness[] */ ],
  "stats": { "scored_sectors": 30, "total_sectors": 86, "signal_count": 0 }
}
```
- 无数据时返回 200 + `sectors: []` + `freshness` 里 `status=unknown`，**不要 500**。
- `report` 为 `null` 表示尚未生成。
- `indices[]` / `us_indices[]` 的 `ret_1d/5d/20d/60d/ytd` 为**百分数**（-5.48 表示 -5.48%），按交易日窗口计算；历史长度不足该窗口时为 `null`，**不返回 0**。
- `ret_ytd` 基准是「当年首个交易日的前一交易日」收盘；若该基准不在库内（次新指数）则为 `null`。
- `rel_300_20d / rel_300_ytd` 是相对沪深300 的**超额百分点**（指数收益 − 沪深300 同周期收益），仅 A 股指数返回数值；`us_indices` 跨市场无意义，一律 `null`。

### 3.2 GET `/api/v1/market/sector/{sector_code}?trade_date=`
```json
{ "sector_code": "BK1036", "sector_name": "半导体", "sector_type": "industry",
  "trade_date": "2026-10-21",
  "market_data": { "close": 1234.5, "pct_chg": 3.2, "volume": 0, "amount": 5.1e10,
                   "turnover_rate": 2.4, "up_count": 45, "down_count": 12,
                   "leading_stock": "中芯国际", "leading_stock_pct": 5.8 },
  "scores": { "technical": 7.8, "sentiment": 6.4, "news": 5.5, "composite": 6.9,
              "detail": { "trend": 8.0, "momentum": 7.2, "volume": 7.9,
                          "margin": 5.0, "northward": 5.0, "moneyflow": 6.1,
                          "news": 5.5, "weights": { "…": "…" } } },
  "leaders": { "strength": [ … ], "sentiment": [ … ] },
  "recent_trend": [ { "date": "2026-10-17", "close": 1200.1, "pct_chg": 1.2 } ],
  "members": [ { "code": "688981", "name": "中芯国际", "pct_chg": 5.8 } ] }
```
- `members` 按当日 SCD2 名单返回，最多 200 条；`name` 取自 `dim_stock`，名称缺失时为 `null`，`pct_chg` 缺失时也为 `null`。
- 名单不完整（THS `pages > 10`，仅开放前后各 5 页）时返回已抓取的部分，不补 0；可用 3.5 的 `member_count=null` 判定。

### 3.3 GET `/api/v1/market/indices?trade_date=&days=`
- `days=1`（默认）：`{ "trade_date": "2026-10-26", "items": [ /* 与 dashboard.indices 同结构，含多周期收益与相对300字段 */ ] }`
- `days>1`：`{ "trade_date": "2026-10-26", "series": { "sh000001": [ { "trade_date": "…", "open": …, "high": …, "low": …, "close": …, "pct_chg": …, "volume": …, "amount": … } ] } }`（序列模式不附带多周期收益字段）

### 3.4 GET `/api/v1/market/temperature?trade_date=&days=`
市场温度计（V6 T3）。无 `trade_date` 取库内最新交易日；`days` 取值 1–250，默认 1。

`days=1`：
```json
{ "trade_date": "2026-10-26", "temp_score": 41.3, "confidence": "ok",
  "up_count": 2082, "down_count": 2991, "flat_count": 144, "total_count": 5217,
  "limit_up_count": 65, "limit_down_count": 34, "median_pct": -0.495,
  "above_ma20_ratio": 0.2149, "amount": 1611431825081.23,
  "amount_vs_ma20": 0.963, "index_vol_annual": 15.112,
  "components": { "available_count": 6, "total_count": 6,
                  "items": [ { "key": "up_ratio", "label": "上涨占比", "unit": "%",
                               "raw": 0.3991, "score": 34.9, "weight": 0.3,
                               "direction": 1, "available": true, "note": "…" } ] },
  "src": "engine", "updated_at": "…", "freshness": [ /* Freshness[] */ ] }
```

`days>1`：`{ "trade_date": "2026-10-26", "count": 60, "series": [ /* 每日 trade_date/temp_score/confidence/计数/比率，升序 */ ] }`

- `temp_score` 为 0–100（1 位小数）；子项缺失时存 `null` 且 `confidence` 降级，**不以 0 代替**。
- `confidence`：`ok` = 6/6 子项可用；`partial` = 4–5 项可用；`insufficient` = 关键项（上涨占比 / 站上 MA20）缺失或可用项 < 4，此时 `temp_score` 为 `null`。
- `total_count` 为有效交易家数（剔除停牌）；`above_ma20_ratio` 是 0–1 小数；`amount_vs_ma20` 是倍数；`index_vol_annual` 是百分数。
- 指定日没有温度记录时返回 `409 DATA_NOT_READY`（`detail.trade_date` 指明日期）；行情或特征尚未就绪时应先运行回填/管线，而不是当成 0 分。

### 3.5 GET `/api/v1/market/sectors?trade_date=&sector_type=`
板块列表（V6 T4 起作为 treemap 数据源正式文档化）。`sector_type` 取 `industry`（默认）/ `concept`；无 `trade_date` 取库内最新交易日。
```json
{ "trade_date": "2026-10-26", "count": 90,
  "items": [ { "sector_code": "THS_消费电子", "sector_name": "消费电子",
               "sector_type": "industry", "close": 10977.422, "pct_chg": -1.421,
               "amount": 43380581000.0, "turnover_rate": null,
               "up_count": 17, "down_count": 69,
               "leading_stock": "达瑞电子", "leading_stock_pct": 20.0,
               "market_cap": 2637834474066.67, "float_cap": 2399042204473.82,
               "main_net_inflow": -690026326.24, "rs_20d": -3.907,
               "member_count": 96, "technical_score": 4.04,
               "sentiment_score": 4.96, "news_score": 5.0, "composite_score": 4.6,
               "strength_leaders": [ … ], "sentiment_leaders": [ … ] } ] }
```
- `items` 与 3.1 `sectors` 同结构；T4 **增列** `market_cap / float_cap / main_net_inflow / rs_20d / member_count`，原字段一个不动。
- `market_cap` / `float_cap`：按当日 SCD2 名单聚合的成分股合计（元）；成分数 < 3、名单不完整或个股市值缺失时为 `null`，不补 0。
- `main_net_inflow`：成分股 `fact_moneyflow.main_net_t0` 合计（元）；与市值同样受成分数 < 3 与名单完整性限制。
- `rs_20d`：板块 20 交易日收益 − 沪深300 同窗口收益（百分点）；任一侧不足 21 个交易日为 `null`。
- `member_count`：当日 SCD2 **完整**名单成分数；`0` 表示该板块没有名单（概念板块常见）；`null` 表示名单不完整（THS 只开放前后各 5 页），此时市值/资金流同 `null`，前端按「数据不足」显示。
- `up_count` / `down_count` 在部分同花顺概念板块上恒为 `null`（源头不提供），前端显示「不适用」，**不得**按 0 渲染。

## 4. 个股（E4）

### 4.1 GET `/api/v1/stocks/{code}`
```json
{ "code": "688981", "name": "中芯国际", "exchange": "SH", "board": "科创板",
  "industry_l1": "电子", "industry_l2": "半导体", "ownership": "地方国企",
  "is_st": false, "list_date": "2020-07-16", "status": 1,
  "latest": { "trade_date": "2026-10-21", "open": 88.1, "high": 92.0, "low": 87.5,
              "close": 91.2, "pre_close": 88.0, "pct_chg": 3.636, "volume": 12345678,
              "amount": 1.1e9, "turnover_rate": 3.1, "market_cap": 7.2e11,
              "pe_ttm": 55.3, "pb": 4.1, "ps_ttm": 9.2, "dv_ttm": 0.35,
              "is_limit_up": false, "is_limit_down": false, "is_suspended": false },
  "features": { "ma5": 90.1, "ma20": 88.7, "rsi14": 61.2, "macd_dif": 0.8,
                "macd_dea": 0.6, "macd_hist": 0.2, "atr14": 3.1,
                "momentum_20": 8.4, "volatility_20": 32.1, "volume_ratio": 1.3,
                "ma_bullish": true, "macd_golden_cross": false },
  "scores": { "value": 3.1, "quality": 6.2, "momentum": 7.4, "volatility": 5.1,
              "liquidity": 8.8, "sentiment": 5.0, "composite": 5.9 },
  "sectors": [ { "sector_code": "BK1036", "sector_name": "半导体" } ],
  "freshness": { … } }

### 4.2 GET `/api/v1/stocks/{code}/kline?limit=250&start=&end=`
```json
{ "code": "688981", "adjust_basis": "raw", "count": 250,
  "items": [ { "trade_date": "2026-10-21", "open": 88.1, "high": 92.0,
    "low": 87.5, "close": 91.2, "volume": 12345678, "amount": 1.1e9,
    "pct_chg": 3.636, "ma5": 90.1, "ma20": 88.7, "ma60": 85.2,
    "boll_mid": 88.7, "boll_upper": 95.2, "boll_lower": 82.2 } ] }
```
- `adjust_basis` 固定为 `"raw"`：OHLC 为未复权原始价；MA/BOLL 也由同一组未复权收盘价计算（`features.py` 不读 `adj_factor`）。前端画 K 线与回测成交标记均按此口径，不做二次复权换算。
- `boll_mid/upper/lower` 来自 `fact_feature_daily`；特征缺失的日期返回 null（不是 0）。

### 4.3 GET `/api/v1/stocks/search?q=&limit=20`
`{ "items": [ { "code": "688981", "name": "中芯国际", "industry_l1": "电子" } ] }`

## 5. 选股（E3，兼容旧路径）

### 5.1 GET `/api/v1/algo/list`
```json
[ { "algo_code": "b1", "algo_name": "双均线动量突破", "algo_type": "trend",
    "short_desc": "…", "full_desc": "…",
    "params_schema": [ { "key": "min_vol_ratio", "label": "量比下限", "type": "number",
                         "default": 1.0, "min": 0.1, "max": 10 } ],
    "is_active": true, "sort_order": 3 } ]
```

### 5.2 GET `/api/v1/algo/{algo_code}?trade_date=&limit=20&force_refresh=false&<策略参数>`
```json
{ "algo": "b1", "algo_name": "双均线动量突破", "trade_date": "2026-10-21",
  "run": { "run_id": "9f2c…", "created_at": "2026-10-21T16:02:11+08:00",
           "elapsed_ms": 812, "universe_count": 5382, "signal_count": 20,
           "data_version": "2026-10-21:b5382", "params": { "pe_max": 50, "limit": 20 } },
  "from_cache": true, "count": 20,
  "data": [ { "code": "600519", "name": "贵州茅台", "rank": 1,
              "score": 22.3, "composite_score": 7.4,
              "close": 1520.0, "pct_chg": 1.2,
              "pe_ttm": 22.3, "pb": 8.9, "ps_ttm": 12.3, "dv_ttm": 2.15,
              "macd_dif": 0.5, "macd_dea": 0.3, "rsi14": 58.2,
              "metrics": { "pe_ttm": 22.3, "macd_golden_cross": true } } ] }
```
- **`score` 是该策略的排序指标原始值**（A3=综合分、A4=股息率、B1=ROC10、B2=RSI6、B3/B5=KDJ_J、B4=带宽分位），
  **`composite_score` 才是 0–10 的综合因子得分**（可能为 null）。前端"得分"列请用后者。
- **参数必须真生效**：不同 `pe_max` 必须产生不同 `run_id` 与不同结果。
- `data[].metrics` 为策略依据的完整指标，前端表格列由 `metrics` 动态生成。
- `run` 额外字段：`status`（running/success/failed）、失败时 `error`、回测 run 带 `is_backtest`。

**run 级市场门槛 `market_gate`（仅 B4/B5，V6 T7 补充）**

b4/b5 每次响应顶层带 `market_gate`（新 run 与缓存 run 均按 `trade_date` 现算，不落库）：
```json
{ "index_code": "000300", "available": true, "close": 4103.54, "ma20": 4275.67,
  "pct_chg": -0.8, "passed": false, "rule": "沪深300 收盘 ≥ MA20 × 0.995",
  "threshold": 4254.2916 }
```
- `passed` 为该策略对应的门槛（b4=`b4_ok`，b5=`b5_ok`）；`rule` / `threshold` 供 UI 展示。
- 指数数据缺失时 `available=false`、`passed=true`（按放行处理），`rule` 说明原因。
- 0 信号时前端据此显示“市场环境要求未满足…”，而非笼统的“没有选股信号”。

**逐条依据 `metrics.triggered`（B1–B5，V6 T7）**

每个信号附带条件树逐条求值结果，与选股共用同一份 `CONDITION_SPECS`，不另建解释表：
```json
[ { "key": "ema_cross_3d", "label": "EMA5 近 3 日上穿 EMA20", "scope": "stock",
    "required": true, "passed": true, "actual": true, "op": "eq", "threshold": true,
    "detail": "ema5=8.262 ema20=7.371" },
  { "key": "b4_market", "label": "沪深300 收盘 ≥ MA20 × 0.995", "scope": "market",
    "required": true, "passed": false, "actual": false,
    "detail": "沪深300 close=4103.54 ma20=4275.67" } ]
```
- `scope`：`stock`（个股条件）或 `market`（市场环境门槛，前端单独分组）。
- `op` / `threshold` 仅比较类条件返回；`notna` 与市场门槛没有 `threshold`，`actual` 为原始值或整体布尔。
- `detail` 为人类可读字段快照；所有值均为 JSON 原生类型（numpy / Decimal 已在后端转换）。
- 条件组（如「阳线或收涨」「趋势支撑」）折叠为一条，`actual/passed` 为组整体布尔值。
- **旧 run 兼容**：重构前的缓存 run 没有 `triggered`，前端显示「该 run 为旧版本快照，无逐条依据」，
  不得渲染成空列表或伪造全通过。升级后对最新交易日执行
  `POST /api/v1/algo/{algo_code}/refresh`（a3/a4/b1–b5 共 7 个）刷新缓存，详见 CHANGELOG 7.8。

**A3 评分拆解 `metrics.score_breakdown`（仅 A3，V6 T7）**

直接读取 `fact_stock_score_daily.detail`，不新增计算：
```json
{ "composite": 8.5,
  "weight_sum": 0.9,
  "parts": [ { "key": "value", "name": "价值", "score": 5.0, "z": 0.5,
               "weight": 0.2, "contribution": 1.1111,
               "raw": { "pe_ttm": null, "pb": null, "ps_ttm": null } } ],
  "formula": "综合分 = 10 × Σ(分位 × 权重) / Σ(当日可用权重)；贡献 = 分项得分 × 权重 / Σ(当日可用权重)",
  "sentiment_source": "neutral" }
```
- `z` 为全市场分位（0–1），`score = z × 10`；`weight` 来自评分明细 `weights`，与
  `backend/app/engine/stock_score.py` 的 `FACTOR_WEIGHTS` 顺序一致，前端不得自造权重。
- `weight_sum` 为当日有分位的因子权重之和；`contribution = score × weight / weight_sum`
  （0–10 分制），逐项贡献之和与 `composite` 一致（±0.01 四舍五入）。
- 分位缺失的因子 `z = null`、`contribution = null`，`score` 仍按中性 0.5 显示 5.0 分，
  但不计入 `weight_sum`（与 `composite_factor` 的逐行归一口径一致）。
- CSV 导出（5.3）不包含 `triggered` / `score_breakdown` 这类结构化字段。

### 5.3 其他
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/algo/{algo_code}/runs?limit=10` | `{items: RunSummary[]}`（**默认排除回测 run**，需要时加 `include_backtest=true`） |
| GET | `/api/v1/algo/runs/{run_id}` | 单次 run 明细：`{algo, algo_name, trade_date, run:{…}, data:[…]}` |
| GET | `/api/v1/algo/{algo_code}/export.csv?trade_date=&…` | `text/csv`（UTF-8 BOM；列=排名/代码/名称/综合得分(0-10)/排序分/metrics 动态列） |
| POST | `/api/v1/algo/{algo_code}/refresh` | 忽略缓存重跑（默认参数） |

`RunSummary`：`{run_id, algo_code, trade_date, created_at, elapsed_ms, universe_count, signal_count, data_version, status, is_backtest}`。

### 5.4 GET `/api/v1/algo/ladder?window=60&horizon=20&trade_date=`

策略天梯：信号级 5/10/20 日前瞻收益（V6 T5）。`window ∈ {20,60,120}`，`horizon ∈ {5,10,20}`，
超出返回 `INVALID_PARAM`；`trade_date` 省略时取库内最新统计日。

口径：**T 日收盘出信号 → T+1 交易日开盘买入**，买入价 `open × adj_factor`；H 日收益 =
自 T+1 起第 H 个交易日 `close × adj_factor / 买入价 − 1`；超额对 `sh000300` 同窗口
（指数不复权，起点 T+1 收盘、终点第 H 日收盘）。**未成熟窗口不计入统计**；
T+1 停牌/一字板（`fillable=false`）排除出胜率与均值。

```json
{ "trade_date": "2026-10-26", "window": 60, "horizon": 20,
  "benchmark": "sh000300", "window_start": "2026-07-27",
  "items": [{
    "algo_code": "b5", "algo_name": "布林下轨 × KDJ 双确认", "rank": 1,
    "sample_signals": 5, "sample_stocks": 5,
    "avg_ret": -0.49, "median_ret": -0.83, "win_rate": 0.2, "avg_excess": 5.35,
    "best": 6.08, "worst": -4.06,
    "immature": 9, "unfillable": 0, "no_data": 0, "total_signals": 14,
    "low_sample": true,
    "spark": [{ "trade_date": "2026-08-17", "win_rate": 0.2, "samples": 5 }],
    "horizons": {
      "5":  { "...": "同顶层统计字段，5 日口径" },
      "10": { "...": "同顶层统计字段，10 日口径" },
      "20": { "...": "同顶层统计字段，20 日口径" }
    }
  }],
  "immature_total": 100, "unfillable_total": 0,
  "freshness": { "dataset": "signal_perf", "status": "ok", "...": "契约 0.4" },
  "note": "信号级收益……与回测 FIFO 回合胜率不是同一口径" }
```

- `avg_ret` / `avg_excess` / `best` / `worst` 单位是**百分数**（`3.41` = +3.41%）。
- `win_rate` 为 0–1 小数，口径是 `ret_H > 0` 的信号占比，**不是回测 FIFO 回合胜率**，UI 必须标注。
- `sample_signals` = 已成熟且可成交且收益非空的样本数；`immature / unfillable / no_data`
  与有效样本互斥，`total_signals` 为区间内全部信号。
- 有效样本 < 30 时 `low_sample=true`，排序仅作参考。
- 顶层统计字段等于 `horizons[String(horizon)]`，一次请求即可渲染 5/10/20 三列。
- `spark` 为最近 20 个出信号日的胜率（`win_rate` 0–1，`samples` 当日有效样本数）。

### 5.5 GET `/api/v1/algo/consensus?trade_date=&min_count=2&limit=100`

多策略共识榜（V6 T6）：同一只票被多个策略同时命中时，按命中策略数排序。

- `min_count` 为**最少命中策略数**，只接受 `2–7`，非法返回 422（统一错误体不适用 FastAPI 参数校验）；
- `limit` 1–500，默认 100；`trade_date` 省略时取 `fact_stock_daily.max(trade_date)`（最新行情日）；
- 统计口径：当日 `stg_run.status='success'` 且 `is_backtest IS FALSE` 的**全部 run**；同一策略的多次参数 run
  合并统计，`hit_count = count(DISTINCT algo_code)`。同一策略多次 run 命中同一票只算一次，取**名次最优**
  那次 run 的 `rank/score/metrics`。只统计当前启用策略（历史 a1/a2 run 不参与）。
- **不返回跨策略平均分**：各策略 `score` 量纲不同，混算会误导；`score` 是该策略排序指标原始值。
- `metrics` 只保留每个命中策略的**前 3 个键值**（按键名排序），避免响应膨胀；
- 所有缺失值保持 `null` / `{}`，不补 0。`pct_chg` 为百分数（`1.637` = +1.637%），`composite_score` 为 0–10；
- `total_algos_evaluated` = 当日有成功非回测 run 的策略数（含 0 信号的策略），`hit_count` 不会超过它；
- 失败 run（`status='failed'`）不参与任何计数；`freshness` 对应数据集 `algo_run`。

```json
{ "trade_date": "2026-10-26", "min_count": 2, "total_algos_evaluated": 7,
  "items": [{
    "code": "601838", "name": "成都银行",
    "industry_l1": "银行", "board": "主板", "ownership": "央企",
    "hit_count": 2, "best_rank": 3,
    "pct_chg": 1.637, "composite_score": 7.1,
    "algos": [
      { "algo_code": "a4", "algo_name": "高股息 × 低估值", "rank": 3, "score": 7.5684,
        "metrics": { "close": 18.63, "composite_score": 7.1, "dv_ttm": 7.5684 } },
      { "algo_code": "a3", "algo_name": "央企 × 综合评分TOP", "rank": 45, "score": 7.1,
        "metrics": { "close": 18.63, "composite_score": 7.1, "dv_ttm": 7.5684 } }
    ]
  }],
  "freshness": { "dataset": "algo_run", "status": "ok", "...": "契约 0.4" },
  "note": "各策略评分量纲不同，榜单按命中策略数排序，不做跨策略分数平均。" }
```

- 排序：`hit_count DESC` → `best_rank ASC` → `composite_score DESC NULLS LAST` → `code ASC`。
- `best_rank` 为各命中策略名次的最小值（`null` 名次不参与）。
- 单条 SQL 完成；同一策略当日多次 run 的去重与跨策略计数均不产生 N×策略查询。

### 5.6 POST `/api/v1/algo/hits`（V6 T8）

自选股站内提醒：按提交的代码列表查询近 N 个交易日的策略命中。**只读查询，语义上等价 GET**；用 POST 是因为 codes 列表可能很长。

请求体：

```json
{ "codes": ["000739", "000560"], "days": 10 }
```

- `codes`：1–6 位数字串数组；服务端去重保序；去重后最多 100 个，超出 422（`INVALID_PARAM`）；
  任一元素非法（含空格、负号、7 位、SQL 片段等）直接 422，不静默丢弃。
- `days`：1–60，默认 10；越界由 FastAPI 参数校验返回 422。
- 窗口以**最近一个非回测成功 run 的交易日**为锚点回溯 N 个开市日（不取 `CURRENT_DATE`），
  数据未更新时仍能返回最近的可用信号。
- 统计口径：当前启用策略（`SPECS`）且 `status='success'`、`is_backtest IS FALSE` 的 run；
  **回测 run 永不参与**。单条 SQL（`stg_signal ⋈ stg_run` + `code = ANY(:codes)`），
  不会按票循环；所有值均参数绑定，无字符串拼接。
- 每条信号按 `(algo_code, trade_date, code)` 去重，只保留名次最优的一条 run。

响应：

```json
{ "items": [
    { "code": "000739", "name": "普洛药业", "algo_code": "b2", "algo_name": "RSI6 超卖回归",
      "signal_date": "2026-10-26", "rank": 2, "hit_id": "b2:2026-10-26" },
    { "code": "000560", "name": "我爱我家", "algo_code": "b3", "algo_name": "KDJ 低位金叉",
      "signal_date": "2026-10-26", "rank": 1, "hit_id": "b3:2026-10-26" }
  ],
  "latest_signal_date": "2026-10-26",
  "freshness": { "dataset": "algo_run", "status": "ok", "...": "契约 0.4" },
  "days": 10 }
```

- `hit_id = algo_code:signal_date` 是提醒去重键：同一策略同一天命中多只自选股只算**一条**新提醒；
  已读 `hit_id` 由前端写入 5.7 的服务端状态（localStorage 仅作缓存），导航栏只显示未读数量（>9 显示 `9+`）。
- `rank` 可能为 `null`；`latest_signal_date` 为 `items` 中最大日期，无命中时为 `null`。
- `items` 排序：`signal_date DESC` → `algo_code ASC` → `rank ASC NULLS LAST` → `code ASC`。

### 5.7 GET/PUT `/api/v1/watchlist`（V6 T8）

无账号体系下的**单租户**自选状态，固定一行 `app_watchlist_state(id=1)`；localStorage 只是本机缓存。

GET 响应：

```json
{ "codes": ["000560", "000739"], "seen_hits": ["b2:2026-10-26"],
  "updated_at": "2026-10-31T14:44:14.376683+00:00" }
```

PUT 请求体（两个字段均可选，只传一个时另一个保持不变）：

```json
{ "codes": ["000739"], "seen_hits": ["b2:2026-10-26"] }
```

- `codes`：校验与 5.6 一致（1–6 位数字串、去重保序、≤100），非法 422 `INVALID_PARAM`。
- `seen_hits`：每项 `algo_code:YYYY-MM-DD`（真实日历日期），去重、≤500，非法 422 `INVALID_PARAM`。
- 无版本号，last-write-wins；响应始终返回完整状态，`updated_at` 每次写入刷新。
- 前端启动时 GET 合并：本地有待同步改动则先推本地，否则以服务器为准、已读取并集，
  服务器为空而本地非空时把本地名单迁到服务器。清空站点数据、隐私模式或换浏览器（同一后端）均可恢复。
- 写入采用 400ms 防抖 + 失败标记 pending + 15 秒重试 + `online` 事件立即重试；失败不影响本机继续使用。

## 6. 回测（D4/D5）

### 6.1 POST `/api/v1/backtest/{algo_code}`
Query：`start`（默认近 1 年）、`end`、`rebalance_days=20`、`max_positions=20`、
`benchmark=sh000300`、`commission=0.00025`、`stamp_tax=0.001`、`slippage=0.001`、
以及策略参数（同 5.2）。
响应：
```json
{ "bt_id": "b7d1…", "algo": "a3", "start_date": "2025-10-21", "end_date": "2026-10-21",
  "params": { "rebalance_days": 20, "max_positions": 20, "benchmark": "sh000300",
              "commission": 0.00025, "stamp_tax": 0.001, "slippage": 0.001,
              "strategy": { "pe_max": 50 } },
  "metrics": { "total_return": 0.183, "annual_return": 0.181, "max_drawdown": -0.121,
               "sharpe": 1.42, "sortino": 1.9, "win_rate": 0.57, "profit_loss_ratio": 1.8,
               "turnover": 3.4, "excess_return": 0.052, "benchmark_return": 0.131,
               "trade_count": 42, "calmar": 1.5, "volatility": 0.21, "adjusted": true },
  "equity_curve": [ { "date": "2025-10-21", "nav": 1.0, "benchmark": 1.0 } ],
  "trades": [ { "date": "2025-10-23", "code": "600519", "name": "贵州茅台", "side": "buy",
                "price": 1512.3, "shares": 100, "amount": 151230.0, "fee": 42.8,
                "reason": "rebalance" } ],
  "created_at": "…" }
```
### 6.2 GET `/api/v1/backtest/{bt_id}` — 同上；`GET /api/v1/backtest/{bt_id}/export.csv` — 净值曲线 CSV。

### 6.3 GET `/api/v1/backtest/{bt_id}/trades?code=`
```json
{ "bt_id": "b7d1…", "algo_code": "b1", "code": "600519", "total": 42, "count": 3,
  "items": [ { "date": "2025-10-23", "code": "600519", "name": "贵州茅台", "side": "buy",
               "price": 1512.3, "shares": 100, "amount": 151230.0, "fee": 42.8,
               "reason": "rebalance" } ] }
```
- 直接读 `backtest_run.trades`，**不重新计算回测**；`code` 可省略（省略返回全部），过滤按 6 位补零后精确匹配。
- `shares=0` 的记录是被拒单（`reason ∈ limit_up/limit_down/suspended/no_data`），前端需与真实成交区分。
- 回测不存在返回 404 `NOT_FOUND`。

## 7. 前端路由

| 路由 | 页面 |
|---|---|
| `/` | 新闻首页（保留） |
| `/article/:id` | 新闻详情（保留） |
| `/dashboard` | 盘后分析（原 `/dashboard-v3`，旧新闻统计页删除） |
| `/algo-center` | 选股中心（参数/run 元信息/CSV） |
| `/stocks/:code` | 个股详情（新增） |
| `/jobs` | 任务监控（新增） |
| `/about` | 关于 |
| `*` | 404 |

## 8. 兼容性说明

- 旧 `/api/v1/analysis/*`（dashboard / run-analysis / sector / seed-demo）**已移除**，
  迁移到 `/api/v1/market/*` 与 `/api/v1/admin/*`。
- 旧 `/api/v1/stocks` 不存在，新增。
- 新闻相关接口**完全兼容**，前端新闻页无需改动字段。
