# GFNA V7 T1 · 市场状态识别与配置建议仪表盘

> 版本：2026-11-12
> 输入规格：`C:\Users\21468\Downloads\行情仪表盘_市场阶段与配置提示词.md`
> 执行方式：一次一项、测试先行、单独提交、默认不 push；数据库订正先预览后授权
> 交付位置：`/dashboard`（盘后分析页）顶部 Hero 区

---

## 1. 目标与已确认决策

把「市场阶段判断 + 持仓配置建议 + 一周市场温度计」三块内容做成 `盘后分析` 页顶部的
Hero 仪表盘，面向非专业投资者；每块都能展开查看 5 条因子依据。

已与用户确认的决策：

| 决策项 | 结论 |
|---|---|
| 生成方式 | 后端确定性规则引擎，落库 + API 返回严格 JSON；不使用运行时 LLM 生成结论 |
| 缺失因子 | **补数据源**：10Y/1Y 国债收益率（中债）、融资买入额（沪深两市） |
| 温度计口径 | 按提示词第 6 节新公式重建，回补历史，替换现有 6 子项口径 |
| 页面布局 | 三张卡置顶替代旧 `MarketTemperature`；旧 6 子项明细不再展示 |
| 主宽基 | 沪深 300 的 OHLC 做趋势/波动因子；全 A 个股聚合做广度/估值/情绪 |
| 配置板块 | 固定类型模板 + 按当日相对强度动态补充 1–2 个真实行业 |
| 阈值校准 | 用 2023-09 ~ 2026-11 历史做一次回看校准，校准结果写入常量与报告 |
| 历史回看 | 跟随页面日期选择器重算；无数据日期显示「数据积累中」 |
| 两个分数 | 把握度（confidence）与乐观度（optimism）都展示 |
| 两融回补 | 近 3 年（约 750 个交易日），带断点与限速 |
| 五档标签 | 全局改为「冰点 / 清冷 / 平稳 / 温热 / 沸腾」，边界 0-19/20-39/40-59/60-79/80-100 |

---

## 2. 数据基线（2026-11-12 实测）

| 数据 | 覆盖 | 结论 |
|---|---|---|
| `fact_stock_daily` | 2021-01-04 ~ 2026-11-06；2021 年仅 56 只/日、2022 年 ~1486 只/日、2023 年起 ~4800 只/日、2024 年起 ~5270 只/日 | **2022-09 起可用于广度**；750 日窗口到 **2024-09** 才完整 |
| `market_indices` | 7 个指数完整 OHLC；沪深300 自 2002 年 | 趋势/波动因子用 `sh000300` |
| `fact_feature_daily` | 2021-01-04 ~ 2026-11-06 | 现有 MA20/60/120，但**无 MA30**，需从日线自算 |
| `market_temperature_daily` | 2025-10-09 起 263 天 | 旧口径，需按新公式整体回补 |
| `margin_trading` | 2026-10-20 起 13 天，仅余额；北向全空 | 需补 `margin_buy_amount` 并回补 3 年 |
| `fact_moneyflow` | 北向持股比例全空 | R5 北向因子不可用，缺失处理 |
| 估值 | PE/PB/股息率覆盖 ~5500 只 | ERP、破净率、股息率可算 |
| 行业 | `dim_stock.industry_l1` 127 类；`fact_sector_daily` 90 个 THS 行业 | 板块角色映射与集中度可算 |
| 国债收益率 | 中债国债曲线 1Y/10Y，`ak.bond_china_yield` 实测可用 | 新增 `macro_rates` 表 |
| 两融买入额 | SSE 区间接口、SZSE 单日接口均含融资买入额 | SSE 批量 + SZSE 逐日回补 |

**不可用因子**：北向净流入、偏股基金新发、独立情绪指数、MA30 现成列。
前两项按缺失处理并计入 `completeness`；情绪指数用现有子项合成替代（见 4.6）。

---

## 3. 数据模型

### 3.1 新表 `macro_rates`

