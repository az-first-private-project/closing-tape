/**
 * 回测 API（契约第 6 节）
 */
import { downloadCsv, request } from './client'
import type {
  BacktestGridCreateResponse,
  BacktestGridDetail,
  BacktestGridRequest,
  BacktestGridRetryResponse,
  BacktestJobResponse,
  BacktestOptions,
  BacktestResult,
  BacktestStatusResponse,
  BacktestTradesResponse,
} from '../types/backtest'

/**
 * 创建回测任务（6.1 POST /backtest/{algo_code}）
 * 立即返回 bt_id 与 queued 状态，实际计算在后台；用 fetchBacktestStatus 轮询。
 * 全部参数走 query string：基础参数 + 策略参数（同 5.2）。
 */
export function runBacktest(algoCode: string, options: BacktestOptions = {}): Promise<BacktestJobResponse> {
  const { params, ...baseOptions } = options
  return request<BacktestJobResponse>(`/backtest/${encodeURIComponent(algoCode)}`, {
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

/** 轮询回测任务状态（6.2）：只取状态与错误，成功后再取完整结果 */
export function fetchBacktestStatus(btId: string): Promise<BacktestStatusResponse> {
  return request<BacktestStatusResponse>(`/backtest/${encodeURIComponent(btId)}`)
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
