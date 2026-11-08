/**
 * 统一分区标题（U04）
 * =====================
 * 全站唯一分区编号规则：两位数字 01 / 02 / 03，仅用于页内一级分区。
 * 标题 + 口径说明 + 右侧操作在同一基线上对齐。
 */
import type { ReactNode } from 'react'
import { cn } from '../lib/utils'

interface SectionHeaderProps {
  /** 两位分区序号，如 "01" */
  index: string
  title: ReactNode
  /** 口径/单位/数据范围说明 */
  hint?: ReactNode
  actions?: ReactNode
  id?: string
  className?: string
}

export default function SectionHeader({
  index,
  title,
  hint,
  actions,
  id,
  className,
}: SectionHeaderProps) {
  return (
    <div
      className={cn(
        'mb-2.5 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1',
        className,
      )}
    >
      <h2 id={id} className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        <span className="section-index">{index}</span>
        <span className="text-[13px] font-semibold tracking-wide text-foreground">{title}</span>
        {hint && <span className="text-[12px] text-muted-foreground">{hint}</span>}
      </h2>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}
