/**
 * 统一刊头（U04）
 * =================
 * 全站 10 个路由共用：眉线 + 英文眉题 + 主标题 + 口径说明 + 右侧操作。
 * 详情页（文章/个股）用 compact 字号，避免和正文标题争夺层级。
 */
import type { ReactNode } from 'react'
import { cn } from '../lib/utils'

interface PageHeaderProps {
  eyebrow: ReactNode
  title: ReactNode
  /** 标题后的附加标记（徽标、图标等） */
  titleAddon?: ReactNode
  description?: ReactNode
  /** 标题下方、说明上方的附加信息 */
  meta?: ReactNode
  actions?: ReactNode
  size?: 'default' | 'compact'
  /** 404 等单栏页面使用居中刊头 */
  align?: 'left' | 'center'
  className?: string
}

const TITLE_CLASS: Record<'default' | 'compact', string> = {
  default: 'page-title mt-2.5',
  compact: 'mt-2.5 text-[26px] font-semibold leading-[1.25] tracking-[-0.025em] sm:text-[32px]',
}

export default function PageHeader({
  eyebrow,
  title,
  titleAddon,
  description,
  meta,
  actions,
  size = 'default',
  align = 'left',
  className,
}: PageHeaderProps) {
  const centered = align === 'center'
  return (
    <header
      className={cn(
        'flex flex-wrap gap-4',
        centered ? 'flex-col items-center text-center' : 'items-end justify-between',
        className,
      )}
    >
      <div className={cn('rise min-w-0', centered && 'flex flex-col items-center')}>
        <div className="flex items-center gap-2.5">
          <span aria-hidden="true" className="h-px w-6 bg-brass" />
          <span className="eyebrow">{eyebrow}</span>
        </div>
        <div className="flex flex-wrap items-center justify-center gap-2">
          <h1 className={TITLE_CLASS[size]}>{title}</h1>
          {titleAddon}
        </div>
        {meta}
        {description && (
          <div
            className={cn(
              'mt-2 text-[12px] leading-relaxed text-muted-foreground',
              centered ? 'max-w-lg' : 'max-w-3xl',
            )}
          >
            {description}
          </div>
        )}
      </div>
      {actions && (
        <div
          className={cn(
            'flex min-w-0 max-w-full flex-wrap items-center gap-2',
            centered && 'justify-center',
          )}
        >
          {actions}
        </div>
      )}
    </header>
  )
}