| 列 | 类型 | 说明 |
|---|---|---|
| `trade_date` | DATE PK | 交易日 |
| `cn_1y` | NUMERIC(8,4) | 1 年期国债收益率 % |
| `cn_10y` | NUMERIC(8,4) | 10 年期国债收益率 % |
| `term_spread` | NUMERIC(8,4) | 10Y-1Y，百分点 |
| `src` | VARCHAR(16) | `chinabond` |
| `updated_at` | TIMESTAMPTZ | |

### 3.2 新表 `fact_market_daily`

每日一行，存**原始因子**（未标准化），供分位计算、证据展示与回放。

字段组：
- 指数：`index_code, close, ret_1d, ret_20, ema20, ema60, ema120, ema_slope60, ma_alignment, adx14, di_plus, di_minus, efficiency_ratio, choppiness, macd_hist, rsi14, price_vs_ma120`
- 量能：`amount, amount_ma20, amount_vs_ma20, market_turnover, turnover_ma5, volume_price_match, industry_heat_concentration, capital_efficiency`
- 广度：`up_count, down_count, flat_count, total_count, limit_up_cnt, limit_down_cnt, adv_dec_ratio, above_ma30_ratio, new_high_60_ratio, broken_net_ratio`
- 估值避险：`dividend_yield, earnings_yield, cn_1y, cn_10y, erp, bond_equity_gap, term_spread, defensive_rel_momentum, margin_buy_amount, margin_buy_ratio`
- 波动：`rv20, rv60, natr14, boll_bandwidth, max_drawdown_3m`
- 元数据：`data_quality, src, updated_at`

### 3.3 新表 `market_regime_daily`

每日一行，存**标准化结果 + 状态机输出 + 配置建议**。

| 列 | 类型 | 说明 |
|---|---|---|
| `trade_date` | DATE PK | |
| `regime_code` | VARCHAR(16) | `bottoming/trend_up/risk_off/repair_up/pull_down` |
| `regime_label` | VARCHAR(16) | 中文 |
| `regime_since` | DATE | 本状态起始日 |
| `regime_days` | INT | 已持续交易日 |
| `note` | TEXT | 补充说明 |
| `headline` | VARCHAR(120) | 一句话白话结论 |
| `confidence_score` | SMALLINT | 0–100 |
| `confidence_level` | VARCHAR(8) | 低/中/高 |
| `optimism_score` | SMALLINT | 0–100 |
| `optimism_level` | VARCHAR(8) | 谨慎/偏谨慎/中性/偏乐观/乐观 |
| `group_t/m/b/v/q/r` | NUMERIC(6,4) | 五组得分（V 为 `v_q` 0–1，其余 -1~1） |
| `factors` | JSONB | 每个因子的 `raw/pct/x/group` |
| `allocation` | JSONB | 三档配置、vs_yesterday、rebalance_note |
| `evidence` | JSONB | Top5 因子依据 |
| `completeness` | NUMERIC(5,4) | 有效因子 / 总因子 |
| `data_quality` | VARCHAR(16) | `full/partial/accumulating` |
| `stale_fields` | JSONB | 缺失因子中文名 |
| `src`, `updated_at` | | |

### 3.4 扩展 `margin_trading`

新增 `margin_buy_amount NUMERIC(24,2)`（元，沪深合计）。可空，不重写历史已有行。

### 3.5 迁移

新增 `backend/alembic/versions/20261112_0011_market_regime.py`：
- `create_table macro_rates`
- `create_table fact_market_daily`
- `create_table market_regime_daily`
- `add_column margin_trading.margin_buy_amount`
- 全部幂等（先 `inspect().has_table/has_column`）
- `downgrade` 只删新增对象，不动旧表数据

---

## 4. 因子计算口径

### 4.1 标准化（所有因子统一）

