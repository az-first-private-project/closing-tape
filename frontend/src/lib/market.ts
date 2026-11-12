/**
 * 市场温度色带与标签（V7 T1）
 * =============================
 * 阈值是产品语义，不允许在组件里写魔法数字。
 * 区间：0–19 冰点 / 20–39 清冷 / 40–59 平稳 / 60–79 温热 / 80–100 沸腾。
 * 边界值归入较暖的一档（20 → 清冷），与「温度上升」的直觉一致。
 */
import type { RegimeCode } from '../types/market'

export interface TemperatureBand {  /** 该档上限（不含） */
  max: number
  label: string
  /** 文字颜色 class（五个 token 在深浅主题下均 ≥ 4.5:1） */
  textClass: string
  /** CSS 变量，供 SVG / 进度条使用 */
  color: string
}

export const TEMPERATURE_BANDS: TemperatureBand[] = [
  { max: 20, label: '冰点', textClass: 'text-temp-0', color: 'var(--color-temp-0)' },
  { max: 40, label: '清冷', textClass: 'text-temp-1', color: 'var(--color-temp-1)' },
  { max: 60, label: '平稳', textClass: 'text-temp-2', color: 'var(--color-temp-2)' },
  { max: 80, label: '温热', textClass: 'text-temp-3', color: 'var(--color-temp-3)' },
  { max: 100.1, label: '沸腾', textClass: 'text-temp-4', color: 'var(--color-temp-4)' },
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

/** 五态状态色（V7 T1）：上攻暖红 / 修复浅橙 / 筑底浅蓝 / 向下灰蓝 / 避险深灰绿 */
export const REGIME_STYLES: Record<
  RegimeCode,
  { color: string; textClass: string; bgClass: string }
> = {
  trend_up: {
    color: 'var(--color-regime-trend)',
    textClass: 'text-regime-trend',
    bgClass: 'bg-regime-trend',
  },
  repair_up: {
    color: 'var(--color-regime-repair)',
    textClass: 'text-regime-repair',
    bgClass: 'bg-regime-repair',
  },
  bottoming: {
    color: 'var(--color-regime-bottom)',
    textClass: 'text-regime-bottom',
    bgClass: 'bg-regime-bottom',
  },
  pull_down: {
    color: 'var(--color-regime-pull)',
    textClass: 'text-regime-pull',
    bgClass: 'bg-regime-pull',
  },
  risk_off: {
    color: 'var(--color-regime-riskoff)',
    textClass: 'text-regime-riskoff',
    bgClass: 'bg-regime-riskoff',
  },
}
