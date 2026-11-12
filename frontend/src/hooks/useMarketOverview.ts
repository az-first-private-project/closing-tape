/**
 * 市场状态仪表盘 Hook（V7 T1）
 * ==============================
 * 最新交易日每 10 分钟刷新；回看历史时不轮询。
 */
import { useQuery } from '@tanstack/react-query'
import { fetchMarketOverview } from '../api/market'
import type { MarketOverview } from '../types/market'

export function useMarketOverview(tradeDate?: string) {
  return useQuery<MarketOverview>({
    queryKey: ['market-overview', tradeDate || 'latest'],
    queryFn: () => fetchMarketOverview(tradeDate),
    staleTime: 5 * 60 * 1000,
    refetchInterval: tradeDate ? false : 10 * 60 * 1000,
    retry: false,
  })
}
