/**
 * 纸面组合 API（V6 T11）
 */
import { request } from './client'
import type {
  PaperDetail,
  PaperPortfoliosResponse,
  PaperSyncResponse,
} from '../types/paper'

/** 组合列表（含未初始化的启用策略） */
export function fetchPaperPortfolios(): Promise<PaperPortfoliosResponse> {
  return request<PaperPortfoliosResponse>('/paper/portfolios')
}

/** 单组合明细：净值序列 + 持仓 + 明日待执行 + 与回测差值 */
export function fetchPaperDetail(algoCode: string, days = 250): Promise<PaperDetail> {
  return request<PaperDetail>(`/paper/portfolio/${encodeURIComponent(algoCode)}`, {
    query: { days },
  })
}

/** 手动触发完整区间重算（后台执行，立即返回 accepted） */
export function syncPaperPortfolio(algoCode: string): Promise<PaperSyncResponse> {
  return request<PaperSyncResponse>(
    `/paper/portfolio/${encodeURIComponent(algoCode)}/sync`,
    { method: 'POST' },
  )
}
