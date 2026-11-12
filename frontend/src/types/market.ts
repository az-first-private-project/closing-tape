/**
 * 行情相关类型 —— 严格对齐 docs/V5-API-CONTRACT.md 第 3 节
 * 字段口径：价格 open/high/low/close/pre_close；涨跌 pct_chg（百分数）；金额 amount（元）；成交量 volume（股）。
 */
import type { Freshness } from './common'

/** 指数行情（3.1 indices） */
export interface IndexQuote {
  index_code: string
  index_name: string
  open: number | null
  high: number | null
  low: number | null
  close: number | null
  pre_close: number | null
  /** 百分数，如 -1.614 表示 -1.614% */
  pct_chg: number | null
  volume: number | null
  /** 单位：元 */
  amount: number | null
  /** 多周期收益（百分数，如 -5.48 表示 -5.48%）；历史不足为 null */
  ret_1d?: number | null
  ret_5d?: number | null
  ret_20d?: number | null
  ret_60d?: number | null
  ret_ytd?: number | null
  /** 相对沪深300 的超额（百分点）；基准自身为 0，美股为 null */
  rel_300_20d?: number | null
  rel_300_ytd?: number | null
}

/** 指数日线序列点（T14：GET /market/indices?days>1） */
export interface IndexSeriesPoint {
  trade_date: string
  open: number | null
  high: number | null
  low: number | null
  close: number | null
  pct_chg: number | null
  volume: number | null
  amount: number | null
}

/** GET /api/v1/market/indices?days>1 */
export interface IndexSeriesResponse {
  trade_date: string | null
  series: Record<string, IndexSeriesPoint[]>
}

/** 美股指数（3.1 us_indices，结构较 A 股指数精简） */
export interface UsIndexQuote {
  index_code: string
  index_name: string
  close: number | null
  pct_chg: number | null
  /** 美股交易日（T-1，与 A 股 trade_date 不同） */
  trade_date: string | null
  /** 绝对收益（百分数）；跨市场不提供相对300 */
  ret_1d?: number | null
  ret_5d?: number | null
  ret_20d?: number | null
  ret_60d?: number | null
  ret_ytd?: number | null
  rel_300_20d?: number | null
  rel_300_ytd?: number | null
}

/** 龙头股（板块内强势/情绪龙头） */
export interface LeaderStock {
  code: string
  name: string
  change_pct: number | null
  rank_text: string | null
}

/** 板块行情 + 评分（3.1 sectors / top_sectors / bottom_sectors） */
export interface SectorQuote {
  sector_code: string
  sector_name: string
  sector_type: string | null
  close: number | null
  pct_chg: number | null
  /** 单位：元 */
  amount: number | null
  /** 百分数 */
  turnover_rate: number | null
  up_count: number | null
  down_count: number | null
  leading_stock: string | null
  leading_stock_pct: number | null
  /** 成分股总市值合计（元，T4；缺失为 null） */
  market_cap: number | null
  /** 成分股流通市值合计（元，T4） */
  float_cap: number | null
  /** 成分股主力净流入合计（元，T4） */
  main_net_inflow: number | null
  /** 20 日相对沪深300 超额收益（百分点，T4） */
  rs_20d: number | null
  /** 当日 SCD2 名单成分数（T4；0 表示无名单；partial 名单为 null） */
  member_count: number | null
  /** 名单是否只有 partial 样本（THS pages>10 只暴露前后各 5 页） */
  is_partial: boolean
  /** partial 名单的样本成员数；完整名单为 null */
  partial_member_count: number | null
  /** 0–10 分制；未评分时为 null（不得用 5 兜底） */
  technical_score: number | null
  sentiment_score: number | null
  news_score: number | null
  composite_score: number | null
  /** 后端在无评分/无龙头时可能返回 null，前端统一按空数组处理 */
  strength_leaders: LeaderStock[] | null
  sentiment_leaders: LeaderStock[] | null
}

export type SectorType = 'industry' | 'concept'

/** 板块评分序列（P3-2 评分榜 sparkline） */
export interface SectorScoreSparklineItem {
  sector_code: string
  /** 与响应 dates 等长；当日无评分为 null，不用 0 补齐 */
  scores: (number | null)[]
}

/** GET /api/v1/market/sector-scores/sparkline */
export interface SectorScoreSparkline {
  end: string | null
  days: number
  /** 库内实际存在的评分日，升序 */
  dates: string[]
  items: SectorScoreSparklineItem[]
}

/** GET /api/v1/market/sectors 响应（T4） */
export interface SectorList {
  trade_date: string | null
  count: number
  items: SectorQuote[]
}

/** 热力图数据项（3.1 heatmap_data） */
export interface HeatmapItem {
  sector_code: string
  sector_name: string
  pct_chg: number | null
}

/** 融资融券概览（3.1 margin_data；数值单位：亿元） */
export interface MarginData {
  trade_date: string | null
  margin_balance: number | null
  short_balance: number | null
  margin_change: number | null
  /** 北向资金：若监管暂停披露则恒为 null */
  northward_net: number | null
}

/** AI 日报（3.1 report；为 null 表示尚未生成） */
export interface DailyReport {
  content: string
  is_fallback: boolean
  model: string | null
  created_at: string | null
}

