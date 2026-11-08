/**
 * 个股相关类型 —— 严格对齐 docs/V5-API-CONTRACT.md 第 4 节
 * 注意：价格字段是 close 而非 close_price；涨跌是 pct_chg 而非 change_pct。
 */
import type { Freshness } from './common'

/** 最新日行情（4.1 latest） */
export interface StockLatest {
  trade_date: string | null
  open: number | null
  high: number | null
  low: number | null
  close: number | null
  pre_close: number | null
  /** 百分数 */
  pct_chg: number | null
  volume: number | null
  /** 单位：元 */
  amount: number | null
  turnover_rate: number | null
  market_cap: number | null
  pe_ttm: number | null
  pb: number | null
  ps_ttm: number | null
  dv_ttm: number | null
  is_limit_up: boolean | null
  is_limit_down: boolean | null
  is_suspended: boolean | null
}

/** 技术指标（4.1 features） */
export interface StockFeatures {
  ma5: number | null
  ma20: number | null
  rsi14: number | null
  macd_dif: number | null
  macd_dea: number | null
  macd_hist: number | null
  atr14: number | null
  momentum_20: number | null
  volatility_20: number | null
  volume_ratio: number | null
  ma_bullish: boolean | null
  macd_golden_cross: boolean | null
}

/** 因子评分（4.1 scores，统一 0–10 分制） */
export interface StockScores {
  value: number | null
  quality: number | null
  momentum: number | null
  volatility: number | null
  liquidity: number | null
  sentiment: number | null
  composite: number | null
}

/** 所属板块（4.1 sectors） */
export interface StockSectorRef {
  sector_code: string
  sector_name: string
}

/** GET /api/v1/stocks/{code} 响应（4.1） */
export interface StockProfile {
  code: string
  name: string
  exchange: string
  board: string
  industry_l1: string | null
  industry_l2: string | null
  ownership: string | null
  is_st: boolean
  list_date: string | null
  status: number | null
  /** 解析后的交易日（后端额外字段） */
  trade_date?: string | null
  latest: StockLatest | null
  features: StockFeatures | null
  scores: StockScores | null
  sectors: StockSectorRef[]
  /** C1：当日 SCD2 全量归属数；`sectors` 最多返回 20 条 */
  sectors_total_matching?: number | null
  /**
   * 后端实际返回 Freshness[]，契约 4.1 写的是单个对象；
   * 前端两种形态都兼容（见 StockDetailPage 的归一化）。
   */
  freshness: Freshness[] | Freshness | null
}

/** K 线单点（4.2） */
export interface KlineItem {
  trade_date: string
  open: number | null
  high: number | null
  low: number | null
  close: number | null
  volume: number | null
  amount: number | null
  pct_chg: number | null
  ma5: number | null
  ma20: number | null
  ma60: number | null
  boll_mid: number | null
  boll_upper: number | null
  boll_lower: number | null
}

/** GET /api/v1/stocks/{code}/kline 响应（4.2） */
export interface KlineResponse {
  code: string
  /** 复权口径：K 线与 MA/BOLL 均为未复权原始价 */
  adjust_basis?: 'raw' | 'hfq'
  count?: number
  items: KlineItem[]
}

/** 批量走势项（P2-5）：closes 为未复权收盘，时间升序，可能不足 days 个 */
export interface SparklineBatchItem {
  code: string
  closes: (number | null)[]
}

/** GET /api/v1/stocks/sparkline/batch 响应（P2-5） */
export interface SparklineBatchResponse {
  end: string | null
  days: number
  adjust_basis?: 'raw' | 'hfq'
  items: SparklineBatchItem[]
}

/** 股票搜索结果项（4.3） */
export interface StockSearchItem {
  code: string
  name: string
  industry_l1: string | null
}

/** GET /api/v1/stocks/search 响应（4.3） */
export interface StockSearchResponse {
  items: StockSearchItem[]
}
