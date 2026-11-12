/**
 * 行情 API（契约第 3 节，替代旧 /analysis/*）
 */
import { request } from './client'
import type {
  DashboardData,
  IndexSeriesResponse,
  MarketOverview,
  SectorDetail,
  SectorList,
  SectorScoreSparkline,
  SectorType,
} from '../types/market'

/**
 * 盘后分析仪表盘（3.1）
 * 无数据时后端返回 200 + sectors:[] + freshness(unknown)，不抛错。
 */
export function fetchDashboard(tradeDate?: string): Promise<DashboardData> {
  return request<DashboardData>('/market/dashboard', {
    query: { trade_date: tradeDate ?? undefined },
  })
}

/**
 * 指数近期日线序列（T14 sparkline）
 * days 为自然日；取 60 天覆盖长假后的 30 个交易日，前端再截最后 30 点。
 */
export function fetchIndexSeries(days = 60, tradeDate?: string): Promise<IndexSeriesResponse> {
  return request<IndexSeriesResponse>('/market/indices', {
    query: { trade_date: tradeDate ?? undefined, days },
  })
}

/** 单板块详情（3.2） */
export function fetchSectorDetail(sectorCode: string, tradeDate?: string): Promise<SectorDetail> {
  return request<SectorDetail>(`/market/sector/${encodeURIComponent(sectorCode)}`, {
    query: { trade_date: tradeDate ?? undefined },
  })
}

/** 板块列表（3.1/T4；含市值、资金流、相对强度，供 treemap 使用） */
export function fetchSectors(
  tradeDate?: string,
  sectorType?: SectorType,
): Promise<SectorList> {
  return request<SectorList>('/market/sectors', {
    query: { trade_date: tradeDate ?? undefined, sector_type: sectorType },
  })
}

/**
 * 板块综合评分近 N 个评分日序列（P3-2 评分榜 sparkline）
 * 横轴为库内实际评分日；缺分日期返回 null，由前端断线显示。
 */
export function fetchSectorScoreSparkline(
  codes: string[],
  options: { days?: number; end?: string } = {},
): Promise<SectorScoreSparkline> {
  return request<SectorScoreSparkline>('/market/sector-scores/sparkline', {
    query: {
      codes: codes.join(','),
      days: options.days ?? 30,
      end: options.end || undefined,
    },
  })
}

/**
 * 市场状态 + 配置建议 + 一周温度计（V7 T1）
 * 有状态：完整三块；数据积累中：regime/allocation 为 null，thermometer 可用。
 */
export function fetchMarketOverview(tradeDate?: string): Promise<MarketOverview> {
  return request<MarketOverview>('/market/overview', {
    query: { trade_date: tradeDate ?? undefined },
  })
}
