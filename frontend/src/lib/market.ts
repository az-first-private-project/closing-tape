/**
 * 市场温度色带与标签（V6 T3）
 * =============================
 * 阈值是产品语义，不允许在组件里写魔法数字。
 * 区间：0–20 极冷 / 20–40 偏冷 / 40–60 中性 / 60–80 偏热 / 80–100 极热。
 * 边界值归入较暖的一档（20 → 偏冷），与「温度上升」的直觉一致。
 */
export interface TemperatureBand {
  /** 该档上限（不含） */
  max: number
  label: string
  /** 文字颜色 class（五个 token 在深浅主题下均 ≥ 4.5:1） */
  textClass: string
  /** CSS 变量，供 SVG / 进度条使用 */
  color: string
}

export const TEMPERATURE_BANDS: TemperatureBand[] = [
  { max: 20, label: '极冷', textClass: 'text-temp-0', color: 'var(--color-temp-0)' },
  { max: 40, label: '偏冷', textClass: 'text-temp-1', color: 'var(--color-temp-1)' },
  { max: 60, label: '中性', textClass: 'text-temp-2', color: 'var(--color-temp-2)' },
  { max: 80, label: '偏热', textClass: 'text-temp-3', color: 'var(--color-temp-3)' },
  { max: 100.1, label: '极热', textClass: 'text-temp-4', color: 'var(--color-temp-4)' },
]

/** 温度分 → 色带；null（数据不足）→ null */
export function temperatureBand(score: number | null | undefined): TemperatureBand | null {
  if (typeof score !== 'number' || !Number.isFinite(score)) return null
  const clamped = Math.min(100, Math.max(0, score))
  return TEMPERATURE_BANDS.find((band) => clamped < band.max) ?? TEMPERATURE_BANDS[TEMPERATURE_BANDS.length - 1]
}

/** 温度分 → 中文档位；null → 数据不足 */
export function temperatureLabel(score: number | null | undefined): string {
  return temperatureBand(score)?.label ?? '数据不足'
}

/** 温度分 → CSS 颜色（SVG / 进度条） */
export function temperatureColor(score: number | null | undefined): string {
  return temperatureBand(score)?.color ?? 'var(--color-flat)'
}
