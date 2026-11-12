/**
 * 市场状态 · 因子依据列表（V7 T1）
 * =================================
 * 统一渲染 5 条「为什么这么判断」：因子中文名、当日值、历史分位、作用方向、白话解释。
 * 只出现在展开区，主卡不裸露指标名。
 */
import type { MarketEvidence } from '../types/market'

function percentileText(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '离散信号'
  return `历史 ${Math.round(value * 100)}% 分位`
}

export default function EvidenceList({
  items,
  emptyText = '暂无可用因子依据',
}: {
  items: MarketEvidence[]
  emptyText?: string
}) {
  if (items.length === 0) {
    return <p className="py-2 text-[12px] text-muted-foreground">{emptyText}</p>
  }

  return (
    <div className="divide-y divide-border/60">
      {items.map((item) => (
        <div key={item.code} className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 py-2.5">
          <span className="mt-0.5 rounded border border-border/70 bg-muted/40 px-1.5 py-0.5 font-mono text-[10px] leading-4 text-muted-foreground">
            {item.code}
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <span className="text-[12px] font-medium text-foreground">{item.name}</span>
              <span className="num text-[12px] text-foreground">{item.value}</span>
              <span className="num text-[11px] text-muted-foreground">
                {percentileText(item.percentile)}
              </span>
              <span
                className={`rounded-full border border-border/70 px-1.5 py-0.5 text-[10px] ${
                  item.direction.startsWith('支撑')
                    ? 'text-brass-strong dark:text-brass'
                    : 'text-muted-foreground'
                }`}
              >
                {item.direction}
              </span>
            </div>
            <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
              {item.plain}
            </p>
          </div>
        </div>
      ))}
    </div>
  )
}
