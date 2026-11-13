/**
 * 指数对比 / 风格轮动 Hook（V7 T3）
 * ================================
 * 最新共同交易日每 10 分钟刷新；指定历史日期时不轮询。
 */
import { useQuery } from '@tanstack/react-query'
import { fetchIndexCompare } from '../api/market'
import type { IndexCompareResult } from '../types/market'

export function useIndexCompare(tradeDate?: string) {
  return useQuery<IndexCompareResult>({
    queryKey: ['index-compare', tradeDate || 'latest'],
    queryFn: () => fetchIndexCompare(tradeDate),
    staleTime: 5 * 60 * 1000,
    refetchInterval: tradeDate ? false : 10 * 60 * 1000,
    retry: false,
  })
}
