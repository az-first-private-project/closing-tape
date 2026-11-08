/**
 * 统一三态视图：Loading / Error / Empty
 * ========================================
 * 错误态会展示后端统一错误体的 code 与 message（契约 0.2），
 * 便于定位「数据未就绪 / 数据质量 / 参数错误」等原因。
 *
 * 视觉：三态共用一套「仪表盘占位」语言——发丝线框、细条纹骨架、
 * 居中的圆形图标座，避免整页出现大块纯灰。
 */
import type { ReactNode } from 'react'
import { AlertCircle, Inbox, RefreshCw } from 'lucide-react'
import { ApiError } from '../api/client'
import { useDelayedFlag } from '../hooks/useDelayedFlag'

/** 骨架线 */
export function SkeletonLine({ className = 'h-3 w-full' }: { className?: string }) {
  return <div className={`skeleton ${className}`} aria-hidden="true" />
}

/** 卡片骨架 */
export function SkeletonCard({ lines = 3, className = '' }: { lines?: number; className?: string }) {
  return (
    <div className={`card p-4 space-y-3 ${className}`} aria-hidden="true">
      <SkeletonLine className="h-4 w-1/3" />
      {Array.from({ length: lines }).map((_, index) => (
        <SkeletonLine
          key={index}
          className={index === lines - 1 ? 'h-3 w-2/3' : 'h-3 w-full'}
        />
      ))}
    </div>
  )
}

