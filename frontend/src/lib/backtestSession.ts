/**
 * 回测会话状态（缺陷 1）
 * ======================
 * 把 bt_id / grid_id 从组件生命周期中解耦：
 * - 单次回测只持久化任务引用，完整结果仍由后端按 bt_id 恢复；
 * - 参数矩阵只持久化 grid_id 与扫描输入，格子进度由 GET /backtest/grid/{id} 恢复；
 * - 使用 localStorage：路由切换、F5、关闭标签页/重启浏览器后都可恢复（本地单机工具）；
 *   旧版本存在 sessionStorage 的数据在首次读取时自动迁移。
 */

export interface StoredBacktestTask {
  algoCode: string
  btId: string
  startedAt: number
  start?: string
  rebalanceDays?: number
  maxPositions?: number
}

export interface StoredPanelState {
  tab: 'single' | 'grid'
  start: string
  rebalanceDays?: number
}

export const GRID_METRICS = ['excess', 'drawdown', 'sharpe', 'win', 'trades'] as const
export type GridMetric = (typeof GRID_METRICS)[number]

export interface StoredGridState {
  gridId: string | null
  start: string
  endOverride: string
  rebalanceDays: number[]
  maxPositions: number[]
  metric: GridMetric
  selectedBtId: string | null
}

const ACTIVE_KEY = 'grna:backtest:active:v1'
const PANEL_PREFIX = 'grna:backtest:panel:v1:'
const GRID_PREFIX = 'grna:backtest:grid:v1:'
const SELECTED_ALGO_KEY = 'grna:algo:selected:v1'

type ActiveMap = Record<string, StoredBacktestTask>

let activeCache: ActiveMap | null = null
let revision = 0
const listeners = new Set<() => void>()

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

function legacyStorage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage
  } catch {
    return null
  }
}

function readJson(key: string): unknown {
  let raw: string | null = null
  try {
    raw = storage()?.getItem(key) ?? null
  } catch {
    raw = null
  }
  if (raw) {
    try {
      return JSON.parse(raw)
    } catch {
      return null
    }
  }
  // 旧版本 sessionStorage 数据迁移：写入成功后再删除，避免隐私模式丢数据
  const legacy = legacyStorage()
  if (!legacy) return null
  try {
    const legacyRaw = legacy.getItem(key)
    if (!legacyRaw) return null
    const parsed = JSON.parse(legacyRaw)
    try {
      storage()?.setItem(key, legacyRaw)
      legacy.removeItem(key)
    } catch {
      // 写入失败时保留旧数据，下次再迁移
    }
    return parsed
  } catch {
    return null
  }
}

function writeJson(key: string, value: unknown): void {
  const store = storage()
  if (!store) return
  try {
    store.setItem(key, JSON.stringify(value))
  } catch {
    // 隐私模式/配额不足时只保留内存状态
  }
}

/** 升级时的整表迁移：把旧版本 sessionStorage 中的 grna:* 键搬到 localStorage */
function migrateLegacyStorage(): void {
  const legacy = legacyStorage()
  const store = storage()
  if (!legacy || !store) return
  try {
    const keys: string[] = []
    for (let index = 0; index < legacy.length; index += 1) {
      const key = legacy.key(index)
      if (key && key.startsWith('grna:')) keys.push(key)
    }
    for (const key of keys) {
      const raw = legacy.getItem(key)
      if (!raw) continue
      try {
        if (store.getItem(key) === null) store.setItem(key, raw)
        legacy.removeItem(key)
      } catch {
        // 单条失败不影响其他键
      }
    }
  } catch {
    // 忽略迁移失败，读取时还有单键兜底
  }
}

migrateLegacyStorage()

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function asString(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}

function asPositiveInt(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
    ? Math.round(value)
    : undefined
}

function asNumberArray(value: unknown): number[] | null {
  if (!Array.isArray(value)) return null
  const numbers = value
    .filter((item): item is number => typeof item === 'number' && Number.isFinite(item))
    .map((item) => Math.round(item))
  return numbers.length > 0 ? numbers : null
}

