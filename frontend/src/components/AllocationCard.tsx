/**
 * 持仓配置建议卡（V7 T1）
 * ========================
 * 100% 堆叠条 + 防御/进攻/均衡三档明细（比例、板块类型、理由、较昨日变化）。
 * 只输出板块类型/行业名，不出现个股与代码。
 */
import { useId, useState } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'
import type { AllocationKey, MarketAllocation, MarketEvidence } from '../types/market'
import EvidenceList from './EvidenceList'

const ALLOCATION_COLORS: Record<AllocationKey, string> = {
  defensive: 'var(--color-chart-1)',
  offensive: 'var(--color-up)',
  balanced: 'var(--color-brass)',
}

function DeltaChip({ value }: { value: number | undefined }) {
  if (value === undefined || value === 0) {
    return <span className="num text-[11px] text-muted-foreground">—</span>
  }
  const up = value > 0
  return (
    <span className={`num text-[11px] ${up ? 'text-brass-strong dark:text-brass' : 'text-muted-foreground'}`}>
      {up ? '▲' : '▼'} {Math.abs(value)}pct
    </span>
  )
}

export default function AllocationCard({
  allocation,
  evidence,
}: {
  allocation: MarketAllocation
  evidence: MarketEvidence[]
}) {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  const buckets = allocation.buckets ?? []

  return (
    <section className="card relative overflow-hidden p-4 sm:p-5">
      <div className="flex items-start justify-between gap-2">
        <div>
          <span className="eyebrow">Position Plan</span>
          <h2 className="mt-1.5 text-[15px] font-semibold tracking-[-0.01em]">持仓配置建议</h2>
        </div>
        <span className="num text-[11px] text-muted-foreground">合计 {allocation.total}%</span>
      </div>

      {/* 100% 堆叠条 */}
      <div className="mt-4 flex h-3 overflow-hidden rounded-full bg-border/50" role="img" aria-label="防御、进攻、均衡配置比例">
        {buckets.map((bucket) => (
          <div
            key={bucket.key}
            title={`${bucket.label} ${bucket.pct}%`}
            style={{
              width: `${bucket.pct}%`,
              backgroundColor: ALLOCATION_COLORS[bucket.key],
            }}
            className="h-full transition-[width] duration-700"
          />
        ))}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
        {buckets.map((bucket) => (
          <span key={bucket.key} className="inline-flex items-center gap-1">
            <span
              aria-hidden="true"
              className="inline-block h-2 w-2 rounded-sm"
              style={{ backgroundColor: ALLOCATION_COLORS[bucket.key] }}
            />
            {bucket.label} <span className="num text-foreground">{bucket.pct}%</span>
          </span>
        ))}
      </div>

      <div className="mt-4 space-y-3">
        {buckets.map((bucket) => (
          <div key={bucket.key} className="rounded-lg border border-border/60 bg-muted/20 p-3">
            <div className="flex items-baseline justify-between gap-2">
              <div className="flex items-baseline gap-2">
                <span className="text-[13px] font-medium text-foreground">{bucket.label}</span>
                <span className="readout text-[20px] leading-none text-foreground">
                  {bucket.pct}
                  <span className="ml-0.5 text-[11px] font-normal text-muted-foreground">%</span>
                </span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[11px] text-muted-foreground">较昨日</span>
                <DeltaChip value={allocation.vs_yesterday?.[bucket.key]} />
              </div>
            </div>
            <p className="mt-1 text-[11px] text-muted-foreground">{bucket.role}</p>
            {bucket.sectors.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {bucket.sectors.map((sector) => (
                  <span
                    key={`${bucket.key}-${sector}`}
                    className="rounded-full border border-border/70 bg-card/60 px-2 py-0.5 text-[11px] text-foreground"
                  >
                    {sector}
                  </span>
                ))}
              </div>
            )}
            <p className="mt-2 text-[12px] leading-relaxed text-muted-foreground">{bucket.reason}</p>
          </div>
        ))}
      </div>

      {allocation.rebalance_note && (
        <p className="mt-3 rounded-lg border border-border/60 bg-muted/20 px-2.5 py-2 text-[11px] leading-relaxed text-muted-foreground">
          <span className="text-foreground">下次调仓触发：</span>
          {allocation.rebalance_note}
        </p>
      )}
      {allocation.confidence_caveat && (
        <p className="mt-2 rounded-lg border border-warning/25 bg-warning/[0.06] px-2.5 py-2 text-[11px] leading-relaxed text-warning">
          {allocation.confidence_caveat}
        </p>
      )}

      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls={panelId}
        className="mt-3 flex w-full items-center justify-between border-t border-border/60 pt-3 text-[12px] text-muted-foreground transition-colors hover:text-foreground"
      >
        <span>为什么这么配</span>
        {open ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
      </button>
      {open && (
        <div id={panelId} className="mt-1">
          <EvidenceList items={evidence} />
        </div>
      )}
    </section>
  )
}
