/**
 * 牛熊位置 × 估值 Hook（V7 T4）
 */
import { useQuery } from '@tanstack/react-query'
import { fetchBullBearValuation } from '../api/market'
import type { BroadValuation } from '../types/market'

export function useBullBearValuation() {
  return useQuery<BroadValuation>({
    queryKey: ['bull-bear-valuation'],
    queryFn: fetchBullBearValuation,
    staleTime: 5 * 60 * 1000,
    refetchInterval: 10 * 60 * 1000,
    retry: false,
  })
}
