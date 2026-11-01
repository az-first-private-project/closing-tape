/**
 * 纸面组合类型（V6 T11）
 * 收益/回撤/差值均为小数比率（0.0912 = 9.12%），展示时乘 100。
 */

/** 纸面组合数据新鲜度 */
export interface PaperFreshness {
  latest_trade_date: string | null
  market_trade_date: string | null
  lag_days: number | null
  stale: boolean
  updated_at: string | null
  ready: boolean
}

/** 与同区间最近一次人工回测的差值（paper - backtest，小数） */
export interface PaperVsBacktest {
  bt_id: string
  total_return: number | null
  excess_return: number | null
  max_drawdown: number | null
  total_return_diff: number | null
  excess_return_diff: number | null
  max_drawdown_diff: number | null
  as_of: string
}

export interface PaperSummary {
  nav: number | null
  benchmark_nav: number | null
  total_return: number | null
  benchmark_return: number | null
  excess_return: number | null
  max_drawdown: number | null
}

/** pending=未初始化；waiting=建仓日尚无下一交易日；active=正常；failed=同步失败 */
export type PaperStatus = 'pending' | 'waiting' | 'active' | 'failed'

export interface PaperPortfolioCard extends PaperSummary {
  portfolio_id: string | null
  algo_code: string
  name: string
  status: PaperStatus
  start_date: string | null
  tracking_days: number
  latest_trade_date: string | null
  rebalance_days: number | null
  max_positions: number | null
  initial_capital: number
  vs_backtest: PaperVsBacktest | null
  freshness: PaperFreshness
}

export interface PaperPortfoliosResponse {
  items: PaperPortfolioCard[]
  total: number
}

export interface PaperNavPoint {
  date: string
  nav: number | null
  benchmark: number | null
  drawdown: number | null
}

export interface PaperHolding {
  code: string
  name: string
  shares: number
  /** 占总资产比例（0–1） */
  weight: number
  /** 每股成本（含手续费） */
  cost: number
  /** 每股现价（后复权口径） */
  price: number
  /** 浮动盈亏比例（0–1） */
  pnl_pct: number
}

export interface PaperPendingOrder {
  side: 'buy' | 'sell'
  code: string
  name: string
  reason: string
  /** true=可成交；false=不可成交；null=最新一天尚未有 T+1 行情 */
  fillable: boolean | null
  block_reason: string | null
}

export interface PaperDetail {
  portfolio_id: string
  algo_code: string
  name: string
  status: PaperStatus
  start_date: string | null
  tracking_days: number
  params: {
    rebalance_days: number | null
    max_positions: number | null
    initial_capital: number
    strategy: Record<string, string | number | boolean | null>
  }
  caliber: string
  series: PaperNavPoint[]
  holdings: PaperHolding[]
  cash_ratio: number | null
  pending_orders: PaperPendingOrder[]
  vs_backtest: PaperVsBacktest | null
  summary: PaperSummary
  freshness: PaperFreshness
}

export interface PaperSyncResponse {
  status: 'accepted'
  algo: string
  mode: 'background'
  hint: string
}
