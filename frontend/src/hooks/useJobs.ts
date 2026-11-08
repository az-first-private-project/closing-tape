/**
 * 任务监控 Hook（契约第 1 节 /meta/jobs）
 */
import { useQuery } from '@tanstack/react-query'
import { fetchJobs } from '../api/meta'
import type { JobQuery } from '../types/jobs'

/**
 * 采集/策略任务列表（C1：返回 items + total/has_more 分页元信息）
 * @param query limit / offset / task / status 过滤
 * @param paused 暂停后不再 30 秒轮询
 */
export function useJobs(query: JobQuery = {}, paused = false) {
  return useQuery({
    queryKey: [
      'jobs',
      query.limit ?? 50,
      query.offset ?? 0,
      query.task ?? '',
      query.status ?? '',
    ],
    queryFn: () => fetchJobs(query),
    refetchInterval: paused ? false : 30_000,
    staleTime: 10_000,
  })
}
