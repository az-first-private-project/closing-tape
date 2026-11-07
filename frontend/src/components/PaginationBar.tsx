import { ChevronLeft, ChevronRight } from 'lucide-react'
import { cn } from '../lib/utils'

interface PaginationBarProps {
  page: number
  totalPages: number
  total: number
  onPageChange: (page: number) => void
}

/** 分页：左右方向键按钮 + 等宽页码读数，视觉上与表格底部的元信息一致 */
export default function PaginationBar({
  page,
  totalPages,
  total,
  onPageChange,
}: PaginationBarProps) {
  return (
    <div className="mt-12 flex items-center justify-center gap-4 border-t border-border/60 pt-6">
      <button
        onClick={() => onPageChange(page - 1)}
        disabled={page <= 1}
        className="btn btn-sm h-9 w-9 !px-0"
        aria-label="上一页"
      >
        <ChevronLeft className="h-4 w-4" />
      </button>

      <div className="flex items-baseline gap-2 font-mono text-[11px] text-muted-foreground">
        <span className="text-foreground">{String(page).padStart(2, '0')}</span>
        <span className="text-muted-foreground/50">/</span>
        <span>{String(totalPages).padStart(2, '0')}</span>
        <span className="ml-2 text-muted-foreground/70">共 {total} 篇</span>
      </div>

      <button
        onClick={() => onPageChange(page + 1)}
        disabled={page >= totalPages}
        className={cn('btn btn-sm h-9 w-9 !px-0')}
        aria-label="下一页"
      >
        <ChevronRight className="h-4 w-4" />
      </button>
    </div>
  )
}
