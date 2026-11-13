/**
 * 元信息 API（契约第 1 节）：数据新鲜度、交易日历、任务列表、健康检查
 */
import { extractList, extractListPage, request } from './client'
import type {
  Freshness,
  HealthResponse,
  ListPage,
  TradeCalendarResponse,
} from '../types/common'
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
 * 采集/策略任务列表（1，C1 追加分页元信息）。
 * 保留包裹对象里的 total/has_more/page/page_size；裸数组时仅返回 items。
 */
export async function fetchJobs(query: JobQuery = {}): Promise<ListPage<Job>> {
  const payload = await request<unknown>('/meta/jobs', {
    query: {
      limit: query.limit,
      offset: query.offset,
      task: query.task,
      status: query.status,
    },
  })
  return extractListPage<Job>(payload)
}

/** 健康检查：注意 /health 不在 /api/v1 前缀下 */
export function fetchHealth(): Promise<HealthResponse> {
  return request<HealthResponse>('/health', { baseUrl: '' })
}

/**
 * 一键补数：检查最近缺失交易日并在后台补跑（复用启动补缺逻辑）。
 * 配置了 ADMIN_API_KEY 时需传 X-Admin-Key；仅本机/管理密钥可调用。
 */
export function triggerCatchUp(
  adminKey?: string,
): Promise<{ status: string; mode: string; hint: string }> {
  return request<{ status: string; mode: string; hint: string }>('/admin/catch-up', {
    method: 'POST',
    headers: adminKey ? { 'X-Admin-Key': adminKey } : undefined,
  })
}
