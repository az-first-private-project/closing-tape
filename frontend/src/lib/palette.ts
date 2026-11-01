/**
 * 命令面板纯逻辑（V6 T15）
 * -------------------------
 * 最近记录与高亮切分不依赖 React / DOM，便于 Node 单测；
 * localStorage 访问全部包裹 try/catch，隐私模式下降级为空列表。
 */
export type PaletteKind = 'page' | 'stock' | 'algo'

export interface PaletteRecentEntry {
  kind: PaletteKind
  label: string
  sublabel?: string
  path: string
}

export interface HighlightPart {
  text: string
  match: boolean
}

export const PALETTE_RECENT_KEY = 'gfna-palette-recent'
export const PALETTE_RECENT_LIMIT = 10
export const PALETTE_RECENT_VISIBLE = 3

interface StorageReader {
  getItem(key: string): string | null
}

interface StorageWriter {
  setItem(key: string, value: string): void
}

const KINDS = new Set<PaletteKind>(['page', 'stock', 'algo'])

function isRecentEntry(value: unknown): value is PaletteRecentEntry {
  if (typeof value !== 'object' || value === null) return false
  const entry = value as Record<string, unknown>
  return (
    typeof entry.kind === 'string'
    && KINDS.has(entry.kind as PaletteKind)
    && typeof entry.label === 'string'
    && entry.label.trim().length > 0
    && typeof entry.path === 'string'
    && entry.path.trim().length > 0
    && (entry.sublabel === undefined || typeof entry.sublabel === 'string')
  )
}

/** 去重（kind + path）后插入最前，并截断到上限 */
export function mergeRecent(
  current: PaletteRecentEntry[],
  entry: PaletteRecentEntry,
  limit = PALETTE_RECENT_LIMIT,
): PaletteRecentEntry[] {
  const rest = current.filter(
    (item) => !(item.kind === entry.kind && item.path === entry.path),
  )
  return [entry, ...rest].slice(0, Math.max(1, limit))
}

/** 读取最近跳转；非法 JSON / 无效条目 / localStorage 不可用时返回空数组 */
export function loadPaletteRecent(storage?: StorageReader): PaletteRecentEntry[] {
  try {
    const raw = (storage ?? window.localStorage).getItem(PALETTE_RECENT_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    const seen = new Set<string>()
    return parsed.filter(isRecentEntry).filter((entry) => {
      const key = `${entry.kind}:${entry.path}`
      if (seen.has(key)) return false
      seen.add(key)
      return true
    }).slice(0, PALETTE_RECENT_LIMIT)
  } catch {
    return []
  }
}

/** 覆盖写入最近跳转；隐私模式 / 配额不足时静默忽略 */
export function savePaletteRecent(
  entries: PaletteRecentEntry[],
  storage?: StorageWriter,
): void {
  try {
    (storage ?? window.localStorage).setItem(
      PALETTE_RECENT_KEY,
      JSON.stringify(entries.slice(0, PALETTE_RECENT_LIMIT)),
    )
  } catch {
    // localStorage 不可用不应影响跳转
  }
}

/**
 * 按子串切分高亮片段（大小写不敏感，保留原文大小写）。
 * 返回结构化片段，由 React 节点渲染，禁止 dangerouslySetInnerHTML。
 */
export function splitHighlight(text: string, query: string): HighlightPart[] {
  const needle = query.trim()
  if (!needle) return [{ text, match: false }]
  if (!text) return []

  const haystackLower = text.toLowerCase()
  const needleLower = needle.toLowerCase()
  const parts: HighlightPart[] = []
  let cursor = 0
  let hit = haystackLower.indexOf(needleLower, cursor)

  while (hit !== -1) {
    if (hit > cursor) parts.push({ text: text.slice(cursor, hit), match: false })
    parts.push({ text: text.slice(hit, hit + needle.length), match: true })
    cursor = hit + needle.length
    hit = haystackLower.indexOf(needleLower, cursor)
  }

  if (cursor < text.length) parts.push({ text: text.slice(cursor), match: false })
  return parts
}