- 滚动窗口 750 个交易日，`min_periods=250`；不足 250 日 → 该日不输出状态。
- 窗口内先做 1% / 99% 缩尾，再算当日值的百分位 `p ∈ [0,1]`。
- `x = 2p - 1`；逆向因子取 `x = -(2p-1)`。
- 缺失 → `x=0`，并降低 `completeness`。
- 2023-09 ~ 2024-09 使用扩展窗口（250–750），`data_quality=partial`；
  2024-09 之后为完整 750 日窗口，`data_quality=full`。

### 4.2 T 层（沪深300）

| 因子 | 口径 | 方向 |
|---|---|---|
| T1 `ema_slope60` | EMA60 的 20 日线性回归斜率 / close | 正 |
| T2 `ma_alignment` | close>EMA20>EMA60=+1，反向=-1，其余 0 | 正 |
| T3 `adx14` | Wilder ADX14；标准化后乘 `sign(+DI - -DI)` | 正 |
| T4 `efficiency_ratio` | `abs(close_t-close_{t-20}) / sum(abs(diff),20)` | 正 |
| T5 `choppiness` | `100*log10(sum(TR,14)/(max(H,14)-min(L,14)))/log10(14)` | 逆向 |
| T6 `price_vs_ma120` | `close/MA120 - 1` | 正 |

`T = 0.28*T1 + 0.22*T2 + 0.20*T3 + 0.14*T4 + 0.08*T5 + 0.08*T6`
（T5 已按逆向标准化，故权重为正）

### 4.3 M / B 层（短期动量与广度）

| 因子 | 口径 |
|---|---|
| M1 `macd_hist` | 沪深300 MACD(12,26,9) 柱体 |
| M2 `rsi14` | 沪深300 RSI14 |
| M3 `ret20` | 沪深300 20 日收益 |
| M4 `above_ma30_ratio` | 全 A 收盘价 > 自身 MA30 的占比（MA30 从 `fact_stock_daily.close` 自算） |
| M5 `new_high_60_ratio` | 收盘价创 60 日新高（含当日）的个股占比 |
| M6 `adv_dec_ratio` | 上涨家数 / (上涨+下跌) |
| M7 `limit_ratio` | (涨停-跌停)/(涨停+跌停+1) |

`M = 0.30*M1 + 0.20*M2 + 0.25*M3 + 0.25*M4`
`B = 0.30*M4 + 0.25*M5 + 0.25*M6 + 0.20*M7`

### 4.4 V 层（波动风险）

`pV1..pV5` 为原始百分位（未翻转）：
- V1 `rv20`：沪深300 20 日年化波动
- V2 `rv60`：沪深300 60 日年化波动
- V3 `natr14`：ATR14/close×100
- V4 `boll_bandwidth`：(上轨-下轨)/中轨×100
- V5 `max_drawdown_3m`：近 60 交易日最大回撤

`V_q = clip(0.35*pV1 + 0.25*pV2 + 0.20*pV3 + 0.10*pV4 + 0.10*pV5, 0, 1)`

### 4.5 Q 层（量能）

| 因子 | 口径 | 方向 |
|---|---|---|
| Q1 `amount_vs_ma20` | 全市场成交额 / 20 日均额 | 正 |
| Q2 `turnover_ma5` | 市场换手率（Σ成交额/Σ流通市值）5 日均值 | 正 |
| Q3 `volume_price_match` | 近 10 日“涨放量/跌缩量”天数占比 | 正 |
| Q4 `industry_heat_concentration` | 成交额前 5 行业占比（按 `dim_stock.industry_l1`） | 逆向 |
| Q5 `capital_efficiency` | `pct_chg/(amount/1e12)` 的 20 日均值 | 正 |
| Q6 `margin_buy_ratio` | 沪深融资买入额 / 全市场成交额 | 正 |
| Q7 `fund_issue_amt` | 缺失 | — |

`Q = 0.25*Q1 + 0.15*Q2 + 0.15*Q3 + 0.10*(-Q4) + 0.15*Q5 + 0.10*Q6 + 0.10*Q7`
（Q7 缺失时组内权重重归一）

### 4.6 R 层（风险偏好与避险）

