/**
 * 回测 API（契约第 6 节）
 */
import { downloadCsv, request } from './client'
import type {
  BacktestGridCreateResponse,
  BacktestGridDetail,
  BacktestGridRequest,
  BacktestGridRetryResponse,
  BacktestOptions,
  BacktestResult,
  BacktestTradesResponse,
} from '../types/backtest'

/**
 * 运行回测（6.1 POST /backtest/{algo_code}）
 * 全部参数走 query string：基础参数 + 策略参数（同 5.2）。
 */
export function runBacktest(algoCode: string, options: BacktestOptions = {}): Promise<BacktestResult> {
  const { params, ...baseOptions } = options
  return request<BacktestResult>(`/backtest/${encodeURIComponent(algoCode)}`, {
    method: 'POST',
    query: {
      start: baseOptions.start || undefined,
      end: baseOptions.end || undefined,
      rebalance_days: baseOptions.rebalance_days,
      max_positions: baseOptions.max_positions,
      benchmark: baseOptions.benchmark,
      commission: baseOptions.commission,
      stamp_tax: baseOptions.stamp_tax,
      slippage: baseOptions.slippage,
      ...(params ?? {}),
    },
  })
}

/** 按 bt_id 查询回测结果（6.2） */
export function fetchBacktest(btId: string): Promise<BacktestResult> {
  return request<BacktestResult>(`/backtest/${encodeURIComponent(btId)}`)
}

/** 回测成交明细（6.3），可按股票代码过滤 */
export function fetchBacktestTrades(
  btId: string,
  code?: string,
): Promise<BacktestTradesResponse> {
  return request<BacktestTradesResponse>(`/backtest/${encodeURIComponent(btId)}/trades`, {
    query: { code: code || undefined },
  })
}

/** 导出净值曲线 CSV（6.2） */
export function downloadBacktestCsv(btId: string): Promise<void> {
  return downloadCsv(`/backtest/${encodeURIComponent(btId)}/export.csv`, undefined, `backtest_${btId}.csv`)
}

/** 建立参数敏感性矩阵（T10）；后台逐格执行，立即返回 grid_id */
export function createBacktestGrid(
  algoCode: string,
  payload: BacktestGridRequest,
): Promise<BacktestGridCreateResponse> {
  return request<BacktestGridCreateResponse>(
    `/backtest/${encodeURIComponent(algoCode)}/grid`,
    { method: 'POST', body: payload },
  )
}

/** 查询矩阵状态与每格指标（T10） */
export function fetchBacktestGrid(gridId: string): Promise<BacktestGridDetail> {
  return request<BacktestGridDetail>(`/backtest/grid/${encodeURIComponent(gridId)}`)
}

/** 重试矩阵内失败格（P1-4）：只重置 failed，成功/运行中的格不受影响 */
export function retryBacktestGrid(gridId: string): Promise<BacktestGridRetryResponse> {
  return request<BacktestGridRetryResponse>(
    `/backtest/grid/${encodeURIComponent(gridId)}/retry`,
    { method: 'POST' },
  )
}
