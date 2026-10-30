/**
 * 自选股本地 store（V6 T6 先行落地最小版本，T8 在此基础上加提醒）
 * =====================================================================
 * 项目没有账号体系，名单只存 localStorage（key `gfna-watchlist`）。
 * localStorage 在隐私模式/被禁用时会抛异常：此时降级为内存态，并让调用方
 * 通过 reason='storage' 提示"本浏览器不保存自选"（刷新后丢失）。
 */
const STORAGE_KEY = 'gfna-watchlist'

export const MAX_WATCHLIST = 100

/** localStorage 不可用时的内存副本 */
let memory: string[] = []

function normalize(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  const seen = new Set<string>()
  for (const item of value) {
    if (typeof item !== 'string') continue
    if (!/^\d{1,6}$/.test(item)) continue
    seen.add(item)
  }
  return [...seen]
}

/** 当前自选代码列表（去重、过滤非法值） */
export function getWatchlist(): string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    memory = normalize(raw ? JSON.parse(raw) : [])
    return [...memory]
  } catch {
    return [...memory]
  }
}

export function isWatched(code: string): boolean {
  return getWatchlist().includes(code)
}

export interface ToggleResult {
  watched: boolean
  /** limit=达到上限；storage=浏览器不保存，仅当前页面生效 */
  reason: 'limit' | 'storage' | null
}

/** 切换自选；超限或存储不可用时返回原因，绝不抛错 */
export function toggleWatch(code: string): ToggleResult {
  if (!/^\d{1,6}$/.test(code)) return { watched: false, reason: null }
  const current = getWatchlist()
  const watched = !current.includes(code)
  if (watched && current.length >= MAX_WATCHLIST) {
    return { watched: false, reason: 'limit' }
  }
  const next = watched ? [...current, code] : current.filter((item) => item !== code)
  memory = next
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    return { watched, reason: null }
  } catch {
    return { watched, reason: 'storage' }
  }
}
