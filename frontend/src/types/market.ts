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
  /** 0–10 分制；未评分时为 null（不得用 5 兜底） */
  technical_score: number | null
  sentiment_score: number | null
  news_score: number | null
  composite_score: number | null
  /** 后端在无评分/无龙头时可能返回 null，前端统一按空数组处理 */
  strength_leaders: LeaderStock[] | null
  sentiment_leaders: LeaderStock[] | null
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
  name: string
  pct_chg: number | null
}

/** GET /api/v1/market/sector/{sector_code} 响应（3.2） */
export interface SectorDetail {
  sector_code: string
  sector_name: string
  sector_type: string | null
  trade_date: string
  market_data: SectorMarketData
  scores: SectorScores
  leaders: {
    strength: LeaderStock[]
    sentiment: LeaderStock[]
  }
  recent_trend: SectorTrendPoint[]
  members: SectorMember[]
}
