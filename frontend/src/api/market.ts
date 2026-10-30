/**
 * 行情 API（契约第 3 节，替代旧 /analysis/*）
 */
import { request } from './client'
import type {
  DashboardData,
  MarketTemperature,
  SectorDetail,
  SectorList,
  SectorType,
  TemperatureSeries,
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
 * 市场温度计单日全字段（T3）
 * 无数据时后端按契约返回 409 DATA_NOT_READY，由调用方区分「未生成」与「接口故障」。
 */
export function fetchTemperature(tradeDate?: string): Promise<MarketTemperature> {
  return request<MarketTemperature>('/market/temperature', {
    query: { trade_date: tradeDate ?? undefined },
  })
}

/** 市场温度近 N 个交易日序列（T3，sparkline 用） */
export function fetchTemperatureSeries(days = 60, tradeDate?: string): Promise<TemperatureSeries> {
  return request<TemperatureSeries>('/market/temperature', {
    query: { trade_date: tradeDate ?? undefined, days },
  })
}
