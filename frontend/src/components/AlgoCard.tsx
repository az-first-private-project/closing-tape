/**
 * 算法卡片（契约 5.1）
 * =====================
 * 展示算法名称、类型、简介、信号数与参数数量；未启用算法置灰不可选。
 * 类型覆盖：价值/评分（A3、A4）与趋势/反转/突破（B1–B5）。
 */
import { motion } from 'framer-motion'
import type { AlgoMeta } from '../types/algo'

const TYPE_COLORS: Record<string, string> = {
  technical: 'border-chart-1/40 bg-chart-1/10',
  value: 'border-success/40 bg-success/10',
  fund: 'border-chart-4/40 bg-chart-4/10',
  score: 'border-chart-2/40 bg-chart-2/10',
  trend: 'border-chart-3/40 bg-chart-3/10',
  reversal: 'border-warning/40 bg-warning/10',
  breakout: 'border-primary/40 bg-primary/10',
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
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      onClick={() => {
        if (enabled) onSelect(algo.algo_code)
      }}
      className={`
        card p-4
        ${enabled ? 'cursor-pointer' : 'opacity-60 cursor-not-allowed'}
        ${isActive ? 'ring-2 ring-primary border-primary' : 'border-border'}      `}
    >
      {/* 标题行 */}
      <div className="flex items-center justify-between gap-2 mb-2">
        <h3 className="font-semibold text-sm truncate" title={algo.algo_name}>
          {algo.algo_name}
        </h3>
        <span
          className={`shrink-0 text-[11px] px-2 py-0.5 rounded-full font-medium border text-foreground ${
            TYPE_COLORS[algo.algo_type] ?? 'bg-muted text-muted-foreground border-border'
          }`}
        >
          {TYPE_LABELS[algo.algo_type] ?? algo.algo_type}
        </span>
      </div>

      {/* 描述 */}
      <p className="text-xs text-muted-foreground mb-3 line-clamp-2" title={algo.short_desc}>
        {algo.short_desc}
      </p>

      {/* 底部：信号数 + 参数数 + 操作 */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
          <span>信号数: <span className="num">{signalCount !== null ? signalCount : '—'}</span></span>
          <span>·</span>
          <span>参数 {algo.params_schema.length} 项</span>
        </div>
        <button
          type="button"
          disabled={!enabled}
          className={`
            text-xs px-3 py-1 rounded-lg transition-colors
            ${
              isActive
                ? 'bg-primary text-primary-foreground'
                : 'border border-border hover:bg-muted text-foreground'
            }
            disabled:opacity-50
          `}
        >
          {enabled ? (isActive ? '已选中' : '查看详情') : '未启用'}
        </button>
      </div>
    </motion.div>
  )
}
