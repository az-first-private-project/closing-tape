/**
 * 回测 Hook（契约第 6 节）
 */
import { useMutation, useQuery } from '@tanstack/react-query'
import { fetchBacktestTrades, runBacktest } from '../api/backtest'
import type { BacktestOptions } from '../types/backtest'

export interface BacktestVariables {
  algoCode: string
  options: BacktestOptions
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