/** 表格骨架 */
export function SkeletonTable({
  rows = 6,
  cols = 5,
  className = '',
}: {
  rows?: number
  cols?: number
  className?: string
}) {
  const gridStyle = { gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }
  return (
    <div className={`card overflow-hidden ${className}`} aria-hidden="true">
      <div className="flex items-center justify-between gap-2 border-b border-border/70 px-4 py-2.5">
        <SkeletonLine className="h-3 w-16" />
        <SkeletonLine className="h-3 w-12" />
      </div>
      <div>
        <div className="grid gap-3 border-b border-border/60 bg-muted/30 px-4 py-2.5" style={gridStyle}>
          {Array.from({ length: cols }).map((_, index) => (
            <SkeletonLine key={index} className="h-2.5 w-2/3" />
          ))}
        </div>
        {Array.from({ length: rows }).map((_, row) => (
          <div
            key={row}
            className="grid gap-3 border-b border-border/40 px-4 py-3 last:border-0"
            style={gridStyle}
          >
            {Array.from({ length: cols }).map((_, col) => (
              <SkeletonLine key={col} className={col === 0 ? 'h-3 w-4/5' : 'h-3 w-1/2'} />
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

/** 图表骨架 */
export function SkeletonChart({
  height = 240,
  className = '',
}: {
  height?: number
  className?: string
}) {
  return (
    <div className={`card p-4 ${className}`} aria-hidden="true">
      <SkeletonLine className="mb-3 h-4 w-32" />
      <div className="skeleton w-full" style={{ height }} />
    </div>
  )
}

/** KPI 指标卡骨架 */
export function SkeletonKpiRow({ count = 6, className = '' }: { count?: number; className?: string }) {
  const gridClass =
    count <= 4
      ? 'grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2.5'
      : 'grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2.5'
  return (
    <div className={`${gridClass} ${className}`} aria-hidden="true">
      {Array.from({ length: count }).map((_, index) => (
        <div key={index} className="card space-y-2.5 p-3.5">
          <SkeletonLine className="h-2.5 w-1/2" />
          <SkeletonLine className="h-5 w-2/3" />
        </div>
      ))}
    </div>
  )
}

/** 加载态：形状占位，超过 150ms 才显示，避免快请求闪烁 */
export function LoadingState({ text = '正在加载…', className = '' }: { text?: string; className?: string }) {
  const visible = useDelayedFlag(true)
  if (!visible) return null
  return (
    <div className={`card space-y-3 p-6 ${className}`} role="status" aria-live="polite">
      <div className="flex items-center gap-2">
        <SkeletonLine className="h-4 w-4 rounded-full" />
        <SkeletonLine className="h-3 w-40 max-w-full" />
      </div>
      <SkeletonLine className="h-3 w-full" />
      <SkeletonLine className="h-3 w-4/5" />
      <span className="sr-only">{text}</span>
    </div>
  )
}

/** 错误态：优先展示后端错误码与错误信息 */
export function ErrorState({
  error,
  title = '数据加载失败',
  onRetry,
  className = '',
}: {
  error?: unknown
  title?: string
  onRetry?: () => void
  className?: string
}) {
  const apiError = error instanceof ApiError ? error : null
  const message =
    apiError?.message ||
    (error instanceof Error ? error.message : '') ||
    '请求失败，请检查网络或稍后重试'
  const detail = apiError?.detail
  const nextAction =
    typeof detail === 'object' && detail !== null && 'next_action' in detail
      ? (detail as Record<string, unknown>).next_action
      : null
  const nextActionText =
    typeof nextAction === 'string' && nextAction.trim() ? nextAction.trim() : null
  // C2：优先用后端 suggestion，其次 detail.next_action，最后给统一兜底
  const suggestionText =
    apiError?.suggestion?.trim() ||
    nextActionText ||
    '请稍后重试；若持续失败，请携带错误码与请求 ID 反馈。'

  return (
    <div className={`card flex flex-col items-center gap-3 px-6 py-10 text-center ${className}`}>
      <span className="grid h-11 w-11 place-items-center rounded-full border border-alert/25 bg-alert/[0.06]">
        <AlertCircle className="h-5 w-5 text-alert" />
      </span>
      <p className="text-[15px] font-semibold">{title}</p>
      {apiError?.code && (
        <span className="rounded border border-alert/25 bg-alert/[0.06] px-2 py-0.5 font-mono text-[12px] tracking-wide text-alert">
          {apiError.code}
        </span>
      )}
      <p className="max-w-md break-words text-[12px] leading-relaxed text-muted-foreground">{message}</p>
      <p className="max-w-md text-[12px] leading-relaxed text-muted-foreground/80">建议：{suggestionText}</p>
      {apiError?.requestId && (
        <p className="font-mono text-[11px] tracking-wide text-muted-foreground/60">
          请求 ID：{apiError.requestId}
        </p>
      )}
      {apiError?.detail !== null && apiError?.detail !== undefined && (
        <details className="max-w-md text-[12px] text-muted-foreground">
          <summary className="cursor-pointer transition-colors hover:text-foreground">查看错误详情</summary>
          <pre className="mt-2 whitespace-pre-wrap break-all rounded-lg border border-border/70 bg-muted/40 p-2.5 text-left font-mono text-[12px]">
            {JSON.stringify(apiError.detail, null, 2)}
          </pre>
        </details>
      )}
      {onRetry && (
        <button onClick={onRetry} className="btn btn-primary btn-sm mt-1">
          <RefreshCw className="h-3.5 w-3.5" />
          重试
        </button>
      )}
    </div>
  )
}

/** 空态 */
export function EmptyState({
  title = '暂无数据',
  description,
  action,
  className = '',
}: {
  title?: string
  description?: ReactNode
  action?: ReactNode
  className?: string
}) {
  return (
    <div className={`card flex flex-col items-center gap-3 px-6 py-12 text-center ${className}`}>
      <span className="grid h-11 w-11 place-items-center rounded-full border border-border/80 bg-muted/35">
        <Inbox className="h-5 w-5 text-muted-foreground/60" />
      </span>
      <p className="text-[14px] font-medium text-foreground">{title}</p>
      {description && (
        <div className="max-w-md text-[12px] leading-relaxed text-muted-foreground">{description}</div>
      )}
      {action}
    </div>
  )
}

/** 骨架块（复用 index.css 的 .skeleton 动画） */
export function SkeletonBlock({ className = 'h-24' }: { className?: string }) {
  return <div className={`skeleton ${className}`} />
}
