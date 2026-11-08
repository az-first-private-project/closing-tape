/**
 * 全局状态中心（U09）
 * =====================
 * 导航栏铃铛 → 本次会话的异常与瞬时提示流水。
 * 每条包含：时间 / 级别 / 标题 / 原因 / 错误码 / 建议 / 来源路径 / 请求 ID。
 * 仅读取本地 sessionStorage，不新增事件表或事件服务。
 */
import { useEffect, useRef, useState } from 'react'
import {
  Bell,
  CheckCircle2,
  CircleAlert,
  Info,
  Trash2,
  TriangleAlert,
} from 'lucide-react'
import {
  clearStatus,
  listStatus,
  markStatusRead,
  subscribeStatus,
  type StatusEvent,
  type StatusLevel,
} from '../lib/statusFeed'
import { cn } from '../lib/utils'

const LEVEL_META: Record<
  StatusLevel,
  { label: string; icon: typeof Info; className: string }
> = {
  success: { label: '成功', icon: CheckCircle2, className: 'text-success' },
  info: { label: '提示', icon: Info, className: 'text-brass-strong dark:text-brass' },
  warning: { label: '注意', icon: TriangleAlert, className: 'text-warning' },
  error: { label: '错误', icon: CircleAlert, className: 'text-alert' },
}

function formatTime(at: number): string {
  return new Date(at).toLocaleTimeString('zh-CN', { hour12: false })
}

export default function StatusCenter() {
  const [open, setOpen] = useState(false)
  const [events, setEvents] = useState<StatusEvent[]>(() => listStatus())
  const wrapRef = useRef<HTMLDivElement>(null)
  const unread = events.filter((event) => !event.read).length

  useEffect(() => subscribeStatus(() => setEvents(listStatus())), [])

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      if (!wrapRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    window.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  const toggleOpen = () => {
    const next = !open
    setOpen(next)
    if (next) markStatusRead()
  }

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={toggleOpen}
        aria-label={unread > 0 ? `状态中心，${unread} 条未读` : '状态中心'}
        aria-expanded={open}
        aria-controls="status-center-panel"
        title="本次会话的状态与异常记录"
        className="group relative grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-transparent
          text-muted-foreground transition-all duration-300
          hover:border-border hover:bg-secondary/60 hover:text-brass
          focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <Bell className="h-[17px] w-[17px]" />
        {unread > 0 && (
          <span
            aria-hidden="true"
            className="absolute -right-1 -top-1 min-w-[17px] rounded-full border border-background
              bg-alert px-1 text-center text-[11px] font-bold leading-[17px] text-background num"
          >
            {unread > 9 ? '9+' : unread}
          </span>
        )}
      </button>

      {open && (
        <div
          id="status-center-panel"
          role="region"
          aria-label="状态中心"
          className="absolute right-0 top-[calc(100%+8px)] z-[60] max-h-[70vh] w-[min(22rem,calc(100vw-2rem))]
            overflow-y-auto rounded-xl border border-border/80 bg-card/98 p-3 shadow-[var(--shadow-3)]
            backdrop-blur-md scrollbar-thin"
        >
          <div className="flex items-center justify-between gap-2 border-b border-border/60 pb-2">
            <p className="text-[12px] font-semibold tracking-wide text-foreground">本次会话状态</p>
            {events.length > 0 && (
              <button
                type="button"
                onClick={() => clearStatus()}
                className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[12px]
                  text-muted-foreground transition-colors hover:text-alert
                  focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                清空
              </button>
            )}
          </div>

          {events.length === 0 ? (
            <p className="px-1 py-8 text-center text-[12px] text-muted-foreground">
              本次会话暂无异常或提示
            </p>
          ) : (
            <ul className="mt-2 space-y-2">
              {events.map((event) => {
                const meta = LEVEL_META[event.level]
                const Icon = meta.icon
                return (
                  <li
                    key={event.id}
                    className={cn(
                      'rounded-lg border border-border/60 bg-muted/20 p-2.5',
                      !event.read && 'border-brass/40 bg-brass/[0.06]',
                    )}
                  >
                    <div className="flex items-start gap-2">
                      <Icon className={cn('mt-0.5 h-3.5 w-3.5 shrink-0', meta.className)} aria-hidden="true" />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                          <span className="text-[12px] font-medium text-foreground">{event.title}</span>
                          {event.code && (
                            <span className="rounded border border-border/70 bg-background px-1 py-0.5 font-mono text-[11px] text-muted-foreground">
                              {event.code}
                            </span>
                          )}
                          <span className="num ml-auto text-[11px] text-muted-foreground">
                            {formatTime(event.at)}
                          </span>
                        </div>
                        {event.detail && (
                          <p className="mt-1 break-words text-[12px] leading-relaxed text-muted-foreground">
                            {event.detail}
                          </p>
                        )}
                        {event.suggestion && (
                          <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground/80">
                            建议：{event.suggestion}
                          </p>
                        )}
                        <div className="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 font-mono text-[11px] text-muted-foreground/60">
                          {event.path && <span>来源 {event.path}</span>}
                          {event.requestId && <span>ID {event.requestId}</span>}
                        </div>
                      </div>
                    </div>
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
