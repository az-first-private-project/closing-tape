/**
 * Ctrl/Cmd+K 命令面板（V6 T15）
 * =============================
 * 自研，不引 cmdk / fuse.js / downshift。
 * - 股票：复用 /stocks/search，250ms debounce + AbortController（旧请求随 queryKey 切换取消）；
 * - 页面：与 Navbar 共享 NAV_ITEMS；
 * - 策略：复用 /algo/list，首次打开才请求并进 react-query 缓存；
 * - 高亮只按子串切分 React 节点，禁止 dangerouslySetInnerHTML。
 */
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Brain,
  Clock,
  CornerDownLeft,
  FileText,
  Search,
  TrendingUp,
  X,
  type LucideIcon,
} from 'lucide-react'
import { fetchAlgoList } from '../api/algo'
import { searchStocks } from '../api/stock'
import { NAV_ITEMS, PALETTE_OPEN_EVENT } from '../lib/navigation'
import {
  loadPaletteRecent,
  mergeRecent,
  savePaletteRecent,
  splitHighlight,
  PALETTE_RECENT_LIMIT,
  PALETTE_RECENT_VISIBLE,
  type PaletteKind,
  type PaletteRecentEntry,
} from '../lib/palette'
import { cn } from '../lib/utils'

type GroupKey = 'recent' | 'stocks' | 'pages' | 'algos'

const GROUP_LABELS: Record<GroupKey, string> = {
  recent: '最近',
  stocks: '股票',
  pages: '页面',
  algos: '策略',
}

const SEARCH_DEBOUNCE_MS = 250
const STOCK_RESULT_LIMIT = 8

interface PaletteItem {
  key: string
  kind: PaletteKind
  group: GroupKey
  label: string
  sublabel?: string
  path: string
  icon: LucideIcon
}

interface PaletteGroup {
  key: GroupKey
  items: PaletteItem[]
  /** 无结果时的非交互提示（搜索中 / 加载失败） */
  hint?: string
}

function recentIcon(entry: PaletteRecentEntry): LucideIcon {
  if (entry.kind === 'algo') return Brain
  if (entry.kind === 'stock') return TrendingUp
  return NAV_ITEMS.find((item) => item.to === entry.path)?.icon ?? FileText
}

function matches(text: string, query: string): boolean {
  return text.toLowerCase().includes(query.toLowerCase())
}

/** 只按子串切分；React 自动转义文本，查询词不会成为 HTML */
function Highlight({ text, query }: { text: string; query: string }) {
  return (
    <>
      {splitHighlight(text, query).map((part, index) =>
        part.match ? (
          <mark key={index} className="rounded bg-warning/25 px-0.5 text-foreground">
            {part.text}
          </mark>
        ) : (
          <span key={index}>{part.text}</span>
        ),
      )}
    </>
  )
}

