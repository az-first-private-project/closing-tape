/**
 * 自选股信号提醒 Hook（V6 T8）
 * ==============================
 * - 名单来自 localStorage，变更通过 watchlist store 的事件同步到所有组件；
 * - 命中查询每 60 秒轮询，且只在标签页可见时启用（切后台即停，省 QPS）；
 * - 重试语义沿用 client.ts：5xx / 网络错误最多 1 次，4xx 不重试；
 * - 新提醒 = 当前命中里未写入已读水位（gfna-watchlist-seen）的 hit_id。
 */
import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchWatchHits } from '../api/algo'
import {
  getSeenHitIds,
  getWatchlist,
  getWatchlistSyncStatus,
  subscribeWatchlist,
  type WatchlistSyncStatus,
} from '../lib/watchlist'
import type { WatchHit } from '../types/algo'

/** 订阅自选名单（同标签页 + 跨标签页） */
export function useWatchlist(): string[] {
  const [watchlist, setWatchlist] = useState<string[]>(() => getWatchlist())
  useEffect(() => subscribeWatchlist(() => setWatchlist(getWatchlist())), [])
  return watchlist
}

/** 订阅已读水位 */
export function useSeenHitIds(): string[] {
  const [seen, setSeen] = useState<string[]>(() => getSeenHitIds())
  useEffect(() => subscribeWatchlist(() => setSeen(getSeenHitIds())), [])
  return seen
}

/** 服务端同步状态（用于提示“已保存到服务器/仅本机”） */
export function useWatchSyncStatus(): WatchlistSyncStatus {
  const [status, setStatus] = useState<WatchlistSyncStatus>(() => getWatchlistSyncStatus())
  useEffect(() => subscribeWatchlist(() => setStatus(getWatchlistSyncStatus())), [])
  return status
}

/** 页面可见性：后台标签页返回 false，用于停掉轮询 */
function usePageVisible(): boolean {
  const [visible, setVisible] = useState(
    () => typeof document === 'undefined' || document.visibilityState === 'visible',
  )
  useEffect(() => {
    const onChange = () => setVisible(document.visibilityState === 'visible')
    document.addEventListener('visibilitychange', onChange)
    return () => document.removeEventListener('visibilitychange', onChange)
  }, [])
  return visible
}

/** 未读 hit_id（按 algo_code:signal_date 去重） */
export function unseenHitIds(items: WatchHit[], seenIds: string[]): string[] {
  const seen = new Set(seenIds)
  const unseen = new Set<string>()
  for (const item of items) {
    if (!seen.has(item.hit_id)) unseen.add(item.hit_id)
  }
  return [...unseen]
}

/** 自选股近 N 个交易日的命中（后台标签页不轮询） */
export function useWatchSignals(watchlist: string[], days = 10) {
  const visible = usePageVisible()

  return useQuery({
    queryKey: ['watch-hits', days, watchlist.join(',')],
    queryFn: () => fetchWatchHits(watchlist, days),
    enabled: watchlist.length > 0,
    staleTime: 30_000,
    refetchInterval: visible ? 60_000 : false,
    refetchIntervalInBackground: false,
    // client.ts 已对 5xx / 网络错误重试 1 次，这里不再叠加重试
    retry: false,
  })
}

/** 导航栏红点数据：未读命中数（同一策略同一天只算一条） */
export function useWatchBadge(days = 10) {
  const watchlist = useWatchlist()
  const seen = useSeenHitIds()
  const query = useWatchSignals(watchlist, days)

  const newCount = useMemo(
    () => unseenHitIds(query.data?.items ?? [], seen).length,
    [query.data, seen],
  )

  return { watchlist, seen, query, newCount }
}
