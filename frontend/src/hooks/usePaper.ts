/**
 * 纸面组合 Hook（V6 T11）
 */
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  fetchPaperDetail,
  fetchPaperPortfolios,
  syncPaperPortfolio,
} from '../api/paper'

/** 组合列表；同步进行中时加速轮询，页面隐藏时停止 */
export function usePaperPortfolios(polling = false) {
  return useQuery({
    queryKey: ['paper-portfolios'],
    queryFn: fetchPaperPortfolios,
    refetchInterval: polling ? 3000 : false,
    refetchIntervalInBackground: false,
    staleTime: 30 * 1000,
  })
}

/** 单组合明细；同步进行中时加速轮询 */
export function usePaperDetail(algoCode: string | null, days = 250, polling = false) {
  return useQuery({
    queryKey: ['paper-detail', algoCode, days],
    queryFn: () => fetchPaperDetail(algoCode as string, days),
    enabled: !!algoCode,
    refetchInterval: polling ? 3000 : false,
    refetchIntervalInBackground: false,
    staleTime: 30 * 1000,
    retry: false,
  })
}

/** 手动重算（后台任务） */
export function usePaperSync() {
  return useMutation({
    mutationFn: (algoCode: string) => syncPaperPortfolio(algoCode),
  })
}
