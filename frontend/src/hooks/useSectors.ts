/**
 * 板块列表 / 板块详情 Hook（V6 T4）
 * =================================
 * treemap 需要独立的板块列表（含市值/资金流/相对强度），与仪表盘 sectors 分开请求；
 * 切换 industry/concept 时 queryKey 变化，不复用上一维度的缓存，避免旧格子残留。
 */
import { useQuery } from '@tanstack/react-query'
import { fetchSectorDetail, fetchSectors } from '../api/market'
import type { SectorDetail, SectorList, SectorType } from '../types/market'

/** 板块列表（按交易日 + 板块类型） */
export function useSectors(tradeDate?: string, sectorType: SectorType = 'industry') {
  return useQuery<SectorList>({
    queryKey: ['sectors', tradeDate || 'latest', sectorType],
    queryFn: () => fetchSectors(tradeDate, sectorType),
    staleTime: 5 * 60 * 1000,
    refetchInterval: tradeDate ? false : 10 * 60 * 1000,
  })
}

/** 板块详情（treemap 点击后的内嵌抽屉） */
export function useSectorDetail(code: string | null, tradeDate?: string) {
  return useQuery<SectorDetail>({
    queryKey: ['sector-detail', code, tradeDate || 'latest'],
    queryFn: () => fetchSectorDetail(code as string, tradeDate),
    enabled: Boolean(code),
    staleTime: 5 * 60 * 1000,
    // 板块不存在 / 历史无数据属于业务态，重试没有意义
    retry: false,
  })
}
