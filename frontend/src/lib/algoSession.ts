/**
 * 选股中心会话状态（缺陷 2）
 * ==========================
 * - 结果上下文：当前策略的参数面板、交易日、正在展示的 run（按策略分别保存）；
 * - 表格状态：按 run_id 保存排序 / 展开行 / 选中行 / 表格滚动 / 页面滚动；
 * - URL 序列化：algo、date、run 与 p.<参数> 可在刷新、分享、前进后退时完整还原；
 * - 全部使用 sessionStorage（仅当前标签页），隐私模式禁用存储时安全降级为 no-op，
 *   关闭标签后由完整 URL 继续还原结果。
 */
import type { AlgoMeta, AlgoParams } from '../types/algo'

export interface AlgoTableSort {
  key: string
  direction: 'asc' | 'desc'
}

export interface StoredAlgoTableState {
  sort: AlgoTableSort | null
  expandedKey: string | null
  activeKey: string | null
  scrollTop: number
  pageScrollY: number
  savedAt: number
}

export interface StoredAlgoContext {
  algoCode: string
  params: AlgoParams
  selectedDate: string
  runId: string | null
  savedAt: number
}

export interface AlgoUrlState {
  algoCode: string | null
  runId: string | null
  /** null = URL 未指定；'' = 自动交易日；YYYY-MM-DD = 指定交易日 */
  selectedDate: string | null
  /** URL 里没有 p.* 时为 null */
  params: AlgoParams | null
}

const CONTEXT_PREFIX = 'gfna:algo:context:v1:'
const TABLE_PREFIX = 'gfna:algo:table:v1:'
export const ALGO_PARAM_PREFIX = 'p.'

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage
  } catch {
    return null
  }
}

function readJson(key: string): unknown {
  try {
    const raw = storage()?.getItem(key)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    storage()?.setItem(key, JSON.stringify(value))
  } catch {
    // 配额/隐私模式：持久化失败不影响页面功能
  }
}

function toFiniteNumber(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function sanitizeParams(value: unknown): AlgoParams {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  const result: AlgoParams = {}
  Object.entries(value as Record<string, unknown>).forEach(([key, raw]) => {
    if (typeof raw === 'string' || (typeof raw === 'number' && Number.isFinite(raw))) {
      result[key] = raw
    }
  })
  return result
}

function sanitizeSort(value: unknown): AlgoTableSort | null {
  if (!value || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  if (typeof record.key !== 'string' || !record.key) return null
  if (record.direction !== 'asc' && record.direction !== 'desc') return null
  return { key: record.key, direction: record.direction }
}

/** 读取某策略的会话上下文 */
export function loadAlgoContext(algoCode: string): StoredAlgoContext | null {
  if (!algoCode) return null
  const raw = readJson(CONTEXT_PREFIX + algoCode)
  if (!raw || typeof raw !== 'object') return null
  const record = raw as Record<string, unknown>
  if (record.algoCode !== algoCode) return null
  return {
    algoCode,
    params: sanitizeParams(record.params),
    selectedDate: typeof record.selectedDate === 'string' ? record.selectedDate : '',
    runId: typeof record.runId === 'string' && record.runId ? record.runId : null,
    savedAt: toFiniteNumber(record.savedAt),
  }
}

/** 保存某策略的会话上下文（结果 run_id 与参数面板快照） */
export function saveAlgoContext(context: Omit<StoredAlgoContext, 'savedAt'>): void {
  if (!context.algoCode) return
  writeJson(CONTEXT_PREFIX + context.algoCode, { ...context, savedAt: Date.now() })
}

/** 读取某次 run 的表格状态 */
export function loadAlgoTableState(runId: string): StoredAlgoTableState | null {
  if (!runId) return null
  const raw = readJson(TABLE_PREFIX + runId)
  if (!raw || typeof raw !== 'object') return null
  const record = raw as Record<string, unknown>
  return {
    sort: sanitizeSort(record.sort),
    expandedKey: typeof record.expandedKey === 'string' ? record.expandedKey : null,
    activeKey: typeof record.activeKey === 'string' ? record.activeKey : null,
    scrollTop: Math.max(0, toFiniteNumber(record.scrollTop)),
    pageScrollY: Math.max(0, toFiniteNumber(record.pageScrollY)),
    savedAt: toFiniteNumber(record.savedAt),
  }
}

/** 保存某次 run 的表格状态 */
export function saveAlgoTableState(
  runId: string,
  state: Omit<StoredAlgoTableState, 'savedAt'>,
): void {
  if (!runId) return
  writeJson(TABLE_PREFIX + runId, { ...state, savedAt: Date.now() })
}

/** 解析 URL 中的选股条件；非法 date 视为未指定 */
export function readAlgoUrlState(search: URLSearchParams): AlgoUrlState {
  const algoCode = search.get('algo')?.trim() || null
  const runId = search.get('run')?.trim() || null
  const rawDate = search.get('date')
  let selectedDate: string | null = null
  if (rawDate === 'auto' || rawDate === '') selectedDate = ''
  else if (rawDate && /^\d{4}-\d{2}-\d{2}$/.test(rawDate)) selectedDate = rawDate

  let params: AlgoParams | null = null
  search.forEach((value, key) => {
    if (!key.startsWith(ALGO_PARAM_PREFIX)) return
    const paramKey = key.slice(ALGO_PARAM_PREFIX.length)
    if (!paramKey) return
    params ??= {}
    params[paramKey] = value
  })

  return { algoCode, runId, selectedDate, params }
}

/**
 * 生成选股中心 URL。
 * 格式：?algo=<策略>&date=<auto|YYYY-MM-DD>&run=<run_id>&p.<参数>=<值>
 * - date 省略 = 自动；run 省略 = 当前无结果；
 * - 参数按 params_schema 顺序全量写入（缺省回落默认值），保证条件可分享、可复原。
 */
export function buildAlgoUrlSearch(input: {
  algoCode: string
  selectedDate: string
  runId: string | null
  params: AlgoParams
  algo: AlgoMeta | null
}): URLSearchParams {
  const search = new URLSearchParams()
  if (input.algoCode) search.set('algo', input.algoCode)
  if (input.selectedDate) search.set('date', input.selectedDate)
  if (input.runId) search.set('run', input.runId)
  input.algo?.params_schema.forEach((def) => {
    const value = input.params[def.key] !== undefined ? input.params[def.key] : def.default
    if (value === null || value === undefined) return
    const text = String(value).trim()
    if (!text) return
    search.set(`${ALGO_PARAM_PREFIX}${def.key}`, text)
  })
  return search
}
