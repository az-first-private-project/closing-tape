import { useQuery } from '@tanstack/react-query'
import {
  fetchArticles,
  fetchArticle,
  searchArticles,
  fetchStats,
  fetchDailyTrend,
  fetchSourceDistribution,
  fetchKeywords,
  fetchProcessingStatus,
} from '../lib/api'

// ==================== 文章列表 ====================
export function useArticles(page: number, sourceId?: number | null) {
  return useQuery({
    queryKey: ['articles', page, sourceId],
    queryFn: () => fetchArticles(page, 20, sourceId),
    placeholderData: (prev) => prev, // 翻页时保留旧数据，避免闪烁
  })
}

// ==================== 文章详情 ====================
export function useArticle(id: number) {
  return useQuery({
    queryKey: ['article', id],
    queryFn: () => fetchArticle(id),
    enabled: !!id,
  })
}

// ==================== 搜索 ====================
export function useSearch(q: string, page: number) {
  return useQuery({
    queryKey: ['search', q, page],
    queryFn: () => searchArticles(q, page),
    enabled: q.length > 0,
  })
}

// ==================== 统计数据 ====================
export function useStats() {
  return useQuery({
    queryKey: ['stats'],
    queryFn: fetchStats,
    refetchInterval: 30_000, // 每 30 秒自动刷新
  })
}

export function useDailyTrend(days: number = 30) {
  return useQuery({
    queryKey: ['daily-trend', days],
    queryFn: () => fetchDailyTrend(days),
  })
}

export function useSourceDistribution() {
  return useQuery({
    queryKey: ['source-distribution'],
    queryFn: fetchSourceDistribution,
  })
}

export function useKeywords() {
  return useQuery({
    queryKey: ['keywords'],
    queryFn: () => fetchKeywords(20),
  })
}

export function useProcessingStatus() {
  return useQuery({
    queryKey: ['processing-status'],
    queryFn: fetchProcessingStatus,
    refetchInterval: 30_000,
  })
}
