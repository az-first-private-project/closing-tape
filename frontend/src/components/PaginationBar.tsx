import { ChevronLeft, ChevronRight } from 'lucide-react'
import { cn } from '../lib/utils'

interface PaginationBarProps {
  page: number
  totalPages: number
  total: number
  onPageChange: (page: number) => void
}

export default function PaginationBar({
  page,
  totalPages,
  total,
  onPageChange,
}: PaginationBarProps) {
  return (
    <div className="flex items-center justify-center gap-4 py-8">
      <button
        onClick={() => onPageChange(page - 1)}
        disabled={page <= 1}
        className={cn(
          'flex items-center gap-1 px-4 py-2 rounded-lg text-sm font-medium transition-colors',
          'border border-border hover:bg-secondary',
          'disabled:opacity-40 disabled:cursor-not-allowed'
        )}
      >
        <ChevronLeft className="w-4 h-4" />
        上一页
      </button>

      <span className="text-sm text-muted-foreground">
        第 {page} 页 / 共 {totalPages} 页 ({total} 篇)
      </span>

      <button
        onClick={() => onPageChange(page + 1)}
        disabled={page >= totalPages}
        className={cn(
          'flex items-center gap-1 px-4 py-2 rounded-lg text-sm font-medium transition-colors',
          'border border-border hover:bg-secondary',
          'disabled:opacity-40 disabled:cursor-not-allowed'
        )}
      >
        下一页
        <ChevronRight className="w-4 h-4" />
      </button>
    </div>
  )
}
