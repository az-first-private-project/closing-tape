/**
 * 自选切换按钮（V6 T8）
 * ======================
 * 名单以服务端单租户状态为准，localStorage 仅缓存；切换后通过 store 事件同步到导航栏，
 * 并异步同步到服务器。本机缓存不可用时仍会写服务端，按钮下方给出提示。
 */
import { useState } from 'react'
import { Star } from 'lucide-react'
import { useWatchlist } from '../hooks/useWatchSignals'
import { MAX_WATCHLIST, toggleWatch } from '../lib/watchlist'
import { cn } from '../lib/utils'

interface Props {
  code: string
  name?: string
  /** 是否显示「自选 / 已自选」文字（默认 false，仅星标） */
  showLabel?: boolean
  /** md 用于个股详情页头部 */
  size?: 'sm' | 'md'
  className?: string
}

export default function WatchButton({
  code,
  name,
  showLabel = false,
  size = 'sm',
  className,
}: Props) {
  const watchlist = useWatchlist()
  const watched = watchlist.includes(code)
  const [hint, setHint] = useState<string | null>(null)
  const label = name ?? code

  const handleToggle = (event: React.MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation()
    const result = toggleWatch(code)
    if (result.reason === 'limit') {
      setHint(`自选上限 ${MAX_WATCHLIST} 只，请先在自选页移除`)
    } else if (result.reason === 'storage') {
      setHint('本浏览器缓存不可写，改动将同步到服务器')
    } else {
      setHint(null)
    }
  }

  return (
    <span className={cn('relative inline-flex', className)}>
      <button
        type="button"
        onClick={handleToggle}
        aria-pressed={watched}
        aria-label={watched ? `取消自选 ${label}` : `加入自选 ${label}`}
        title={watched ? `取消自选 ${label}` : `加入自选 ${label}`}
        className={cn(
          'inline-flex items-center gap-1 rounded-md border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          size === 'md' ? 'px-3 py-2 text-sm' : 'px-1.5 py-0.5 text-[11px]',
          watched
            ? 'border-warning/50 bg-warning/10 text-warning'
            : 'border-border text-muted-foreground hover:bg-muted hover:text-foreground',
        )}
      >
        <Star
          className={cn(
            watched && 'fill-current',
            size === 'md' ? 'w-4 h-4' : 'w-3.5 h-3.5',
          )}
        />
        {showLabel && <span>{watched ? '已自选' : '加入自选'}</span>}
      </button>
      {hint && (
        <span
          role="status"
          className="absolute left-0 top-full z-20 mt-1 whitespace-nowrap rounded-md border border-warning/40 bg-card px-2 py-1 text-[10px] text-warning shadow-lg"
        >
          {hint}
        </span>
      )}
    </span>
  )
}