| 因子 | 口径 | 方向 |
|---|---|---|
| R1 `erp` | 全 A 流通市值加权盈利收益率 - 10Y 国债 | 正 |
| R2 `bond_equity_gap` | 全 A 流通市值加权股息率 - 10Y 国债 | 正 |
| R3 `term_spread` | 10Y-1Y | 正 |
| R4 `defensive_rel_momentum` | 防御行业篮子 20 日收益 - 全 A 20 日收益 | 逆向 |
| R5 `northbound_net` | 缺失 | — |
| R6 `broken_net_ratio` | PB<1 占比 | 双向（极高为筑底证据） |
| R7 `sentiment_index` | 用 Q1/Q2/Q6 + M4 + V1 + R1 的 z 分数合成替代 | 正 |

`R = 0.20*R1 + 0.10*R2 + 0.10*R3 + 0.20*R4 + 0.15*R5 + 0.10*R6 + 0.15*R7`
（缺失项 R5 组内重归一）

防御行业白名单（`industry_l1`）：银行、电力、煤炭、公路铁路运输、贵金属、保险、白酒、食品加工制造。

### 4.7 温度计（新公式）

```
temp_score = 100 * (
  0.20*turnover_pct + 0.20*amount_pct + 0.15*limit_up_pct
+ 0.15*margin_buy_pct + 0.10*new_high_60_pct + 0.10*rsi14_norm
+ 0.10*(1-rv20_pct) )
```

- `turnover_pct`：市场换手率的 3 年滚动分位
- `amount_pct`：全市场成交额分位
- `limit_up_pct`：涨停家数分位
- `margin_buy_pct`：融资买入占比分位（缺失时组内重归一）
- `new_high_60_pct`：60 日新高占比分位
- `rsi14_norm = clip((RSI14-30)/40, 0, 1)`
- `rv20_pct`：20 日年化波动分位
- 结果四舍五入到整数
- 可用子项 <4 或 amount/turnover 缺失 → `insufficient`，总分为 null

五档：0–19 冰点(1)、20–39 清冷(2)、40–59 平稳(3)、60–79 温热(4)、80–100 沸腾(5)。

一周序列：最近 5 个交易日；`week_avg/max/min`、`week_slope`（线性回归斜率）、
`week_trend`（>+1.5 升温 / <-1.5 降温 / 其余走平）、`emotion_conclusion`、
`extreme_note`（仅沸腾/冰点给中性提示）。

---

## 5. 状态机

### 5.1 五态与优先级

自上而下命中即停：

1. `risk_off`：`(R<-0.5 且 V_q>0.70 且 M<-0.20)` 或 `(T<-0.50 且 max_drawdown_3m 分位>0.80)`
2. `trend_up`：`T>0.33 且 M>0.20 且 B>0.50 且 Q>0.30 且 Q3>0`；`V_q>0.90` 时 note 标注“上攻但波动过大”
3. `bottoming`：`abs(T)<=0.33 且 V_q<0.35 且 Q1分位<0.25` 且以下至少 2 项：
   ① 近 10 日低点抬高 ② MACD 底背离 ③ 破净率分位>0.85 ④ 情绪指数由恐慌回升（替代项）
   ⑤ 均线由空头转纠缠；若同时 `M<-0.30` → `pull_down`
4. `repair_up`：`abs(T)<=0.33 且 M>0.15 且 T<=0`；或 `T>0` 但未满足 trend_up 全部条件
5. `pull_down`：`abs(T)<=0.33 且 M<-0.15`；或 `Q5 分位<0.20 且 Q4 分位>0.70 且较 5 日前抬升`
6. 兜底：取 `abs(M)` 更接近的一档（repair_up / pull_down），note “方向未明，以观察为主”

### 5.2 迟滞

- 新状态需连续 3 个交易日满足才切换
- 维持阈值放宽 20%（正阈值 ×0.8；负阈值绝对值 ×0.8）
- 最短驻留 5 个交易日，`risk_off` 允许立即切换
- 输出 `since` 与 `days`

