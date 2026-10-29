/**
 * 回测相关类型 —— 严格对齐 docs/V5-API-CONTRACT.md 第 6 节
 * 注意：指标均为小数比率（0.183 = 18.3%），前端展示时再乘 100。
 */
import type { AlgoParamValue, AlgoParams } from './algo'

/** 回测参数快照（6.1 params） */
export interface BacktestParams {
  rebalance_days: number | null
  max_positions: number | null
  benchmark: string | null
  commission: number | null
  stamp_tax: number | null
  slippage: number | null
  /** 策略参数快照 */
  strategy: Record<string, AlgoParamValue>
}

/** 绩效指标（6.1 metrics） */
export interface BacktestMetrics {
  /** 总收益率（小数） */
  total_return: number | null
  annual_return: number | null
  max_drawdown: number | null
  sharpe: number | null
  sortino: number | null
  win_rate: number | null
  profit_loss_ratio: number | null
  turnover: number | null
  excess_return: number | null
  benchmark_return: number | null
  trade_count: number | null
  calmar: number | null
}

/** 净值曲线点（6.1 equity_curve） */
export interface EquityPoint {
  date: string
  nav: number | null
  benchmark: number | null
}

/** 成交明细（6.1 trades） */
export interface BacktestTrade {
  date: string
  code: string
  name: string
  side: 'buy' | 'sell' | string
  price: number | null
  shares: number | null
  amount: number | null
  fee: number | null
  reason: string | null
}

/** GET /api/v1/backtest/{bt_id}/trades 响应（6.3） */
export interface BacktestTradesResponse {
  bt_id: string
  algo_code: string
  /** 请求指定的股票代码（未指定时为 null） */
  code: string | null
  /** 该回测全部成交条数（过滤前） */
  total: number
  count: number
  items: BacktestTrade[]
}

/** POST /api/v1/backtest/{algo_code} 响应（6.1） */
export interface BacktestResult {
  bt_id: string
  algo: string
  start_date: string
  end_date: string
  params: BacktestParams
  metrics: BacktestMetrics
  equity_curve: EquityPoint[]
  trades: BacktestTrade[]
  created_at: string | null
}

/** 前端发起回测的入参（全部走 query string） */
export interface BacktestOptions {
  start?: string
  end?: string
  rebalance_days?: number
  max_positions?: number
  benchmark?: string
  commission?: number
  stamp_tax?: number
  slippage?: number
  /** 策略参数（同 5.2，平铺进 query string） */
  params?: AlgoParams
}
