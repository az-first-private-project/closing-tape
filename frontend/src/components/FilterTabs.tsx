import { motion } from 'framer-motion'
import { cn } from '../lib/utils'

const SOURCES = [
  { id: null, name: '全部' },
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
  return (
    <div className="border-b border-border bg-card">
      <div className="max-w-7xl mx-auto px-4">
        <div className="flex gap-1 py-2 overflow-x-auto">
          {SOURCES.map((source) => (
            <button
              key={source.name}
              onClick={() => onChange(source.id)}
              className={cn(
                'relative px-4 py-2 text-sm font-medium rounded-full transition-colors',
                'hover:text-foreground whitespace-nowrap',
                active === source.id
                  ? 'text-primary-foreground'
                  : 'text-muted-foreground hover:bg-secondary'
              )}
            >
              {/* 滑动指示器背景 */}
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
