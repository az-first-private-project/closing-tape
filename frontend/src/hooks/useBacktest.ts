/**
 * 回测 Hook（契约第 6 节）
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError } from '../api/client'
import {
  createBacktestGrid,
  fetchBacktest,
  fetchBacktestGrid,
  fetchBacktestStatus,
  fetchBacktestTrades,
  retryBacktestGrid,
  runBacktest,
} from '../api/backtest'
import type { BacktestGridRequest, BacktestOptions, BacktestResult } from '../types/backtest'

export interface BacktestVariables {
  algoCode: string
  options: BacktestOptions
}

export interface BacktestGridVariables {
  algoCode: string
  request: BacktestGridRequest
}

/** 轮询间隔与前端等待上限；后端任务不受 30s HTTP 超时约束 */
const POLL_INTERVAL_MS = 1500
const POLL_TIMEOUT_MS = 10 * 60 * 1000

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/** 等待后台回测结束：queued/running 时轮询，成功后再取完整结果。 */
async function waitBacktest(btId: string): Promise<BacktestResult> {
  const deadline = Date.now() + POLL_TIMEOUT_MS
  for (;;) {
    const status = await fetchBacktestStatus(btId)
    if (status.status === 'success') return fetchBacktest(btId)
    if (status.status === 'failed') {
      throw new ApiError({
        code: 'BACKTEST_FAILED',
        status: 200,
        message: status.error || '回测执行失败',
      })
    }
    if (Date.now() >= deadline) {
      throw new ApiError({
        code: 'BACKTEST_TIMEOUT',
        status: 0,
        message: '前端等待回测超时（10 分钟），后台任务可能仍在运行',
      })
    }
    await sleep(POLL_INTERVAL_MS)
  }
}

/** 回测是用户显式点击触发的操作，使用 useMutation 而非 query */
export function useBacktest() {
  return useMutation({
    mutationFn: async (variables: BacktestVariables) => {
      const job = await runBacktest(variables.algoCode, variables.options)
      return waitBacktest(job.bt_id)
    },
  })
}

/** 回测成交明细（6.3）；btId 为空时不发请求 */
export function useBacktestTrades(btId: string, code?: string) {
  return useQuery({
    queryKey: ['backtest-trades', btId, code || 'all'],
    queryFn: () => fetchBacktestTrades(btId, code),
    enabled: !!btId,
    staleTime: 5 * 60 * 1000,
  })
}

/** 已落库回测的完整结果（矩阵格点击复用 T9 面板） */
export function useBacktestDetail(btId: string | null) {
  return useQuery({
    queryKey: ['backtest', btId],
    queryFn: () => fetchBacktest(btId as string),
    enabled: !!btId,
    staleTime: 5 * 60 * 1000,
  })
}

/** 建立参数敏感性矩阵（T10）：用户显式触发 */
export function useBacktestGrid() {
  return useMutation({
    mutationFn: (variables: BacktestGridVariables) =>
      createBacktestGrid(variables.algoCode, variables.request),
  })
}

/** 重试矩阵失败格（P1-4）：成功格复用，仅 failed 重新入队；完成后立即刷新矩阵 */
export function useBacktestGridRetry(gridId: string | null) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => retryBacktestGrid(gridId as string),
    onSuccess: () => {
      if (gridId) {
        void queryClient.invalidateQueries({ queryKey: ['backtest-grid', gridId] })
      }
    },
  })
}

/** 矩阵轮询（T10）：3s 一次，页面不可见、标签页隐藏或全部完成时自动停止 */
export function useBacktestGridDetail(gridId: string | null, active = true) {
  return useQuery({
    queryKey: ['backtest-grid', gridId],
    queryFn: () => fetchBacktestGrid(gridId as string),
    enabled: !!gridId && active,
    refetchInterval: (query) => {
      const data = query.state.data
      if (data && data.progress.done >= data.progress.total) return false
      return 3000
    },
    refetchIntervalInBackground: false,
  })
}
