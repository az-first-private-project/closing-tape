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

### 5.3 其他
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/algo/{algo_code}/runs?limit=10` | `{items: RunSummary[]}`（**默认排除回测 run**，需要时加 `include_backtest=true`） |
| GET | `/api/v1/algo/runs/{run_id}` | 单次 run 明细：`{algo, algo_name, trade_date, run:{…}, data:[…]}` |
| GET | `/api/v1/algo/{algo_code}/export.csv?trade_date=&…` | `text/csv`（UTF-8 BOM；列=排名/代码/名称/综合得分(0-10)/排序分/metrics 动态列） |
| POST | `/api/v1/algo/{algo_code}/refresh` | 忽略缓存重跑（默认参数） |

`RunSummary`：`{run_id, algo_code, trade_date, created_at, elapsed_ms, universe_count, signal_count, data_version, status, is_backtest}`。

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
