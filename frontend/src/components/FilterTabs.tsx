/**
 * 来源筛选标签（契约 2 · /sources）
 * 前端不再写死来源，改为读取后端 /api/v1/sources；
 * 注意：后端 /sources 尚未上线时使用临时兜底列表，接口可用后自动切换为后端数据。
 *
 * 视觉：编辑部的「索引标签」——标签文字 + 铜色滑动下划线，
 * 比整块实心药丸更克制，也不会和数据红绿抢色。
 */
import { motion } from 'framer-motion'
import { useSources } from '../hooks/useArticles'
import { cn } from '../lib/utils'

/** 后端 /sources 未实现时的临时兜底（V2 数据源 id 约定），接口上线后自动弃用 */
const FALLBACK_SOURCES = [
  { id: 1, name: 'Reuters' },
  { id: 2, name: 'Bloomberg' },
  { id: 3, name: 'CNBC' },
  { id: 4, name: 'MarketWatch' },
]

interface FilterTabsProps {
  active: number | null
  onChange: (sourceId: number | null) => void
}

export default function FilterTabs({ active, onChange }: FilterTabsProps) {
  const { data: sources } = useSources()

  const available = sources && sources.length > 0 ? sources : FALLBACK_SOURCES
  const tabs: { id: number | null; name: string }[] = [
    { id: null, name: '全部' },
    ...available.map((source) => ({ id: source.id, name: source.name })),
  ]

  return (
    <div className="border-b border-border/70">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div className="fade-right flex items-stretch gap-1 overflow-x-auto scrollbar-thin">
          <span className="hidden shrink-0 items-center pr-3 font-mono text-[12px] tracking-[0.18em] text-muted-foreground/70 sm:flex">
            SOURCE
          </span>

          {tabs.map((source) => {
            const isActive = active === source.id
            return (
              <button
                key={source.id ?? 'all'}
                onClick={() => onChange(source.id)}
                aria-pressed={isActive}
                className={cn(
                  'relative shrink-0 whitespace-nowrap px-3.5 py-3 text-[13px] font-medium',
                  'transition-colors duration-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  isActive ? 'text-foreground' : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {source.name}
                {isActive && (
                  <motion.span
                    layoutId="filter-indicator"
                    aria-hidden="true"
                    className="absolute inset-x-2.5 -bottom-px h-[2px] rounded-full bg-brass"
                    transition={{ type: 'spring', stiffness: 460, damping: 34 }}
                  />
                )}
              </button>
            )
          })}
        </div>
      </div>
    </div>
  )
}
