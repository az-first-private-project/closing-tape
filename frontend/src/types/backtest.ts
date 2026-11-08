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

/** 月度收益（T9，百分数：4.2 = 4.2%） */
export interface MonthlyReturn {
  ym: string
  ret: number | null
  benchmark: number | null
  excess: number | null
}

/** 回撤曲线点（T9，drawdown 为百分数且 ≤0） */
export interface DrawdownPoint {
  date: string
  nav: number | null
  peak: number | null
  drawdown: number
}

/** 20 日滚动超额点（T9，百分数；样本不足为 null） */
export interface RollingExcessPoint {
  date: string
  value: number | null
}

/** 行业权重（T9，weight 为百分数；现金也作为一项显式返回） */
export interface IndustryWeight {
  name: string
  weight: number
  value: number
  count: number
}

/** 每个调仓日的持仓行业分布（T9） */
export interface IndustrySnapshot {
  date: string
  total_value: number
  cash: number
  cash_weight: number
  stock_count: number
  other_count: number
  industries: IndustryWeight[]
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

/** POST /api/v1/backtest/{algo_code} 响应（6.1，异步任务） */
export interface BacktestJobResponse {
  bt_id: string
  algo: string
  /** 创建即入队；执行状态由 GET /backtest/{bt_id} 获取 */
  status: 'queued' | 'running' | 'success' | 'failed' | string
  start_date: string
  end_date: string
  requested_start: string
  effective_start: string
  start_trimmed: boolean
  poll: string
}

/** GET /api/v1/backtest/{bt_id} 轮询用状态（执行中字段可能为空） */
export interface BacktestStatusResponse {
  bt_id: string
  status: 'queued' | 'running' | 'success' | 'failed' | string
  error: string | null
  elapsed_ms?: number | null
}

/** GET /api/v1/backtest/{bt_id} 响应（6.2） */
export interface BacktestResult {
  bt_id: string
  algo: string
  start_date: string
  end_date: string
  params: BacktestParams
  metrics: BacktestMetrics
  equity_curve: EquityPoint[]
  trades: BacktestTrade[]
  status: 'queued' | 'running' | 'success' | 'failed' | string
  error: string | null
  created_at: string | null
  /** T9 派生视图是否可用（旧记录曲线/成交为空时为 false） */
  extras_available: boolean
  monthly_returns: MonthlyReturn[]
  drawdown_curve: DrawdownPoint[]
  rolling_excess_20d: RollingExcessPoint[]
  industry_exposure: IndustrySnapshot[]
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

/** 参数敏感性矩阵：轴（T10） */
export interface BacktestGridAxes {
  rebalance_days: number[]
  max_positions: number[]
}

/** 矩阵每格指标摘要（T10，比率为小数） */
export interface BacktestGridMetrics {
  total_return: number | null
  excess_return: number | null
  max_drawdown: number | null
  sharpe: number | null
  win_rate: number | null
  trade_count: number | null
}

/** POST 建矩阵时返回的格（后台尚未执行） */
export interface BacktestGridCellBrief {
  bt_id: string
  params: { rebalance_days: number; max_positions: number }
  rebalance_days: number
  max_positions: number
  status: 'queued' | 'success' | string
  reused: boolean
}

/** GET 矩阵详情时的格（带指标、耗时与对齐标记） */
export interface BacktestGridCell extends Omit<BacktestGridCellBrief, 'status'> {
  status: 'queued' | 'running' | 'success' | 'failed' | string
  metrics: BacktestGridMetrics | null
  elapsed_ms: number | null
  error: string | null
  /** 与矩阵主区间/日历一致；false 的格会从热力图剔除 */
  aligned: boolean
}

/** POST /api/v1/backtest/{algo_code}/grid 入参（T10） */
export interface BacktestGridRequest {
  start_date: string
  end_date: string
  rebalance_days: number[]
  max_positions: number[]
  /** 策略参数快照，与单次回测同构 */
  strategy?: AlgoParams
}

/** POST /api/v1/backtest/{algo_code}/grid 响应（T10） */
export interface BacktestGridCreateResponse {
  grid_id: string
  algo: string
  start_date: string
  end_date: string
  requested_start: string
  effective_start: string
  start_trimmed: boolean
  axes: BacktestGridAxes
  cells: BacktestGridCellBrief[]
  total: number
  reused: number
  queued: number
  /** 建矩阵本身不跑回测，固定为 0 */
  elapsed_ms: number
}

/** POST /api/v1/backtest/grid/{grid_id}/retry 响应（P1-4） */
export interface BacktestGridRetryResponse {
  grid_id: string
  algo: string
  retried: number
  queued: number
  /** 重试只入队，不跑回测，固定为 0 */
  elapsed_ms: number
}

/** GET /api/v1/backtest/grid/{grid_id} 响应（T10） */
export interface BacktestGridDetail {
  grid_id: string
  algo: string
  start_date: string
  end_date: string
  axes: BacktestGridAxes
  cells: BacktestGridCell[]
  progress: { done: number; total: number; failed: number }
  total_elapsed_ms: number
  created_at: string | null
}
