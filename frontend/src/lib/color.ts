/**
 * 涨跌色阶与图表系列色
 * ======================
 * 热力图（本任务）与 T4 树图共用：
 *  - 色阶用 CSS color-mix() 从语义 token 插值，主题切换时浏览器自动重算；
 *  - canvas 场景不能直接吃 color-mix()，请用 format.ts 的 colorVar() 取计算后的变量值；
 *  - 色块文字统一用 foreground，保证深/浅主题对比度 ≥ 4.5:1。
 */

/** 色阶饱和点：|涨跌幅| ≥ 8% 视为最热/最冷 */
export const HEAT_MAX_ABS = 8

/** 色阶最小/最大混色比例（百分比） */
const HEAT_MIN_WEIGHT = 8
const HEAT_MAX_WEIGHT = 48

export interface HeatStyle {
  background: string
  color: string
}

/**
 * 涨跌幅 → 背景/文字颜色（连续插值，替代固定档位）
 * @param pct 涨跌幅百分数（-1.61 表示 -1.61%）
 * @param maxAbs 饱和点，默认 8%
 */
export function trendHeat(pct: number | null | undefined, maxAbs = HEAT_MAX_ABS): HeatStyle {
  if (typeof pct !== 'number' || !Number.isFinite(pct)) {
    return { background: 'var(--color-muted)', color: 'var(--color-muted-foreground)' }
  }
  if (pct === 0) {
    return { background: 'var(--color-muted)', color: 'var(--color-foreground)' }
  }
  const intensity = Math.min(1, Math.abs(pct) / maxAbs)
  const weight = Math.round(HEAT_MIN_WEIGHT + intensity * (HEAT_MAX_WEIGHT - HEAT_MIN_WEIGHT))
  const trend = pct > 0 ? 'up' : 'down'
  return {
    background: `color-mix(in srgb, var(--color-${trend}) ${weight}%, var(--color-card))`,
    color: 'var(--color-foreground)',
  }
}

/** 图表系列色（非涨跌语义，按主题取色） */
export const CHART_PALETTE = [
  'var(--color-chart-1)',
  'var(--color-chart-2)',
  'var(--color-chart-3)',
  'var(--color-chart-4)',
  'var(--color-chart-5)',
] as const

export function chartColor(index: number): string {
  return CHART_PALETTE[index % CHART_PALETTE.length]
}
