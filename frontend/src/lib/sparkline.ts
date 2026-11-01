/**
 * Sparkline 几何（V6 T14，纯函数，便于单测）
 * ==========================================
 * 约定：
 *  - `values` 中的 null / NaN / Infinity 一律视为缺数据，折线在缺数据处断开；
 *  - 最多保留最后 `days` 个点；仍超过 200 点时等距降采样，首尾点必保留；
 *  - 0 或 1 个有效点渲染为灰色平线（数据不足），不抛错、不画 NaN 坐标；
 *  - 全等值渲染为平线；显式 domain 时按 domain 映射，否则自动留 8% 纵向余量。
 */

export const SPARKLINE_MAX_POINTS = 200

export interface SparklineGeometryInput {
  values: (number | null)[] | null | undefined
  days?: number
  width?: number
  height?: number
  padding?: number
  domain?: [number, number]
  baseline?: number | null
}

export interface SparklinePoint {
  x: number
  y: number
}

export interface SparklineGeometry {
  width: number
  height: number
  /** 采样后的点数（包含缺数据点） */
  points: number
  validCount: number
  missingCount: number
  /** 按 null 断点拆分后的折线段；0/1 个有效点是横贯全宽的平线 */
  segments: SparklinePoint[][]
  baselineY: number | null
  min: number | null
  max: number | null
  first: number | null
  last: number | null
  flat: boolean
  dataInsufficient: boolean
}

function cleanValue(value: number | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** 截取最后 days 个点；超过 200 点时等距降采样并保留首尾 */
export function sampleSparklineValues(
  values: (number | null)[] | null | undefined,
  days = 30,
): (number | null)[] {
  const source = Array.isArray(values) ? values.map(cleanValue) : []
  const limit = Math.max(1, Math.floor(days))
  const sliced = source.length > limit ? source.slice(-limit) : source
  if (sliced.length <= SPARKLINE_MAX_POINTS) return sliced

  const sampled: (number | null)[] = []
  const step = (sliced.length - 1) / (SPARKLINE_MAX_POINTS - 1)
  let previous = -1
  for (let index = 0; index < SPARKLINE_MAX_POINTS; index += 1) {
    const sourceIndex = Math.round(index * step)
    if (sourceIndex === previous) continue
    sampled.push(sliced[sourceIndex])
    previous = sourceIndex
  }
  return sampled
}

export function buildSparklineGeometry({
  values,
  days = 30,
  width = 120,
  height = 32,
  padding = 2,
  domain,
  baseline = null,
}: SparklineGeometryInput): SparklineGeometry {
  const safeWidth = Math.max(1, width)
  const safeHeight = Math.max(1, height)
  const safePadding = Math.min(Math.max(0, padding), (safeHeight - 1) / 2)
  const sampled = sampleSparklineValues(values, days)
  const valid = sampled.filter((value): value is number => value !== null)

  const first = valid.length > 0 ? valid[0] : null
  const last = valid.length > 0 ? valid[valid.length - 1] : null
  const dataMin = valid.length > 0 ? Math.min(...valid) : null
  const dataMax = valid.length > 0 ? Math.max(...valid) : null

  let domainMin: number
  let domainMax: number
  if (domain) {
    domainMin = Math.min(domain[0], domain[1])
    domainMax = Math.max(domain[0], domain[1])
  } else if (dataMin === null || dataMax === null) {
    domainMin = 0
    domainMax = 1
  } else if (dataMin === dataMax) {
    const pad = dataMin === 0 ? 1 : Math.abs(dataMin) * 0.05
    domainMin = dataMin - pad
    domainMax = dataMax + pad
  } else {
    const pad = (dataMax - dataMin) * 0.08
    domainMin = dataMin - pad
    domainMax = dataMax + pad
  }
  if (!(domainMax > domainMin)) {
    domainMin -= 1
    domainMax += 1
  }

  const toY = (value: number) => {
    const ratio = (value - domainMin) / (domainMax - domainMin)
    const clamped = Math.min(1, Math.max(0, ratio))
    return safePadding + (1 - clamped) * (safeHeight - safePadding * 2)
  }

  const total = sampled.length
  const x = (index: number) => {
    if (total <= 1) return safeWidth / 2
    return (index / (total - 1)) * safeWidth
  }

  const dataInsufficient = valid.length <= 1
  const flat = dataInsufficient || (dataMin !== null && dataMax !== null && dataMin === dataMax)

  let segments: SparklinePoint[][]
  if (valid.length === 0) {
    const mid = safeHeight / 2
    segments = [[{ x: 0, y: mid }, { x: safeWidth, y: mid }]]
  } else if (valid.length === 1) {
    const y = toY(valid[0])
    segments = [[{ x: 0, y }, { x: safeWidth, y }]]
  } else {
    segments = []
    let current: SparklinePoint[] = []
    sampled.forEach((value, index) => {
      if (value === null) {
        if (current.length > 0) segments.push(current)
        current = []
        return
      }
      current.push({ x: x(index), y: toY(value) })
    })
    if (current.length > 0) segments.push(current)
  }

  const baselineY =
    baseline !== null && Number.isFinite(baseline) && baseline >= domainMin && baseline <= domainMax
      ? toY(baseline)
      : null

  return {
    width: safeWidth,
    height: safeHeight,
    points: total,
    validCount: valid.length,
    missingCount: total - valid.length,
    segments,
    baselineY,
    min: dataMin,
    max: dataMax,
    first,
    last,
    flat,
    dataInsufficient,
  }
}
