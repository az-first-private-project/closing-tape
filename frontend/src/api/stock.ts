/**
 * 个股 API（契约第 4 节）
 */
import { request } from './client'
import type {
  KlineResponse,
  SparklineBatchResponse,
  StockProfile,
  StockSearchResponse,
} from '../types/stock'

/** 个股概览（4.1）：基础信息 + 行情 + 技术指标 + 因子评分 + 所属板块 */
export function fetchStock(code: string, tradeDate?: string): Promise<StockProfile> {
  return request<StockProfile>(`/stocks/${encodeURIComponent(code)}`, {
    query: { trade_date: tradeDate || undefined },
  })
}

/** 个股 K 线（4.2），默认最近 250 个交易日 */
export function fetchKline(
  code: string,
  options: { limit?: number; start?: string; end?: string } = {},
): Promise<KlineResponse> {
  return request<KlineResponse>(`/stocks/${encodeURIComponent(code)}/kline`, {
    query: {
      limit: options.limit ?? 250,
      start: options.start || undefined,
      end: options.end || undefined,
    },
  })
}

/** 批量近 N 日收盘（P2-5），一次请求覆盖整页 sparkline */
export function fetchSparklineBatch(
  codes: string[],
  options: { days?: number; end?: string } = {},
): Promise<SparklineBatchResponse> {
  return request<SparklineBatchResponse>('/stocks/sparkline/batch', {
    query: {
      codes: codes.join(','),
      days: options.days ?? 30,
      end: options.end || undefined,
    },
  })
}

/** 个股搜索（4.3）；signal 用于命令面板取消上一次输入请求 */
export function searchStocks(
  q: string,
  limit = 20,
  signal?: AbortSignal,
): Promise<StockSearchResponse> {
  return request<StockSearchResponse>('/stocks/search', {
    query: { q, limit },
    signal,
  })
}
