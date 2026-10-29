/**
 * 市场温度 Hook（V6 T3）
 * =======================
 * 与 useDashboard 同一节奏：最新交易日视图每 10 分钟刷新，回看历史时不轮询。
 * 单日详情与 60 日序列分开请求，避免 days>1 接口承担两种返回结构。
 */
import { useQuery } from '@tanstack/react-query'
import { fetchTemperature, fetchTemperatureSeries } from '../api/market'
import type { MarketTemperature, TemperatureSeries } from '../types/market'

/** 单日温度（含六个子项） */
export function useTemperature(tradeDate?: string) {
  return useQuery<MarketTemperature>({
    queryKey: ['temperature', tradeDate || 'latest'],
    queryFn: () => fetchTemperature(tradeDate),
    staleTime: 5 * 60 * 1000,
    refetchInterval: tradeDate ? false : 10 * 60 * 1000,
    // 409 DATA_NOT_READY 是业务态（尚未生成），重试没有意义
    retry: false,
  })
}

/** 近 N 个交易日温度序列（sparkline） */
export function useTemperatureSeries(days = 60, tradeDate?: string) {
  return useQuery<TemperatureSeries>({
    queryKey: ['temperature-series', days, tradeDate || 'latest'],
    queryFn: () => fetchTemperatureSeries(days, tradeDate),
    staleTime: 5 * 60 * 1000,
    refetchInterval: tradeDate ? false : 10 * 60 * 1000,
    retry: false,
  })
}