### 5.3 把握度

```
score_state = Σ(权重 × clip((因子值-阈值)/尺度, 0, 1))
margin      = score_top1 - score_top2
consistency = 与主状态方向一致的组数 / 5
completeness= 有效因子数 / 总因子数
confidence  = round(100 * sigmoid(2.2*margin) * (0.6+0.4*consistency) * (0.7+0.3*completeness))
```

档位：<45 低 / 45–70 中 / >70 高。

### 5.4 乐观度

```
optimism = clip(50 + 38*(0.30*T + 0.30*M + 0.20*B + 0.20*R) - 12*(V_q-0.5), 0, 100)
```

再按状态修正：`trend_up +12`、`repair_up +5`、`bottoming 0`、`pull_down -8`、`risk_off -18`，clip 到 [0,100]。
档位：0–20 谨慎 / 21–40 偏谨慎 / 41–60 中性 / 61–80 偏乐观 / 81–100 乐观。

---

## 6. 配置建议

### 6.1 基准区间

| 状态 | 防御 | 进攻 | 均衡 |
|---|---|---|---|
| trend_up | 10–20 | 55–70 | 20–30 |
| repair_up | 25–35 | 30–40 | 30–40 |
| bottoming | 30–40 | 15–25 | 40–50 |
| pull_down | 45–60 | 10–20 | 25–35 |
| risk_off | 65–80 | 0–10 | 15–25 |

### 6.2 微调

1. 取区间中值；进攻档 `±8pct × (optimism-50)/50`，从防御/均衡按中值比例回补
2. 三档合计 100%，取 5% 整数倍
3. 单档相对上一交易日变化 ≤15pct；超出截断并写入 `rebalance_note`
4. `confidence<45` 时向均衡档偏移 5–10pct，并标注“把握偏低，建议偏向均衡”
5. 输出 `vs_yesterday`

### 6.3 板块

- 固定模板：
  - 防御：红利低波、银行、公用事业
  - 进攻：科技成长、半导体、券商
  - 均衡：宽基指数、中盘质量、医药
- 动态补充：从角色白名单中按 20 日相对强度取前 1–2 个真实 THS 行业，去重后补入
- 每档 2–4 个；只输出类型/行业名，禁止个股、代码、目标价

动态白名单：
- 防御：银行、电力、煤炭、公路铁路运输、贵金属、保险、白酒、食品加工制造
- 进攻：半导体、软件开发、IT服务、通信设备、电池、光伏设备、证券、军工电子、消费电子
- 均衡：白色家电、化学制药、中药、医疗器械、工业金属、汽车零部件、食品加工制造

---

## 7. API

### `GET /api/v1/market/overview?trade_date=YYYY-MM-DD`

返回文档第 7 节 JSON（`meta/regime/allocation/thermometer/evidence/caveats`），
外加 `freshness`。`trade_date` 缺省为最新有 `market_regime_daily` 的交易日。

- 无该日数据：404；数据积累中：200 + `meta.data_quality="accumulating"`，
  `regime` 与 `allocation` 为 null，`thermometer` 可用则返回
- 现有 `/market/temperature` 保留不动（诊断/兼容），前端不再使用

---

## 8. 前端

1. 新建 `MarketOverview.tsx` 容器 + `RegimeCard.tsx` / `AllocationCard.tsx` / `ThermometerCard.tsx`
2. 三卡置顶：桌面 3 列（阶段 1.2fr / 配置 1fr / 温度 1fr），移动端堆叠
3. 每卡右上角「为什么这么判断」展开 evidence 5 条（因子中文名、当日值、历史分位、方向、白话）
4. 阶段卡：状态色 + 大标签 + headline + 把握度环 + 乐观度条 + since/days
5. 配置卡：100% 堆叠条 + 三档明细（pct、sectors chips、role、reason）+ vs_yesterday + rebalance_note
6. 温度卡：仪表 + 5 日柱/线 + 五档刻度带 + 当前档高亮 + 周均值/斜率 + 情绪结论 + extreme_note
7. `TEMPERATURE_BANDS` 全局改名与边界调整
8. 新增状态色 token（深浅两套）：
   - trend_up 暖红、repair_up 浅橙、bottoming 浅蓝、pull_down 灰蓝、risk_off 深灰绿
