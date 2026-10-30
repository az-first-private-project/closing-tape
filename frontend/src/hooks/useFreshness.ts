/**
 * 数据新鲜度 Hook（契约 0.4 / 1）
 */
import { useQuery } from '@tanstack/react-query'
import { fetchFreshness } from '../api/meta'

/** 全部数据集新鲜度：仪表盘顶部与任务监控页共用 */
export function useFreshness() {
  return useQuery({
    queryKey: ['freshness'],
    queryFn: fetchFreshness,
    staleTime: 60 * 1000,
    refetchInterval: 60 * 1000,
  })
}
