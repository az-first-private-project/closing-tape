/**
 * 算法卡片（契约 5.1）
 * =====================
 * 展示算法名称、类型、简介、信号数与参数数量；未启用算法置灰不可选。
 * 启用算法额外提供「纸面跟踪」入口（V6 T11）。
 * 类型覆盖：价值/评分（A3、A4）与趋势/反转/突破（B1–B5）。
 *
 * 视觉：像一张策略索引卡——类型标签、等宽编号、底部读数条；
 * 选中态用铜色左侧标识 + 细框，而不是粗 ring。
 */
import { motion } from 'framer-motion'
import { Link } from 'react-router-dom'
import { LineChart } from 'lucide-react'
import type { AlgoMeta } from '../types/algo'
import { cn } from '../lib/utils'

const TYPE_COLORS: Record<string, string> = {
  technical: 'border-chart-1/40 bg-chart-1/10',
  value: 'border-success/40 bg-success/10',
  fund: 'border-chart-4/40 bg-chart-4/10',
  score: 'border-chart-2/40 bg-chart-2/10',
  trend: 'border-chart-3/40 bg-chart-3/10',
  reversal: 'border-warning/40 bg-warning/10',
  breakout: 'border-primary/30 bg-primary/10',
}

const TYPE_LABELS: Record<string, string> = {
  technical: '技术派',
  value: '价值派',
  fund: '资金派',
  score: '评分派',
  trend: '趋势派',
  reversal: '反转派',
  breakout: '突破派',
}

interface Props {
  algo: AlgoMeta
  signalCount: number | null
  isActive: boolean
  onSelect: (code: string) => void
}

export default function AlgoCard({ algo, signalCount, isActive, onSelect }: Props) {
  const enabled = algo.is_active !== false

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
      onClick={() => {
        if (enabled) onSelect(algo.algo_code)
      }}
      className={cn(
        'card card-lift group relative flex h-full flex-col p-4',
        enabled ? 'cursor-pointer' : 'cursor-not-allowed opacity-55',
        isActive && 'card-selected',
      )}
    >
      {/* 选中态：左侧铜色标识 */}
      {isActive && (
        <span aria-hidden="true" className="absolute inset-y-3 left-0 w-[2px] rounded-full bg-brass" />
      )}

      {/* 标题行 */}
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-muted-foreground/70">
            {algo.algo_code}
          </p>
          <h3 className="mt-1 truncate text-[13.5px] font-semibold" title={algo.algo_name}>
            {algo.algo_name}
          </h3>
        </div>
        <span
          className={cn(
            'shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-medium text-foreground',
            TYPE_COLORS[algo.algo_type] ?? 'border-border bg-muted text-muted-foreground',
          )}
        >
          {TYPE_LABELS[algo.algo_type] ?? algo.algo_type}
        </span>
      </div>

      {/* 描述 */}
      <p className="mb-3 line-clamp-2 text-[11px] leading-relaxed text-muted-foreground" title={algo.short_desc}>
        {algo.short_desc}
      </p>

      {/* 底部：读数 + 操作 */}
      <div className="mt-auto flex flex-wrap items-center justify-between gap-2 border-t border-border/60 pt-3">
        <div className="flex items-center gap-2.5 text-[10px] text-muted-foreground">
          <span className="inline-flex items-baseline gap-1">
            信号
            <span className="num text-[12px] font-semibold text-foreground">
              {signalCount !== null ? signalCount : '—'}
            </span>
          </span>
          <span aria-hidden="true" className="h-2.5 w-px bg-border/80" />
          <span className="num">参数 {algo.params_schema.length}</span>
        </div>
        <div className="flex items-center gap-1.5">
          {enabled && (
            <Link
              to={`/paper?algo=${algo.algo_code}`}
              onClick={(event) => event.stopPropagation()}
              className="inline-flex items-center gap-1 rounded-md border border-transparent px-1.5 py-1
                text-[10px] text-muted-foreground transition-colors
                hover:border-border/70 hover:bg-muted/60 hover:text-foreground"
              title={`查看「${algo.algo_name}」的纸面组合跟踪`}
            >
              <LineChart className="h-3 w-3" />
              纸面跟踪
            </Link>
          )}
          <button
            type="button"
            disabled={!enabled}
            className={cn(
              'rounded-md px-2 py-1 text-[10px] font-medium transition-colors',
              isActive
                ? 'bg-brass/15 text-brass-strong ring-1 ring-brass/40 dark:text-brass'
                : 'border border-border/80 text-foreground hover:border-brass/40 hover:bg-muted/60',
              'disabled:opacity-50',
            )}
          >
            {enabled ? (isActive ? '已选中' : '查看详情') : '未启用'}
          </button>
        </div>
      </div>
    </motion.div>
  )
}
