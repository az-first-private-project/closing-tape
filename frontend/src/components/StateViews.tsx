/**
 * 统一三态视图：Loading / Error / Empty
 * ========================================
 * 错误态会展示后端统一错误体的 code 与 message（契约 0.2），
 * 便于定位「数据未就绪 / 数据质量 / 参数错误」等原因。
 */
import type { ReactNode } from 'react'
import { AlertCircle, Inbox, Loader2, RefreshCw } from 'lucide-react'
import { ApiError } from '../api/client'

/** 加载态 */
export function LoadingState({ text = '正在加载…', className = '' }: { text?: string; className?: string }) {
  return (
    <div className={`card p-10 flex flex-col items-center gap-3 ${className}`}>
      <Loader2 className="w-6 h-6 animate-spin text-primary" />
      <p className="text-sm text-muted-foreground">{text}</p>
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