export default function CommandPalette() {
  const navigate = useNavigate()
  const inputRef = useRef<HTMLInputElement>(null)
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [composing, setComposing] = useState(false)
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const [recent, setRecent] = useState<PaletteRecentEntry[]>(() => loadPaletteRecent())

  const trimmedQuery = query.trim()

  // 输入法组合期不启动 debounce，结束后再等 250ms
  useEffect(() => {
    if (!open) return
    if (!trimmedQuery) {
      setDebouncedQuery('')
      return
    }
    if (composing) return
    const timer = window.setTimeout(() => setDebouncedQuery(trimmedQuery), SEARCH_DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [open, trimmedQuery, composing])

  const stockQuery = useQuery({
    queryKey: ['palette-stock-search', debouncedQuery],
    queryFn: ({ signal }) => searchStocks(debouncedQuery, STOCK_RESULT_LIMIT, signal),
    enabled: open && debouncedQuery.length > 0,
    staleTime: 60 * 1000,
  })

  const algoQuery = useQuery({
    queryKey: ['algo-list'],
    queryFn: fetchAlgoList,
    enabled: open,
    staleTime: 5 * 60 * 1000,
  })

  const stocksVisible = trimmedQuery.length > 0 && debouncedQuery === trimmedQuery

  const groups = useMemo<PaletteGroup[]>(() => {
    const result: PaletteGroup[] = []

    if (!trimmedQuery) {
      const recentItems = recent
        .slice(0, PALETTE_RECENT_VISIBLE)
        .map((entry, index) => ({
          key: `recent-${entry.kind}-${entry.path}-${index}`,
          kind: entry.kind,
          group: 'recent' as const,
          label: entry.label,
          sublabel: entry.sublabel,
          path: entry.path,
          icon: recentIcon(entry),
        }))
      if (recentItems.length) result.push({ key: 'recent', items: recentItems })
    }

    if (trimmedQuery) {
      const stockItems = stocksVisible
        ? (stockQuery.data?.items ?? []).slice(0, STOCK_RESULT_LIMIT).map((stock) => ({
            key: `stock-${stock.code}`,
            kind: 'stock' as const,
            group: 'stocks' as const,
            label: stock.name,
            sublabel: stock.industry_l1 ? `${stock.code} · ${stock.industry_l1}` : stock.code,
            path: `/stocks/${encodeURIComponent(stock.code)}`,
            icon: TrendingUp,
          }))
        : []
      let hint: string | undefined
      if (stockItems.length === 0) {
        if (!stocksVisible || stockQuery.isFetching) hint = '搜索中…'
        else if (stockQuery.isError) hint = '搜索失败，请稍后重试'
      }
      if (stockItems.length || hint) result.push({ key: 'stocks', items: stockItems, hint })
    }

    const pageItems = NAV_ITEMS
      .filter((item) => !trimmedQuery || matches(
        `${item.label} ${(item.keywords ?? []).join(' ')} ${item.to}`,
        trimmedQuery,
      ))
      .map((item) => ({
        key: `page-${item.to}`,
        kind: 'page' as const,
        group: 'pages' as const,
        label: item.label,
        sublabel: item.to,
        path: item.to,
        icon: item.icon,
      }))
    if (pageItems.length) result.push({ key: 'pages', items: pageItems })

    const algoItems = (algoQuery.data ?? [])
      .filter((algo) => !trimmedQuery || matches(
        `${algo.algo_name} ${algo.algo_code} ${algo.short_desc}`,
        trimmedQuery,
      ))
      .slice(0, STOCK_RESULT_LIMIT)
      .map((algo) => ({
        key: `algo-${algo.algo_code}`,
        kind: 'algo' as const,
        group: 'algos' as const,
        label: algo.algo_name,
        sublabel: `${algo.algo_code} · ${algo.short_desc}`,
        path: `/algo-center?algo=${encodeURIComponent(algo.algo_code)}`,
        icon: Brain,
      }))
    let algoHint: string | undefined
    if (algoItems.length === 0) {
      if (algoQuery.isLoading) algoHint = '策略加载中…'
      else if (algoQuery.isError) algoHint = '策略加载失败'
    }
    if (algoItems.length || algoHint) result.push({ key: 'algos', items: algoItems, hint: algoHint })

    return result
  }, [
    trimmedQuery,
    recent,
    stocksVisible,
    stockQuery.data,
    stockQuery.isFetching,
    stockQuery.isError,
    algoQuery.data,
    algoQuery.isLoading,
    algoQuery.isError,
  ])

  const items = useMemo(() => groups.flatMap((group) => group.items), [groups])
  const indexByKey = useMemo(
    () => new Map(items.map((item, index) => [item.key, index])),
    [items],
  )

  useEffect(() => {
    setActiveIndex(0)
  }, [trimmedQuery])

  useEffect(() => {
    if (activeIndex > items.length - 1) setActiveIndex(0)
  }, [activeIndex, items.length])

  useEffect(() => {
    if (!open) return
    document.getElementById(`palette-item-${activeIndex}`)?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex, open])

  // 打开时记录焦点、锁背景滚动；关闭时还原
  useEffect(() => {
    if (!open) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const previousOverflow = document.body.style.overflow
    setQuery('')
    setComposing(false)
    setDebouncedQuery('')
    setActiveIndex(0)
    setRecent(loadPaletteRecent())
    document.body.style.overflow = 'hidden'
    inputRef.current?.focus()
    return () => {
      document.body.style.overflow = previousOverflow
      if (previous?.isConnected) previous.focus()
    }
  }, [open])

  useEffect(() => {
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (
        !event.repeat
        && !event.shiftKey
        && !event.altKey
        && (event.ctrlKey || event.metaKey)
        && event.key.toLowerCase() === 'k'
      ) {
        event.preventDefault()
        setOpen((value) => !value)
        return
      }
      if (open && event.key === 'Escape' && !event.isComposing) {
        event.preventDefault()
        setOpen(false)
      }
    }
    const openFromNavbar = () => setOpen(true)
    window.addEventListener('keydown', handleKeyDown)
    window.addEventListener(PALETTE_OPEN_EVENT, openFromNavbar)
    return () => {
      window.removeEventListener('keydown', handleKeyDown)
      window.removeEventListener(PALETTE_OPEN_EVENT, openFromNavbar)
    }
  }, [open])

  const close = () => setOpen(false)

  const choose = (item: PaletteItem | undefined) => {
    if (!item) return
    const next = mergeRecent(
      recent,
      {
        kind: item.kind,
        label: item.label,
        sublabel: item.sublabel,
        path: item.path,
      },
      PALETTE_RECENT_LIMIT,
    )
    setRecent(next)
    savePaletteRecent(next)
    setOpen(false)
    navigate(item.path)
  }

  const cycleGroup = (direction: 1 | -1) => {
    const populated = groups.filter((group) => group.items.length > 0)
    if (populated.length === 0) return
    const currentGroup = items[activeIndex]?.group
    const currentIndex = populated.findIndex((group) => group.key === currentGroup)
    const base = currentIndex >= 0
      ? currentIndex
      : direction === 1 ? populated.length - 1 : 0
    const nextGroup = populated[(base + direction + populated.length) % populated.length]
    const nextItemIndex = items.findIndex((item) => item.group === nextGroup.key)
    setActiveIndex(nextItemIndex >= 0 ? nextItemIndex : 0)
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.nativeEvent.isComposing) return

    if (event.key === 'Escape') {
      event.preventDefault()
      close()
      return
    }
    if (event.key === 'Tab') {
      event.preventDefault()
      cycleGroup(event.shiftKey ? -1 : 1)
      return
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (items.length === 0) return
      const delta = event.key === 'ArrowDown' ? 1 : -1
      setActiveIndex((index) => (index + delta + items.length) % items.length)
      return
    }
    if (event.key === 'Enter') {
      event.preventDefault()
      choose(items[activeIndex])
    }
  }

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-[90] flex items-start justify-center bg-background/80 px-4 pt-[12vh] backdrop-blur-sm"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close()
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="命令面板"
        data-command-palette="true"
        className="w-full max-w-xl overflow-hidden rounded-xl border border-border bg-card shadow-2xl"
        onKeyDown={handleKeyDown}
      >
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onCompositionStart={() => setComposing(true)}
            onCompositionEnd={() => setComposing(false)}
            placeholder="搜索股票、策略或页面…"
            role="combobox"
            aria-expanded={true}
            aria-autocomplete="list"
            aria-controls="command-palette-results"
            aria-activedescendant={items[activeIndex] ? `palette-item-${activeIndex}` : undefined}
            data-palette-input="true"
            className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
          {query && (
            <button
              type="button"
              onClick={() => {
                setQuery('')
                inputRef.current?.focus()
              }}
              aria-label="清空搜索"
              className="rounded-md p-1 text-muted-foreground hover:bg-secondary hover:text-foreground"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
          <kbd className="hidden rounded border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground sm:inline">
            Esc
          </kbd>
        </div>

        <div
          id="command-palette-results"
          role="listbox"
          aria-label="命令面板结果"
          className="max-h-[56vh] overflow-y-auto p-2"
        >
          {groups.map((group) => (
            <section key={group.key} role="group" aria-labelledby={`palette-group-${group.key}`}>
              <div
                id={`palette-group-${group.key}`}
                className="flex items-center gap-1 px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground"
              >
                {group.key === 'recent' && <Clock className="h-3 w-3" />}
                {GROUP_LABELS[group.key]}
              </div>
              {group.items.map((item) => {
                const flatIndex = indexByKey.get(item.key) ?? 0
                const Icon = item.icon
                const isActive = flatIndex === activeIndex
                return (
                  <button
                    key={item.key}
                    id={`palette-item-${flatIndex}`}
                    type="button"
                    role="option"
                    aria-selected={isActive}
                    data-palette-item={item.kind}
                    onMouseEnter={() => setActiveIndex(flatIndex)}
                    onClick={() => choose(item)}
                    className={cn(
                      'flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left transition-colors',
                      isActive ? 'bg-secondary text-foreground' : 'text-muted-foreground hover:bg-secondary/60',
                    )}
                  >
                    <Icon className="h-4 w-4 shrink-0" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm">
                        <Highlight text={item.label} query={group.key === 'recent' ? '' : trimmedQuery} />
                      </span>
                      {item.sublabel && (
                        <span className="block truncate text-[11px] text-muted-foreground">
                          <Highlight text={item.sublabel} query={group.key === 'recent' ? '' : trimmedQuery} />
                        </span>
                      )}
                    </span>
                    {isActive && <CornerDownLeft className="h-3.5 w-3.5 shrink-0" />}
                  </button>
                )
              })}
              {group.hint && group.items.length === 0 && (
                <p className="px-3 py-2 text-xs text-muted-foreground">{group.hint}</p>
              )}
            </section>
          ))}

          {trimmedQuery && groups.length === 0 && (
            <p className="px-3 py-6 text-center text-sm text-muted-foreground">
              没有找到「{trimmedQuery}」相关结果
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border px-4 py-2 text-[10px] text-muted-foreground">
          <span><kbd className="rounded border border-border px-1">↑</kbd> <kbd className="rounded border border-border px-1">↓</kbd> 选择</span>
          <span><kbd className="rounded border border-border px-1">Tab</kbd> 切换分组</span>
          <span><kbd className="rounded border border-border px-1">↵</kbd> 打开</span>
          <span><kbd className="rounded border border-border px-1">Esc</kbd> 关闭</span>
          <span className="ml-auto hidden sm:inline">Ctrl/⌘ K 随时唤起</span>
        </div>
      </div>
    </div>
  )
}
