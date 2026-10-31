/**
 * 自选状态 API（V6 T8）
 * ======================
 * 服务端单租户存储：没有账号体系，同一后端共享一份名单与已读水位。
 * 前端 localStorage 仅作缓存；这里负责读取与整份覆盖。
 */
import { request } from './client'

export interface WatchlistState {
  codes: string[]
  seen_hits: string[]
  updated_at: string | null
}

/** 读取服务端自选状态 */
export function fetchWatchlistState(): Promise<WatchlistState> {
  return request<WatchlistState>('/watchlist')
}

/** 整份覆盖自选状态（PUT 幂等，允许重试一次） */
export function saveWatchlistState(
  codes: string[],
  seenHits: string[],
): Promise<WatchlistState> {
  return request<WatchlistState>('/watchlist', {
    method: 'PUT',
    body: { codes, seen_hits: seenHits },
    retry: 1,
  })
}
