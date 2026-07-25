/**
 * API 请求封装
 * 统一的 fetch 封装，自动处理 JSON 转换和错误处理
 */
const BASE_URL = '/api/v1'

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
  is_processed: number
  sentiment_score: number | null
}

export interface PaginatedResponse {
  total: number
  page: number
  page_size: number
  total_pages: number
  items: Article[]
}

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

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: {
      'Content-Type': 'application/json',
    },
    ...options,
  })

  if (!response.ok) {
    throw new Error(`请求失败: ${response.status} ${response.statusText}`)
  }

  return response.json()
}

// ==================== 文章 API ====================

export async function fetchArticles(
  page: number = 1,
  pageSize: number = 20,
  sourceId?: number | null
): Promise<PaginatedResponse> {
  let url = `${BASE_URL}/articles?page=${page}&page_size=${pageSize}`
  if (sourceId) {
    url += `&source_id=${sourceId}`
  }
  return request<PaginatedResponse>(url)
}

export async function fetchArticle(id: number): Promise<Article> {
  return request<Article>(`${BASE_URL}/articles/${id}`)
}

export async function searchArticles(
  q: string,
  page: number = 1,
  pageSize: number = 20
): Promise<PaginatedResponse> {
  const params = new URLSearchParams({ q, page: String(page), page_size: String(pageSize) })
  return request<PaginatedResponse>(`${BASE_URL}/search?${params}`)
}

// ==================== 统计 API ====================

export async function fetchStats(): Promise<StatsResponse> {
  return request<StatsResponse>(`${BASE_URL}/stats`)
}

export async function fetchDailyTrend(days: number = 30): Promise<{ days: number; data: DailyTrendItem[] }> {
  return request(`${BASE_URL}/stats/daily-trend?days=${days}`)
}

export async function fetchSourceDistribution(): Promise<{ data: SourceDistributionItem[]; total: number }> {
  return request(`${BASE_URL}/stats/source-distribution`)
}

export async function fetchKeywords(limit: number = 20): Promise<{ keywords: KeywordItem[] }> {
  return request(`${BASE_URL}/stats/keywords?limit=${limit}`)
}

export async function fetchProcessingStatus(): Promise<ProcessingStatus> {
  return request<ProcessingStatus>(`${BASE_URL}/stats/processing-status`)
}
