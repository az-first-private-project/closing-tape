/**
 * 通用格式化工具（V5）
 * ======================
 * 约定：
 *  - 所有函数都接受 null / undefined，返回 "—"；
 *  - 涨跌幅字段自带百分数语义（如 -1.614 表示 -1.614%），展示时直接加 %；
 *  - 金额字段单位是「元」，按 亿/万 自适应；
 *  - 评分字段统一 0–10 分制。
 */

/** 空值占位符 */
export const EMPTY_TEXT = '—'

/** 判断数字是否可用 */
function usable(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

/** 数字：保留 n 位小数（千分位分隔） */
export function fmtNumber(value: number | null | undefined, digits = 2): string {
  if (!usable(value)) return EMPTY_TEXT
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

/** 整数（千分位） */
export function fmtInt(value: number | null | undefined): string {
  if (!usable(value)) return EMPTY_TEXT
  return Math.round(value).toLocaleString('zh-CN')
}

/** 百分数字段：-1.614 → "-1.61%"；带正负号 */
export function fmtPct(value: number | null | undefined, digits = 2): string {
  if (!usable(value)) return EMPTY_TEXT
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(digits)}%`
}

/**
 * 小数比率 → 百分比：0.987 → "98.7%"（用于 coverage / 回测收益率等）
 * @param alreadyPercent 值为已经是百分数时传 true（如 pct_chg）
 */
export function fmtRatio(value: number | null | undefined, digits = 1): string {
  if (!usable(value)) return EMPTY_TEXT
  return `${(value * 100).toFixed(digits)}%`
}

/** 金额（单位：元）按 亿 / 万 自适应 */
export function fmtAmount(value: number | null | undefined): string {
  if (!usable(value)) return EMPTY_TEXT
  const abs = Math.abs(value)
  if (abs >= 1e8) return `${(value / 1e8).toFixed(2)} 亿`
  if (abs >= 1e4) return `${(value / 1e4).toFixed(2)} 万`
  return `${value.toFixed(0)} 元`
}

/** 成交量（单位：股）按 亿股 / 万股 自适应 */
export function fmtVolume(value: number | null | undefined): string {
  if (!usable(value)) return EMPTY_TEXT
  const abs = Math.abs(value)
  if (abs >= 1e8) return `${(value / 1e8).toFixed(2)} 亿股`
  if (abs >= 1e4) return `${(value / 1e4).toFixed(2)} 万股`
  return `${fmtInt(value)} 股`
}

/** 亿元口径金额（融资融券等后端已是亿元） */
export function fmtYi(value: number | null | undefined, digits = 2): string {
  if (!usable(value)) return EMPTY_TEXT
  return `${fmtNumber(value, digits)} 亿`
}

/** 评分（0–10）：保留一位小数；null → 未评分 */
export function fmtScore(value: number | null | undefined, digits = 1): string {
  if (!usable(value)) return '未评分'
  return value.toFixed(digits)
}

/** 评分条宽度：0–10 映射到 0–100%（null → 0） */
export function scorePercent(value: number | null | undefined): number {
  if (!usable(value)) return 0
  return Math.min(100, Math.max(0, (value / 10) * 100))
}

/** 日期时间：ISO → YYYY-MM-DD HH:mm */
export function fmtDateTime(value: string | null | undefined): string {
  if (!value) return EMPTY_TEXT
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** 日期：ISO → YYYY-MM-DD */
export function fmtDate(value: string | null | undefined): string {
  if (!value) return EMPTY_TEXT
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** 耗时：毫秒 → human readable */
export function fmtDuration(ms: number | null | undefined): string {
  if (!usable(ms)) return EMPTY_TEXT
  if (ms < 1000) return `${Math.round(ms)} ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)} s`
  const minutes = Math.floor(ms / 60_000)
  const seconds = Math.round((ms % 60_000) / 1000)
  return `${minutes} 分 ${seconds} 秒`
}

/** 涨跌语义 class（A 股口径：红涨绿跌，0 / 缺失为中性） */
export function pctColor(value: number | null | undefined): string {
  if (!usable(value) || value === 0) return 'text-flat'
  return value > 0 ? 'text-up' : 'text-down'
}

/** 价格/涨跌通用 class（保留旧名，与 pctColor 同一口径） */
export function priceColorClass(value: number | null | undefined): string {
  return pctColor(value)
}

/** SVG / canvas 用颜色变量（CSS 变量，主题切换后由组件重新读取） */
export function colorVar(value: number | null | undefined, strong = false): string {
  if (!usable(value) || value === 0) return 'var(--color-flat)'
  const trend = value > 0 ? 'up' : 'down'
  return strong ? `var(--color-${trend}-strong)` : `var(--color-${trend})`
}

/** 涨跌箭头：颜色之外的第二重语义 */
export function trendGlyph(value: number | null | undefined): '↑' | '↓' | '—' {
  if (!usable(value) || value === 0) return '—'
  return value > 0 ? '↑' : '↓'
}

/** 涨跌标签背景色（热力图/涨跌徽标用，文字用 strong 档保证对比度） */
export function priceBgClass(value: number | null | undefined): string {
  if (!usable(value) || value === 0) return 'bg-muted text-muted-foreground border-border'
  return value > 0
    ? 'bg-up/10 text-up-strong border-up/30'
    : 'bg-down/10 text-down-strong border-down/30'
}

/** 节选日期（MM-DD），用于图表坐标轴 */
export function shortDate(value: string): string {
  return value.length >= 10 ? value.slice(5) : value
}

/** 通用指标值格式化（用于动态 metrics 表格列） */
export function fmtMetricValue(key: string, value: number | string | boolean | null | undefined): string {
  if (value === null || value === undefined) return EMPTY_TEXT
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'string') return value
  if (!usable(value)) return EMPTY_TEXT

  const lower = key.toLowerCase()
  // 成交量类
  if (lower === 'volume' || lower === 'vol_ma5') return fmtVolume(value)
  // 布林带宽是小数比率（0.05 → 5%）
  if (lower === 'boll_width') return fmtRatio(value, 2)
  // 金额/资金流类（*_wan 已是万元口径，直接千分位展示）
  if (/(amount|inflow|outflow|flow|money|main_net|main_t)/.test(lower)) {
    return lower.endsWith('_wan') ? fmtInt(value) : fmtAmount(value)
  }
  // 百分比类（dv_ttm = 股息率，roc10 = 10 日动量，amp60 = 60 日振幅）
  if (/(pct|rate|ratio|div|dv|amplitude|amp|roc|turnover)/.test(lower)) return `${value.toFixed(2)}%`
  // 估值类（PE 为负表示亏损）
  if (/(pe_ttm|pe|pb|ps_ttm|ps|pcf)/.test(lower)) {
    if (value < 0) return '亏损'
    if (value > 1000) return '>1000'
    return value.toFixed(2)
  }
  // 评分类
  if (/_score$/.test(lower)) return value.toFixed(1)
  // 默认：绝对值较大保留 2 位，小值保留 3 位
  if (Math.abs(value) >= 1000) return fmtInt(value)
  if (Math.abs(value) < 1) return value.toFixed(3)
  return value.toFixed(2)
}