9. `disclaimer` 常驻，不折叠

---

## 9. 回补与校准

1. `scripts/backfill_macro.py --dataset bond`：2022-09-01 → 今，按年分段拉中债收益率
2. `scripts/backfill_macro.py --dataset margin_buy`：近 750 交易日；SSE 区间 + SZSE 逐日；
   checkpoint `backend/var/margin_buy_checkpoint.json`；限速 0.5–1s
3. `scripts/backfill_market_factors.py`：2022-09-01 → 今，批量算 `fact_market_daily`
4. `scripts/backfill_regime.py`：算标准化 + 状态机 + 配置，写 `market_regime_daily`
5. `scripts/backfill_temperature.py`：按新 7 子项口径重建 `market_temperature_daily`
6. `scripts/calibrate_regime.py`：输出状态分布、平均持续、年切换次数、温度分布；
   支持 `--set key=value` 与 `--grid`
7. 校准目标：五态均出现、平均持续 15–40 日、年切换 ≤16 次、单一状态 ≤60% 天数
8. 校准结果与前后对比见 `docs/evidence/v7_regime_calibration.md`

**校准结论（2026-11-12）**：五态齐全，最大单态 55.4%，年切换 10.3 次；
调整 `trend_m/b/q`、`bottom_t_abs/v/q1` 五个阈值，并新增
`T<-0.50 且 V_q>0.60 且 M<-0.30 → risk_off` 兜底规则以覆盖
「估值便宜但趋势崩」的 2024-01 类行情。

---

## 10. 流水线

`DEFAULT_DAILY_STEPS` 调整为：

```
... "margin", "valuation", "features", "scores", "sector_scores",
"macro_rates", "market_factors", "temperature", "regime", "report", ...
```

- `macro_rates`：每日拉最近 5 个自然日，幂等 upsert
- `market_factors`：算当日原始因子
- `temperature`：按新公式算当日温度
- `regime`：算标准化与状态机；依赖前一日状态
- 新增 dataset：`macro_rates`、`market_factors`、`market_regime`
- 更新 `TASK_DATASET` 与 `services/freshness.py` 的 `DATASET_META`

---

## 11. 测试与验收

### 11.1 后端单测

- 标准化：缩尾、分位、方向、缺失中性、窗口不足
- 分组权重：完整/缺项重归一；三组权重和为 1
- 状态机：五态优先级、迟滞 3 日、最短驻留 5 日、risk_off 立即切换、兜底
- confidence / optimism 边界与档位
- 配置：合计 100、5% 倍数、日变化 ≤15、低把握偏均衡
- 温度：公式、缺失重归一、五档、周斜率、情绪结论
- API 契约：字段齐全、历史日期、accumulating、404
- 迁移幂等

### 11.2 数据验收

- `fact_market_daily` 最新日无未来函数：所有滚动窗口只含 T 日及以前
- 最新日：`sum(pct)=100`、`abs(change)<=15`、温度序列 5 天
- 与当日实际行情抽查：涨停家数、成交额、MA30 占比误差在合理范围

### 11.3 前端验收

- `npm run build` 通过
- 真实浏览器打开 `/dashboard`：
  - 三卡渲染、展开 evidence、切换历史日期、刷新
  - 深浅主题、375/1440 视口
  - 控制台无错误、无 NaN/undefined
  - 请求数符合预期

### 11.4 回归

- 后端全量 `pytest`（现有 479 passed 基线不得下降）
- 旧 `/market/temperature` 保持可访问
- `/dashboard` 其它区块不回归

---

## 12. 风险与回滚

