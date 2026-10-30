/**
 * 新闻 API（契约第 2 节：字段与 V2 完全兼容）
 */
import { request } from './client'

/** 新闻文章（契约 2 · Article） */
export interface Article {
  id: number
  title: string
  url: string
  content: string | null
  author: string | null
  publish_time: string | null
  source_id: number
  source_name: string
  summary: string | null
  url_hash: string
  crawl_time: string | null
  /** 后端历史字段为 0/1，V2 兼容保持不变 */
  is_processed: number
  sentiment_score: number | null
}

/** 分页响应（契约 2 · PaginatedResponse） */
export interface PaginatedResponse {
  total: number
  page: number
  page_size: number
  total_pages: number
  items: Article[]
}

/** 新闻统计（契约 2 · /stats） */
export interface StatsResponse {
  total_articles: number
  total_sources: number
  articles_today: number
  processed_count: number
}

export interface DailyTrendItem {
  date: string
  count: number
}

export interface SourceDistributionItem {
  name: string
  count: number
  percentage: number
}

export interface KeywordItem {
  word: string
  count: number
}

export interface ProcessingStatus {
  total: number
  processed: number
  unprocessed: number
  failed: number
  percentage: number
}

/** 新闻来源（契约 2 · /sources，前端不再写死来源） */
export interface NewsSource {
  id: number
  name: string
}

/** 自动抓取状态（POST /crawl/auto、GET /crawl/auto/status） */
export interface AutoCrawlState {
  status?: 'started' | 'running' | 'throttled'
  running: boolean
  started_at: string | null
  finished_at: string | null
  /** 服务端冷却剩余秒数 */
  cooldown: number
  retry_after?: number
  last_result: {
    found: number
    new: number
    elapsed_ms: number
    sources: Record<string, { found: number; new: number; error: string | null }>
  } | null
  last_error: string | null
}

// ==================== 文章 ====================

export function fetchArticles(page = 1, pageSize = 20, sourceId?: number | null): Promise<PaginatedResponse> {
  return request<PaginatedResponse>('/articles', {
    query: { page, page_size: pageSize, source_id: sourceId ?? undefined },
  })
}

export function fetchArticle(id: number): Promise<Article> {
  return request<Article>(`/articles/${id}`)
}

export function searchArticles(q: string, page = 1, pageSize = 20): Promise<PaginatedResponse> {
  return request<PaginatedResponse>('/search', { query: { q, page, page_size: pageSize } })
}

// ==================== 统计 ====================

export function fetchStats(): Promise<StatsResponse> {
  return request<StatsResponse>('/stats')
}

export function fetchDailyTrend(days = 30): Promise<{ days: number; data: DailyTrendItem[] }> {
  return request<{ days: number; data: DailyTrendItem[] }>('/stats/daily-trend', { query: { days } })
}

export function fetchSourceDistribution(): Promise<{ data: SourceDistributionItem[]; total: number }> {
  return request<{ data: SourceDistributionItem[]; total: number }>('/stats/source-distribution')
}

export function fetchKeywords(limit = 20): Promise<{ keywords: KeywordItem[] }> {
  return request<{ keywords: KeywordItem[] }>('/stats/keywords', { query: { limit } })
}

export function fetchProcessingStatus(): Promise<ProcessingStatus> {
  return request<ProcessingStatus>('/stats/processing-status')
}

// ==================== 来源 ====================

/** 契约 2：前端来源筛选不再写死，改为读取 /sources（后端返回 {items,total}） */
export async function fetchSources(): Promise<NewsSource[]> {
  const payload = await request<{ items?: NewsSource[] }>('/sources')
  return payload.items ?? []
}

// ==================== 自动抓取 ====================

/** 打开网站时触发一次（服务端 10 分钟冷却 + 并发锁） */
export function triggerAutoCrawl(): Promise<AutoCrawlState> {
  return request<AutoCrawlState>('/crawl/auto', { method: 'POST' })
}

/** 轮询抓取状态 */
export function fetchAutoCrawlStatus(): Promise<AutoCrawlState> {
  return request<AutoCrawlState>('/crawl/auto/status')
}
