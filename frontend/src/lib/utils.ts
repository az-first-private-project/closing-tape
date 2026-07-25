/** 合并 CSS 类名，过滤掉假值 */
export function cn(...classes: (string | boolean | undefined | null)[]): string {
  return classes.filter(Boolean).join(' ')
}

/** 格式化时间字符串为易读格式 */
export function formatTime(timeStr: string | null): string {
  if (!timeStr) return '时间未知'
  try {
    const date = new Date(timeStr)
    if (isNaN(date.getTime())) return timeStr
    const y = date.getFullYear()
    const m = String(date.getMonth() + 1).padStart(2, '0')
    const d = String(date.getDate()).padStart(2, '0')
    const h = String(date.getHours()).padStart(2, '0')
    const min = String(date.getMinutes()).padStart(2, '0')
    return `${y}-${m}-${d} ${h}:${min}`
  } catch {
    return timeStr
  }
}

/** 来源配色 */
export const SOURCE_COLORS: Record<string, string> = {
  Reuters: '#ff6b35',
  Bloomberg: '#1c1c1c',
  CNBC: '#0066cc',
  MarketWatch: '#2d8f56',
}

export function getSourceColor(name: string): string {
  return SOURCE_COLORS[name] || '#1a73e8'
}
