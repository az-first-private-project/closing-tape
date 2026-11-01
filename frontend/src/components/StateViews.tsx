/**
 * 统一三态视图：Loading / Error / Empty
 * ========================================
 * 错误态会展示后端统一错误体的 code 与 message（契约 0.2），
 * 便于定位「数据未就绪 / 数据质量 / 参数错误」等原因。
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
      <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-2">
        <SkeletonLine className="h-3 w-16" />
        <SkeletonLine className="h-3 w-12" />
      </div>
      <div className="divide-y divide-border">
        <div className="grid gap-3 bg-muted/40 px-4 py-2.5" style={gridStyle}>
          {Array.from({ length: cols }).map((_, index) => (
            <SkeletonLine key={index} className="h-3 w-2/3" />
          ))}
        </div>
        {Array.from({ length: rows }).map((_, row) => (
          <div key={row} className="grid gap-3 px-4 py-3" style={gridStyle}>
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
      ? 'grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2'
      : 'grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2'
  return (
    <div className={`${gridClass} ${className}`} aria-hidden="true">
      {Array.from({ length: count }).map((_, index) => (
        <div key={index} className="card space-y-2 p-3">
          <SkeletonLine className="h-3 w-1/2" />
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

  return (
    <div className={`card p-8 flex flex-col items-center gap-3 text-center ${className}`}>
      <AlertCircle className="w-7 h-7 text-alert" />
      <p className="font-medium">{title}</p>
      {apiError?.code && (
        <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-alert/10 text-alert border border-alert/30">
          {apiError.code}
        </span>
      )}
      <p className="text-sm text-muted-foreground max-w-md break-words">{message}</p>
      {nextActionText && (
        <p className="text-xs text-muted-foreground max-w-md">建议：{nextActionText}</p>
      )}
      {apiError?.detail !== null && apiError?.detail !== undefined && (
        <details className="text-xs text-muted-foreground max-w-md">
          <summary className="cursor-pointer hover:text-foreground">查看错误详情</summary>
          <pre className="mt-2 text-left whitespace-pre-wrap break-all bg-muted rounded-lg p-2">
            {JSON.stringify(apiError.detail, null, 2)}
          </pre>
        </details>
      )}
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-1 inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm bg-primary text-primary-foreground hover:opacity-90 transition-opacity"
        >
          <RefreshCw className="w-4 h-4" />
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
    <div className={`card p-10 flex flex-col items-center gap-3 text-center ${className}`}>
      <Inbox className="w-8 h-8 text-muted-foreground/40" />
      <p className="font-medium text-muted-foreground">{title}</p>
      {description && <div className="text-sm text-muted-foreground/80 max-w-md">{description}</div>}
      {action}
    </div>
  )
}

/** 骨架块（复用 index.css 的 .skeleton 动画） */
export function SkeletonBlock({ className = 'h-24' }: { className?: string }) {
  return <div className={`skeleton ${className}`} />
}
