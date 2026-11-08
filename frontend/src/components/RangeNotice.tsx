/**
 * 展示范围说明（U06）：任何截断/分页都必须让用户看到「已显示 N / 共 M」。
 */
import { cn } from '../lib/utils'

interface RangeNoticeProps {
  shown: number
  total: number | null | undefined
  unit?: string
  note?: string
  className?: string
}

export default function RangeNotice({
  shown,
  total,
  unit = '条',
  note,
  className,
}: RangeNoticeProps) {
  const hasTotal = typeof total === 'number' && Number.isFinite(total)
  return (
    <span className={cn('text-[12px] text-muted-foreground', className)}>
      已显示 <span className="num text-foreground">{shown}</span> / 共{' '}
      <span className="num text-foreground">{hasTotal ? total : '—'}</span> {unit}
      {note ? ` · ${note}` : ''}
    </span>
  )
}
