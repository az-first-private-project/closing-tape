/**
 * 参数面板与 run 快照的比对（U01）
 * ================================
 * 参数面板可能包含未提交的修改；结果、导出与信号依据必须使用 run.params 快照。
 * 这里按 params_schema 归一化两侧取值，只比较策略可调参数（含 limit）。
 */
import type { AlgoMeta, AlgoParams, AlgoParamsSnapshot } from '../types/algo'

function toComparable(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  return String(value)
}

/** 参数面板当前值（缺省回落 schema 默认值）→ 可比较字符串 */
export function panelParamsForCompare(
  algo: AlgoMeta | null,
  values: AlgoParams,
): Record<string, string> {
  const result: Record<string, string> = {}
  algo?.params_schema.forEach((def) => {
    const value = values[def.key] !== undefined ? values[def.key] : def.default
    if (value === null || value === undefined) return
    result[def.key] = toComparable(value)
  })
  return result
}

/** run 参数快照 → 可比较字符串（只看 schema 内参数） */
export function runParamsForCompare(
  algo: AlgoMeta | null,
  snapshot: AlgoParamsSnapshot | null | undefined,
): Record<string, string> {
  const result: Record<string, string> = {}
  algo?.params_schema.forEach((def) => {
    if (!snapshot || !(def.key in snapshot)) return
    result[def.key] = toComparable(snapshot[def.key])
  })
  return result
}

export interface PanelDiff {
  dirty: boolean
  /** 与当前展示 run 快照不一致的参数 key */
  changedKeys: string[]
  /** 显式指定交易日与展示结果交易日不一致 */
  dateMismatch: boolean
}

/** 比较参数面板与当前展示的 run；无 run 时视为不脏 */
export function diffPanelAgainstRun(
  algo: AlgoMeta | null,
  values: AlgoParams,
  run: { params: AlgoParamsSnapshot } | null | undefined,
  selectedDate: string,
  resultTradeDate: string | null | undefined,
): PanelDiff {
  if (!run) return { dirty: false, changedKeys: [], dateMismatch: false }
  const panel = panelParamsForCompare(algo, values)
  const snapshot = runParamsForCompare(algo, run.params)
  const keys = new Set([...Object.keys(panel), ...Object.keys(snapshot)])
  const changedKeys = [...keys].filter((key) => panel[key] !== snapshot[key])
  const dateMismatch = !!selectedDate && !!resultTradeDate && selectedDate !== resultTradeDate
  return { dirty: changedKeys.length > 0 || dateMismatch, changedKeys, dateMismatch }
}