/** 仪表盘统计（3.1 stats） */
export interface DashboardStats {
  scored_sectors: number
  total_sectors: number
  signal_count: number
}

/** GET /api/v1/market/dashboard 响应（3.1） */
export interface DashboardData {
  trade_date: string
  indices: IndexQuote[]
  us_indices: UsIndexQuote[]
  sectors: SectorQuote[]
  heatmap_data: HeatmapItem[]
  top_sectors: SectorQuote[]
  bottom_sectors: SectorQuote[]
  margin_data: MarginData | null
  report: DailyReport | null
  freshness: Freshness[]
  stats: DashboardStats
  /** 后端无数据时附带的原因说明 */
  empty_reason?: string
}

/** 板块行情明细（3.2 market_data） */
export interface SectorMarketData {
  close: number | null
  pct_chg: number | null
  volume: number | null
  amount: number | null
  turnover_rate: number | null
  up_count: number | null
  down_count: number | null
  leading_stock: string | null
  leading_stock_pct: number | null
}

/** 板块评分明细（3.2 scores） */
export interface SectorScores {
  technical: number | null
  sentiment: number | null
  news: number | null
  composite: number | null
  detail: {
    trend: number | null
    momentum: number | null
    volume: number | null
    margin: number | null
    northward: number | null
    forum: number | null
    moneyflow: number | null
  }
}

/** 板块近期走势点（3.2 recent_trend） */
export interface SectorTrendPoint {
  date: string
  close: number | null
  pct_chg: number | null
}

/** 板块成分股（3.2 members） */
export interface SectorMember {
  code: string
  /** 3.2 接口部分板块成员只返回 code，前端回退显示代码 */
  name?: string | null
  pct_chg: number | null
}

/** GET /api/v1/market/sector/{sector_code} 响应（3.2） */
export interface SectorDetail {
  sector_code: string
  sector_name: string
  sector_type: string | null
  /** 名单是否只有 partial 样本 */
  is_partial: boolean
  /** partial 名单的样本成员数；完整名单为 null */
  partial_member_count: number | null
  trade_date: string
  market_data: SectorMarketData
  scores: SectorScores
  leaders: {
    strength: LeaderStock[]
    sentiment: LeaderStock[]
  }
  recent_trend: SectorTrendPoint[]
  /** 最多返回 200 条；完整数量见 members_total_matching */
  members: SectorMember[]
  /** 该日有效成分的全量命中数（C1） */
  members_total_matching?: number | null
}

/** 市场温度子项（T3；raw 单位见 unit） */
export interface TemperatureComponent {
  key: string
  label: string
  /** "%" / "家" / "倍" */
  unit: string
  raw: number | null
  /** 0–100 归一分；null 表示该项缺数据 */
  score: number | null
  weight: number
  /** 1=升温项，-1=降温项（仅用于标注，得分已反转） */
  direction: number
  available: boolean
  note: string
}

export interface TemperatureComponents {
  available_count: number
  total_count: number
  items: TemperatureComponent[]
}

export type TemperatureConfidence = 'ok' | 'partial' | 'insufficient'

// ============================================================
// 市场状态与配置仪表盘（V7 T1 / GET /market/overview）
// ============================================================

export type RegimeCode =
  | 'trend_up'
  | 'repair_up'
  | 'bottoming'
  | 'pull_down'
  | 'risk_off'

export interface MarketOverviewMeta {
  trade_date: string
  index_used: string
  data_quality: 'full' | 'partial' | 'accumulating'
  stale_fields: string[]
  is_historical: boolean
  disclaimer: string
}

export interface RegimeScore {
  score: number
  level: string
  plain: string
}

export interface MarketRegimeBlock {
  code: RegimeCode
  label: string
  headline: string
  confidence: RegimeScore
  optimism: RegimeScore
  since: string
  days: number
  note: string
}

export type AllocationKey = 'defensive' | 'offensive' | 'balanced'

export interface AllocationBucket {
  key: AllocationKey
  label: string
  pct: number
  sectors: string[]
  role: string
  reason: string
}

export interface MarketAllocation {
  total: number
  buckets: AllocationBucket[]
  vs_yesterday: Partial<Record<AllocationKey, number>>
  rebalance_note: string
  confidence_caveat: string
}

export interface ThermometerPoint {
  date: string
  temp: number
  label: string
  /** 1–5 档 */
  score: number
}

export interface MarketThermometer {
  current_temp: number | null
  current_label: string
  current_score: number | null
  series: ThermometerPoint[]
  week_avg: number | null
  week_max: number | null
  week_min: number | null
  week_slope: number | null
  week_trend: string
  emotion_conclusion: string
  extreme_note: string
  components?: TemperatureComponents | null
  confidence?: TemperatureConfidence
}

export interface MarketEvidence {
  code: string
  name: string
  value: string
  percentile: number | null
  direction: string
  plain: string
}

export interface MarketOverview {
  meta: MarketOverviewMeta
  regime: MarketRegimeBlock | null
  allocation: MarketAllocation | null
  thermometer: MarketThermometer
  evidence: MarketEvidence[]
  caveats: string[]
  freshness: Freshness[]
}
