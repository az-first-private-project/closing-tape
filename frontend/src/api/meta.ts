/**
 * 元信息 API（契约第 1 节）：数据新鲜度、交易日历、任务列表、健康检查
 */
import { extractList, request } from './client'
import type { Freshness, HealthResponse, TradeCalendarResponse } from '../types/common'
import type { Job, JobQuery } from '../types/jobs'

/**
 * 所有数据集新鲜度（1）。
 * 注意：后端实际返回 `{items:[...], network_enabled, snapshot_ready_after}`，
 * 与契约 §1 写的 `Freshness[]` 有出入，这里兼容裸数组与包裹对象两种形态。
 */
export async function fetchFreshness(): Promise<Freshness[]> {
  const payload = await request<unknown>('/meta/freshness')
  return extractList<Freshness>(payload)
}

/** 交易日历（1）；start/end 缺省时后端按默认窗口返回 */
export function fetchTradeCalendar(start?: string, end?: string): Promise<TradeCalendarResponse> {
  return request<TradeCalendarResponse>('/meta/trade-calendar', {
    query: { start: start ?? undefined, end: end ?? undefined },
  })
}

/**
 * 采集/策略任务列表（1）。
 * 契约未固定包裹形状，这里兼容裸数组与 {items:[]}/{jobs:[]}。
 */
export async function fetchJobs(query: JobQuery = {}): Promise<Job[]> {
  const payload = await request<unknown>('/meta/jobs', {
    query: { limit: query.limit, task: query.task, status: query.status },
  })
  return extractList<Job>(payload)
}

/** 健康检查：注意 /health 不在 /api/v1 前缀下 */
export function fetchHealth(): Promise<HealthResponse> {
  return request<HealthResponse>('/health', { baseUrl: '' })
}
