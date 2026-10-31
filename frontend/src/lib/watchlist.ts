/**
 * 自选股状态（V6 T8）
 * ====================
 * 存储分层：
 *  - 服务端单租户单行（`app_watchlist_state`）是**事实源**，同一后端下所有浏览器共享；
 *  - localStorage 只是缓存，键：`gfna-watchlist` / `gfna-watchlist-seen`；
 *  - `gfna-watchlist-pending` 标记本地有未同步改动，刷新后优先推送，避免被旧服务端覆盖。
 *
 * 隐私模式 localStorage 会抛：此时降级为内存态并标记 storageBroken；
 * 页面启动仍会从服务端 hydrate，因此刷新后可从服务端恢复，不再永久丢失。
 */
import {
  fetchWatchlistState,
  saveWatchlistState,
  type WatchlistState,
} from '../api/watchlist'

const STORAGE_KEY = 'gfna-watchlist'
const SEEN_STORAGE_KEY = 'gfna-watchlist-seen'
const PENDING_KEY = 'gfna-watchlist-pending'
const CHANGE_EVENT = 'gfna-watchlist-change'
const MAX_SEEN_HITS = 500
const SYNC_DEBOUNCE_MS = 400
const RETRY_DELAY_MS = 15_000

export const MAX_WATCHLIST = 100

export type WatchlistSyncStatus = 'idle' | 'syncing' | 'synced' | 'offline'

/** localStorage 不可用时的内存副本 */
let memory: string[] = []
let seenMemory: string[] = []
/** 任一读写失败后锁定内存模式，避免后续 getItem 把内存态覆盖回旧值 */
let storageBroken = false
/** 有本地改动尚未被服务端确认 */
let localDirty = false
/** 本地改动的版本号，用于避免在途 PUT 覆盖并发改动 */
let changeVersion = 0
let syncStatus: WatchlistSyncStatus = 'idle'
let hydration: Promise<void> | null = null
let pushInFlight = false
let pushQueued = false
let pushTimer: ReturnType<typeof setTimeout> | null = null
let retryTimer: ReturnType<typeof setTimeout> | null = null

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

function normalizeSeen(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  const seen = new Set<string>()
  for (const item of value) {
    if (typeof item !== 'string') continue
    if (!/^[a-z0-9_]+:\d{4}-\d{2}-\d{2}$/i.test(item)) continue
    seen.add(item)
  }
  return [...seen].slice(-MAX_SEEN_HITS)
}

function mergeSeen(a: string[], b: string[]): string[] {
  return normalizeSeen([...a, ...b])
}

function writeLocal(key: string, value: unknown): boolean {
  try {
    localStorage.setItem(key, JSON.stringify(value))
    return true
  } catch {
    storageBroken = true
    return false
  }
}

function setCodes(codes: string[]): boolean {
  memory = [...codes]
  const ok = writeLocal(STORAGE_KEY, memory)
  if (ok) storageBroken = false
  return ok
}

function setSeen(hits: string[]): boolean {
  seenMemory = [...hits]
  const ok = writeLocal(SEEN_STORAGE_KEY, seenMemory)
  if (ok) storageBroken = false
  return ok
}

/** 当前自选代码列表（去重、过滤非法值） */
export function getWatchlist(): string[] {
  if (storageBroken) return [...memory]
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    memory = normalize(raw ? JSON.parse(raw) : [])
    return [...memory]
  } catch {
    storageBroken = true
    return [...memory]
  }
}

export function isWatched(code: string): boolean {
  return getWatchlist().includes(code)
}

/** 已读的命中键（algo_code:signal_date），用于计算导航栏新提醒 */
export function getSeenHitIds(): string[] {
  if (storageBroken) return [...seenMemory]
  try {
    const raw = localStorage.getItem(SEEN_STORAGE_KEY)
    seenMemory = normalizeSeen(raw ? JSON.parse(raw) : [])
    return [...seenMemory]
  } catch {
    storageBroken = true
    return [...seenMemory]
  }
}

export function getWatchlistSyncStatus(): WatchlistSyncStatus {
  return syncStatus
}

function setSyncStatus(next: WatchlistSyncStatus): void {
  if (syncStatus === next) return
  syncStatus = next
  emitChange()
}

// ==================== 未同步标记 ====================

function markPending(): void {
  writeLocal(PENDING_KEY, { codes: memory, seen_hits: seenMemory })
}

function clearPending(): void {
  try {
    localStorage.removeItem(PENDING_KEY)
  } catch {
    // 忽略：清理失败不影响本地缓存
  }
}

function readPending(): { codes: string[]; seen_hits: string[] } | null {
  if (storageBroken) return null
  try {
    const raw = localStorage.getItem(PENDING_KEY)
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return null
    const record = parsed as Record<string, unknown>
    return {
      codes: normalize(record.codes),
      seen_hits: normalizeSeen(record.seen_hits),
    }
  } catch {
    return null
  }
}

// ==================== 服务端同步 ====================

function schedulePush(): void {
  if (pushInFlight) {
    pushQueued = true
    return
  }
  if (pushTimer !== null) clearTimeout(pushTimer)
  pushTimer = setTimeout(() => {
    pushTimer = null
    void pushState()
  }, SYNC_DEBOUNCE_MS)
}

function scheduleRetry(): void {
  if (typeof window === 'undefined' || retryTimer !== null) return
  retryTimer = setTimeout(() => {
    retryTimer = null
    if (localDirty || readPending()) void pushState()
  }, RETRY_DELAY_MS)
}

function clearRetry(): void {
  if (retryTimer !== null) {
    clearTimeout(retryTimer)
    retryTimer = null
  }
}

