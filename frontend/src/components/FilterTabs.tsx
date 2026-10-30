/**
 * 来源筛选标签（契约 2 · /sources）
 * 前端不再写死来源，改为读取后端 /api/v1/sources；
 * 注意：后端 /sources 尚未上线时使用临时兜底列表，接口可用后自动切换为后端数据。
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
    <div className="border-b border-border bg-card">
      <div className="max-w-7xl mx-auto px-4">
        <div className="flex gap-1 py-2 overflow-x-auto scrollbar-thin">
          {tabs.map((source) => (
            <button
              key={source.id ?? 'all'}
              onClick={() => onChange(source.id)}
              className={cn(
                'relative px-4 py-2 text-sm font-medium rounded-full transition-colors',
                'hover:text-foreground whitespace-nowrap',
                active === source.id
                  ? 'text-primary-foreground'
                  : 'text-muted-foreground hover:bg-secondary',
              )}
            >
              {active === source.id && (
                <motion.div
                  layoutId="filter-indicator"
                  className="absolute inset-0 bg-primary rounded-full"
                  transition={{ type: 'spring', stiffness: 500, damping: 30 }}
                />
              )}
              <span className="relative z-10">{source.name}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