function sanitizeTask(algoCode: string, value: unknown): StoredBacktestTask | null {
  if (!isRecord(value)) return null
  const btId = asString(value.btId)
  const startedAt =
    typeof value.startedAt === 'number' && Number.isFinite(value.startedAt)
      ? value.startedAt
      : null
  if (!btId || startedAt === null) return null
  return {
    algoCode,
    btId,
    startedAt,
    start: asString(value.start),
    rebalanceDays: asPositiveInt(value.rebalanceDays),
    maxPositions: asPositiveInt(value.maxPositions),
  }
}

function readActiveMap(): ActiveMap {
  if (activeCache) return activeCache
  const parsed = readJson(ACTIVE_KEY)
  const next: ActiveMap = {}
  if (isRecord(parsed)) {
    for (const [algoCode, value] of Object.entries(parsed)) {
      const task = sanitizeTask(algoCode, value)
      if (task) next[algoCode] = task
    }
  }
  activeCache = next
  return next
}

function emit(): void {
  revision += 1
  listeners.forEach((listener) => listener())
}

export function subscribeBacktestSession(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function getBacktestSessionRevision(): number {
  return revision
}

export function getActiveBacktest(algoCode: string): StoredBacktestTask | null {
  return readActiveMap()[algoCode] ?? null
}

export function saveActiveBacktest(task: StoredBacktestTask): void {
  activeCache = { ...readActiveMap(), [task.algoCode]: task }
  writeJson(ACTIVE_KEY, activeCache)
  emit()
}

export function clearActiveBacktest(algoCode: string, btId?: string): void {
  const current = readActiveMap()
  const task = current[algoCode]
  if (!task || (btId && task.btId !== btId)) return
  const next = { ...current }
  delete next[algoCode]
  activeCache = next
  writeJson(ACTIVE_KEY, next)
  emit()
}

function panelKey(algoCode: string): string {
  return `${PANEL_PREFIX}${algoCode}`
}

export function loadPanelState(algoCode: string): StoredPanelState | null {
  const parsed = readJson(panelKey(algoCode))
  if (!isRecord(parsed)) return null
  const tab = parsed.tab === 'single' || parsed.tab === 'grid' ? parsed.tab : null
  if (!tab) return null
  return {
    tab,
    start: asString(parsed.start) ?? '',
    rebalanceDays: asPositiveInt(parsed.rebalanceDays),
  }
}

export function savePanelState(algoCode: string, state: StoredPanelState): void {
  writeJson(panelKey(algoCode), state)
}

function gridKey(algoCode: string): string {
  return `${GRID_PREFIX}${algoCode}`
}

export function loadGridState(algoCode: string): StoredGridState | null {
  const parsed = readJson(gridKey(algoCode))
  if (!isRecord(parsed)) return null
  const rebalanceDays = asNumberArray(parsed.rebalanceDays)
  const maxPositions = asNumberArray(parsed.maxPositions)
  if (!rebalanceDays || !maxPositions) return null
  const metric = GRID_METRICS.includes(parsed.metric as GridMetric)
    ? (parsed.metric as GridMetric)
    : 'excess'
  const gridId = asString(parsed.gridId)
  const selectedBtId = asString(parsed.selectedBtId)
  return {
    gridId: gridId || null,
    start: asString(parsed.start) ?? '',
    endOverride: asString(parsed.endOverride) ?? '',
    rebalanceDays,
    maxPositions,
    metric,
    selectedBtId: selectedBtId || null,
  }
}

export function saveGridState(algoCode: string, state: StoredGridState): void {
  writeJson(gridKey(algoCode), state)
}

export function clearGridState(algoCode: string, gridId?: string): void {
  const current = loadGridState(algoCode)
  if (!current || (gridId && current.gridId !== gridId)) return
  for (const store of [storage(), legacyStorage()]) {
    if (!store) continue
    try {
      store.removeItem(gridKey(algoCode))
    } catch {
      // 忽略存储清理失败
    }
  }
}

/**
 * 选股中心当前策略（缺陷 1 根因）
 * ================================
 * 策略选择原先只存在组件 state：离开页面再回来会回落默认策略，
 * 导致非默认策略（如 A4）的后台回测被"隐藏"。按标签页记住即可。
 */
export function loadSelectedAlgo(): string | null {
  const value = readJson(SELECTED_ALGO_KEY)
  return typeof value === 'string' && value ? value : null
}

export function saveSelectedAlgo(algoCode: string): void {
  writeJson(SELECTED_ALGO_KEY, algoCode)
}