| 风险 | 处置 |
|---|---|
| 中债接口临时不可用 | 每日采集失败不阻断管线；`macro_rates` 沿用最近一期并标 stale |
| SZSE 回补慢/限频 | checkpoint + 限速 + 后台运行；失败重跑跳过已完成日 |
| 2022-09 前数据稀疏 | 状态仅从 2023-09 输出；更早日期显示“数据积累中” |
| 校准后仍频繁切换 | 提高迟滞天数或缩短窗口；以校准报告为准，不改公式 |
| 新标签影响旧组件 | 全局替换 `TEMPERATURE_BANDS`，同步旧卡删除 |
| 迁移失败 | 迁移只增不删；`downgrade` 只删新增对象 |

回滚顺序：前端先回退 `DashboardPage` 引用 → 后端回退 `regime` 步骤 →
迁移 `alembic downgrade 0010`（新表数据可保留，不影响旧功能）。

---

## 13. 实施顺序

1. 迁移 + 模型 + 常量
2. 采集：国债收益率、融资买入额（脚本 + 单测）
3. `fact_market_daily` 因子引擎（批量 + 单日 + 单测）
4. 状态机 + 配置引擎 + 校准（单测 + 校准报告）
5. 温度计新口径 + 周统计（单测）
6. API + 契约测试
7. 前端三卡 + 标签改名 + 构建
8. 全量回补 + 校准 + 数据验收
9. 浏览器验收 + 全量回归 + 文档

---

## 14. 实施记录（2026-11-12）

### 14.1 已完成

| 项 | 结果 |
|---|---|
| 迁移 | `0011_market_regime`（3 表 + 1 列）、`0012_market_factor_ohlc`（high/low）已应用 |
| 国债收益率 | 2022-09-01 ~ 2026-11-11，1045 行 |
| 融资买入额 | 2023-10-09 ~ 2026-11-05，747 个交易日（5 天 SZSE 网络失败，可重跑） |
| 市场原始因子 | 2022-09-01 ~ 2026-11-06，1011 行 |
| 状态机 | 2023-09-11 ~ 2026-11-06，762 行 |
| 温度计 | 2023-09-11 ~ 2026-11-06，768 行 |
| 后端测试 | 577 passed / 65 skipped |
| 前端构建 | `npm run build` 通过 |
| 浏览器验收 | 三卡渲染、三个「为什么」展开、历史日期切换、深浅主题、无 NaN/undefined、无 console error |
| 数据对账 | 涨停/跌停/成交额/新高占比与 SQL 复算一致；配置合计 100%、5% 整数倍、日变化 ≤15 |

### 14.2 与提示词的偏差（已校准）

1. **主宽基**：万得全A 不可得，改用沪深300 OHLC + 全A 个股聚合作广度。
2. **缺失因子**：北向、基金新发无数据源；ERP/期限利差已补中债；融资买入额已补沪深两市。
3. **震荡筑底阈值**：`bottom_v` 0.35→0.80、`bottom_q1` 0.25→0.45、`bottom_t_abs` 0.33→0.45，
   否则 3 年只有 32 天满足、状态永远不出现。
4. **避险下跌**：新增 `T<-0.50 且 V_q>0.60 且 M<-0.30` 兜底条件，
   覆盖 2024-01「估值便宜但趋势崩」的行情（原规则要求 R<-0.5，ERP 高时永不触发）。
5. **修复/调整分界**：中期略偏正但短期动量为负时按「向下调整」而非「向上修复」，
   避免把走弱误标成修复。
6. **配置板块**：固定模板 + 按 20 日相对强度动态补充 1 个真实 THS 行业（跨档去重）。

### 14.3 已知边界

- `fact_stock_daily` 2021–2022 年广度样本稀疏，2022-09 之前的日期不输出状态。
- 750 日完整窗口到 2024-09 才满足；2023-09 ~ 2024-09 为扩展窗口，`data_quality=partial`。
- 融资买入额缺失 5 个交易日（SZSE 连接被重置），重跑 `backfill_macro.py --dataset margin_buy` 可补齐。
- 温度计 `median_pct` 不再写入（新公式不需要），旧字段保留为空。
