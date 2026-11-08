/**
 * 本次会话的状态流水（U09 最小版本）
 * - 记录接口异常与瞬时提示，供导航栏「状态中心」追溯；
 * - 仅 sessionStorage，不引入事件表 / 事件服务；
 * - 同签名事件 30 秒内去重，避免一页多请求同时失败刷屏。
 */
export type StatusLevel = 'success' | 'info' | 'warning' | 'error'

export interface StatusEvent {
  id: string
  at: number
  level: StatusLevel
  title: string
  detail?: string
  code?: string
  suggestion?: string
  path?: string
  requestId?: string
  read: boolean
}

const STORAGE_KEY = 'gfna-status-feed'
const LIMIT = 50
const DEDUPE_MS = 30_000
const listeners = new Set<() => void>()

function safeRead(): StatusEvent[] {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter(
      (item): item is StatusEvent =>
        typeof item === 'object' &&
        item !== null &&
        typeof (item as StatusEvent).id === 'string' &&
        typeof (item as StatusEvent).at === 'number',
    )
  } catch {
    return []
  }
}

function persist(events: StatusEvent[]): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(events))
  } catch {
    // 隐私模式：中心仍可在本次渲染中工作
  }
  listeners.forEach((listener) => listener())
}

export function listStatus(): StatusEvent[] {
  return safeRead()
}

export function recordStatus(input: Omit<StatusEvent, 'id' | 'at' | 'read'>): void {
  const events = safeRead()
  const last = events[0]
  if (
    last &&
    Date.now() - last.at < DEDUPE_MS &&
    last.level === input.level &&
    last.title === input.title &&
    last.detail === input.detail &&
    last.code === input.code &&
    last.path === input.path
  ) {
    return
  }
  const event: StatusEvent = {
    ...input,
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    at: Date.now(),
    read: false,
  }
  persist([event, ...events].slice(0, LIMIT))
}

export function markStatusRead(): void {
  const events = safeRead()
  if (!events.some((event) => !event.read)) return
  persist(events.map((event) => ({ ...event, read: true })))
}

export function clearStatus(): void {
  persist([])
}

export function subscribeStatus(listener: () => void): () => void {
  listeners.add(listener)
  window.addEventListener('storage', listener)
  return () => {
    listeners.delete(listener)
    window.removeEventListener('storage', listener)
  }
}