/** 把当前本地状态整份推给服务端；失败保留 pending，稍后重试 */
async function pushState(): Promise<boolean> {
  if (pushInFlight) {
    pushQueued = true
    return false
  }
  pushInFlight = true
  const version = changeVersion
  const codes = [...memory]
  const seen = [...seenMemory]
  setSyncStatus('syncing')
  try {
    const saved = await saveWatchlistState(codes, seen)
    if (changeVersion === version) {
      setCodes(normalize(saved.codes))
      setSeen(normalizeSeen(saved.seen_hits))
      localDirty = false
      clearPending()
      setSyncStatus('synced')
      clearRetry()
    } else {
      // 在途期间又有本地改动：保留 pending，稍后把最新状态再推一次
      pushQueued = true
    }
    emitChange()
    return true
  } catch {
    markPending()
    setSyncStatus('offline')
    scheduleRetry()
    return false
  } finally {
    pushInFlight = false
    if (pushQueued) {
      pushQueued = false
      schedulePush()
    }
  }
}

async function doHydrate(): Promise<void> {
  const pending = readPending()
  if (pending || localDirty) {
    if (pending) {
      memory = pending.codes
      seenMemory = pending.seen_hits
    }
    const ok = await pushState()
    if (!ok) return
    // pushState 已把服务端确认后的状态写回本地
  }

  setSyncStatus('syncing')
  let remote: WatchlistState
  try {
    remote = await fetchWatchlistState()
  } catch {
    setSyncStatus('offline')
    scheduleRetry()
    return
  }

  // 请求期间用户又改了：以本地为准，推上去，避免被旧服务端覆盖
  if (localDirty || readPending()) {
    await pushState()
    return
  }

  const remoteCodes = normalize(remote.codes)
  const remoteSeen = normalizeSeen(remote.seen_hits)
  const localCodes = getWatchlist()
  const localSeen = getSeenHitIds()

  if (remoteCodes.length === 0 && localCodes.length > 0) {
    // 首次升级：服务端为空，把本机已有名单迁上去
    setCodes(localCodes)
    setSeen(mergeSeen(localSeen, remoteSeen))
    localDirty = true
    changeVersion += 1
    markPending()
    await pushState()
    return
  }

  setCodes(remoteCodes)
  const mergedSeen = mergeSeen(localSeen, remoteSeen)
  setSeen(mergedSeen)
  localDirty = false
  if (mergedSeen.length !== remoteSeen.length) {
    // 只读水位取并集：服务端缺的本地已读补推一次
    changeVersion += 1
    markPending()
    schedulePush()
  } else {
    clearPending()
    setSyncStatus('synced')
  }
  emitChange()
}

/** 启动时调用：拉服务端状态并合并本地未同步改动 */
export function hydrateWatchlist(): Promise<void> {
  if (hydration === null) {
    hydration = doHydrate().finally(() => {
      if (syncStatus === 'offline') hydration = null
    })
  }
  return hydration
}

// ==================== 本地写操作 ====================

export interface ToggleResult {
  watched: boolean
  /** limit=达到上限；storage=本机缓存不可写（仍会尝试同步服务端） */
  reason: 'limit' | 'storage' | null
}

/** 切换自选；超限或本机缓存不可用时返回原因，绝不抛错 */
export function toggleWatch(code: string): ToggleResult {
  if (!/^\d{1,6}$/.test(code)) return { watched: false, reason: null }
  const current = getWatchlist()
  const watched = !current.includes(code)
  if (watched && current.length >= MAX_WATCHLIST) {
    return { watched: false, reason: 'limit' }
  }
  const next = watched ? [...current, code] : current.filter((item) => item !== code)
  const persisted = setCodes(next)
  localDirty = true
  changeVersion += 1
  markPending()
  emitChange()
  schedulePush()
  return { watched, reason: persisted ? null : 'storage' }
}

/** 把当前页面的命中写入已读水位；重复调用不会产生新的事件 */
export function markHitsSeen(hitIds: string[]): void {
  const current = getSeenHitIds()
  const merged = new Set(current)
  let changed = false
  for (const hitId of hitIds) {
    if (typeof hitId !== 'string' || merged.has(hitId)) continue
    merged.add(hitId)
    changed = true
  }
  if (!changed) return
  setSeen([...merged].slice(-MAX_SEEN_HITS))
  localDirty = true
  changeVersion += 1
  markPending()
  emitChange()
  schedulePush()
}

// ==================== 订阅 ====================

/** 监听自选/已读/同步状态变化（同标签页 + 跨标签页 storage 事件） */
export function subscribeWatchlist(listener: () => void): () => void {
  if (typeof window === 'undefined') return () => undefined
  const onStorage = (event: StorageEvent) => {
    if (event.key === null || event.key === STORAGE_KEY || event.key === SEEN_STORAGE_KEY) {
      listener()
    }
  }
  window.addEventListener(CHANGE_EVENT, listener)
  window.addEventListener('storage', onStorage)
  return () => {
    window.removeEventListener(CHANGE_EVENT, listener)
    window.removeEventListener('storage', onStorage)
  }
}

function emitChange(): void {
  try {
    window.dispatchEvent(new Event(CHANGE_EVENT))
  } catch {
    // 非浏览器环境（SSR / 单测）忽略
  }
}

if (typeof window !== 'undefined') {
  window.addEventListener('online', () => {
    if (syncStatus === 'offline') {
      hydration = null
      void hydrateWatchlist()
    } else if (localDirty || readPending()) {
      schedulePush()
    }
  })
}
