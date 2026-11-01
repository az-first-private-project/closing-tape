/**
 * 回测 Hook（契约第 6 节）
 */
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  createBacktestGrid,
  fetchBacktest,
  fetchBacktestGrid,
  fetchBacktestTrades,
  runBacktest,
} from '../api/backtest'
import type { BacktestGridRequest, BacktestOptions } from '../types/backtest'

export interface BacktestVariables {
  algoCode: string
  options: BacktestOptions
}

export interface BacktestGridVariables {
  algoCode: string
  request: BacktestGridRequest
}

/** 回测是用户显式点击触发的操作，使用 useMutation 而非 query */
export function useBacktest() {
  return useMutation({
    mutationFn: (variables: BacktestVariables) => runBacktest(variables.algoCode, variables.options),
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
