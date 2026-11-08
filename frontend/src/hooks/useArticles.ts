/**
 * 新闻数据 Hook（契约第 2 节，字段与 V2 兼容）
 */
import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  fetchArticles,
  fetchArticle,
  searchArticles,
  fetchStats,
  fetchDailyTrend,
  fetchSourceDistribution,
  fetchKeywords,
  fetchProcessingStatus,
  fetchSources,
  triggerAutoCrawl,
  fetchAutoCrawlStatus,
  type AutoCrawlState,
} from '../api/news'
import { recordStatus } from '../lib/statusFeed'

/** 同一浏览器会话 10 分钟内最多触发一次自动抓取 */
const AUTO_CRAWL_KEY = 'gfna:news-auto-crawl-at'
const AUTO_CRAWL_INTERVAL_MS = 10 * 60 * 1000
const AUTO_CRAWL_POLL_MS = 4000

/** 文章列表（翻页保留旧数据，避免闪烁） */
export function useArticles(page: number, sourceId?: number | null) {
  return useQuery({
    queryKey: ['articles', page, sourceId],
    queryFn: () => fetchArticles(page, 20, sourceId),
    placeholderData: (prev) => prev,
  })
}

/** 文章详情 */
export function useArticle(id: number) {
  return useQuery({
    queryKey: ['article', id],
    queryFn: () => fetchArticle(id),
    enabled: Number.isFinite(id) && id > 0,
  })
}

/** 搜索 */
export function useSearch(q: string, page: number) {
  return useQuery({
    queryKey: ['search', q, page],
    queryFn: () => searchArticles(q, page),
    enabled: q.length > 0,
  })
}

/** 顶部新闻统计（30 秒轮询） */
export function useStats() {
  return useQuery({
    queryKey: ['stats'],
    queryFn: fetchStats,
    refetchInterval: 30_000,
  })
}

/** 新闻来源（前端不再写死来源） */
export function useSources() {
  return useQuery({
    queryKey: ['news-sources'],
    queryFn: fetchSources,
    staleTime: 30 * 60 * 1000,
  })
}

export function useDailyTrend(days = 30) {
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

/**
 * 打开网站自动抓取新闻。
 * - 会话内 10 分钟节流（sessionStorage），服务端还有并发锁 + 冷却；
 * - 抓取期间旧文章保持展示，结束后自动刷新列表/统计。
 */
export function useAutoCrawl() {
  const queryClient = useQueryClient()
  const [crawling, setCrawling] = useState(false)
  const [lastResult, setLastResult] = useState<AutoCrawlState['last_result']>(null)

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined

    const last = Number(sessionStorage.getItem(AUTO_CRAWL_KEY) || 0)
    const shouldTrigger = Date.now() - last >= AUTO_CRAWL_INTERVAL_MS
    if (shouldTrigger) {
      // 先落时间戳再请求：StrictMode 双执行 / 快速刷新都不会重复触发
      sessionStorage.setItem(AUTO_CRAWL_KEY, String(Date.now()))
    }

    const poll = async () => {
      try {
        const state = await fetchAutoCrawlStatus()
        if (cancelled) return
        if (state.running) {
          setCrawling(true)
          timer = setTimeout(poll, AUTO_CRAWL_POLL_MS)
          return
        }
        setCrawling(false)
        if (state.last_result) {
          setLastResult(state.last_result)
          void queryClient.invalidateQueries({ queryKey: ['articles'] })
          void queryClient.invalidateQueries({ queryKey: ['search'] })
          void queryClient.invalidateQueries({ queryKey: ['stats'] })
          void queryClient.invalidateQueries({ queryKey: ['news-sources'] })
        }
      } catch (error) {
        if (!cancelled) {
          setCrawling(false)
          recordStatus({
            level: 'warning',
            title: '新闻抓取状态检查失败',
            detail: error instanceof Error ? error.message : '请求失败',
            code: 'AUTO_CRAWL',
            suggestion: '已有新闻不受影响；可刷新页面重试。',
            path: '/',
          })
        }
      }
    }

    const start = async () => {
      try {
        const state = shouldTrigger ? await triggerAutoCrawl() : await fetchAutoCrawlStatus()
        if (cancelled) return
        if (state.status === 'started' || state.running) {
          setCrawling(true)
          timer = setTimeout(poll, 2500)
        }
      } catch (error) {
        if (!cancelled) {
          setCrawling(false)
          recordStatus({
            level: 'warning',
            title: '自动抓取未能启动',
            detail: error instanceof Error ? error.message : '请求失败',
            code: 'AUTO_CRAWL',
            suggestion: '已有新闻不受影响；可刷新页面重试。',
            path: '/',
          })
        }
      }
    }

    void start()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [queryClient])

  return { crawling, lastResult }
}
