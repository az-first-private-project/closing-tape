/**
 * 市场阶段判断卡（V7 T1）
 * ========================
 * 状态色 + 一句话结论 + 把握度环 + 乐观度条 + 状态持续天数；
 * 右上角展开 5 条因子依据。
 */
import { useId, useState } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'
import type { MarketEvidence, MarketRegimeBlock, MarketOverviewMeta } from '../types/market'
import { REGIME_STYLES } from '../lib/market'
import EvidenceList from './EvidenceList'

const RING_RADIUS = 34
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS

function ConfidenceRing({ score, color }: { score: number; color: string }) {
  const clamped = Math.min(100, Math.max(0, score))
  return (
    <div className="relative h-24 w-24">
      <svg viewBox="0 0 88 88" className="h-full w-full -rotate-90" role="img" aria-label={`把握度 ${score}`}>
        <circle cx="44" cy="44" r={RING_RADIUS} fill="none" stroke="var(--color-border)" strokeWidth="7" />
        <circle
          cx="44"
          cy="44"
          r={RING_RADIUS}
          fill="none"
          stroke={color}
          strokeWidth="7"
          strokeLinecap="round"
          strokeDasharray={`${(clamped / 100) * RING_CIRCUMFERENCE} ${RING_CIRCUMFERENCE}`}
          className="transition-[stroke-dasharray] duration-700"
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="readout text-[24px] leading-none text-foreground">{score}</span>
        <span className="mt-0.5 text-[10px] text-muted-foreground">把握度</span>
      </div>
    </div>
  )
}

export default function RegimeCard({
  regime,
  meta,
  evidence,
}: {
  regime: MarketRegimeBlock
  meta: MarketOverviewMeta
  evidence: MarketEvidence[]
}) {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  const style = REGIME_STYLES[regime.code]

  return (
    <section className="card relative overflow-hidden p-4 sm:p-5">
      <span
        aria-hidden="true"
        className="absolute inset-x-0 top-0 h-1"
        style={{ backgroundColor: style.color }}
      />

      <div className="flex items-start justify-between gap-2">
        <div>
          <span className="eyebrow">Market Regime</span>
          <h2 className="mt-1.5 text-[15px] font-semibold tracking-[-0.01em]">阶段判断</h2>
        </div>
        <div className="flex items-center gap-2">
          <span className="num text-[11px] text-muted-foreground">{meta.trade_date}</span>
          {meta.is_historical && (
            <span className="rounded-full border border-border/70 px-1.5 py-0.5 text-[10px] text-muted-foreground">
              历史回看
            </span>
          )}
        </div>
      </div>

      <div className="mt-4 flex items-center gap-2">
        <span
          className="rounded-full border px-2.5 py-1 text-[12px] font-medium"
          style={{ borderColor: style.color, color: style.color }}
        >
          {regime.label}
        </span>
        <span className="text-[11px] text-muted-foreground">
          已持续 <span className="num text-foreground">{regime.days}</span> 个交易日
        </span>
      </div>

      <p className="mt-3 text-[20px] font-semibold leading-snug tracking-[-0.01em] text-foreground">
        {regime.headline}
      </p>
      <p className="mt-1 text-[11px] text-muted-foreground">
        状态自 {regime.since} 起
        {meta.stale_fields.length > 0 && ` · 缺 ${meta.stale_fields.length} 项数据`}
      </p>

      {regime.note && (
        <p className="mt-2 rounded-lg border border-warning/25 bg-warning/[0.06] px-2.5 py-1.5 text-[11px] leading-relaxed text-warning">
          {regime.note}
        </p>
      )}

      <div className="mt-4 grid grid-cols-[auto_1fr] items-center gap-4">
        <ConfidenceRing score={regime.confidence.score} color={style.color} />
        <div className="space-y-3">
          <div>
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-[12px] text-muted-foreground">乐观程度</span>
              <span className="num text-[13px] font-semibold text-foreground">
                {regime.optimism.score}
                <span className="ml-1 text-[11px] font-normal text-muted-foreground">
                  {regime.optimism.level}
                </span>
              </span>
            </div>
            <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-border/60">
              <div
                className="h-full rounded-full transition-[width] duration-700"
                style={{
                  width: `${regime.optimism.score}%`,
                  backgroundColor: style.color,
                }}
              />
            </div>
            <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
              {regime.optimism.plain}
            </p>
          </div>
          <div>
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-[12px] text-muted-foreground">判断把握</span>
              <span className="num text-[13px] font-semibold text-foreground">
                {regime.confidence.score}
                <span className="ml-1 text-[11px] font-normal text-muted-foreground">
                  {regime.confidence.level}
                </span>
              </span>
            </div>
            <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
              {regime.confidence.plain}
            </p>
          </div>
        </div>
      </div>

      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls={panelId}
        className="mt-4 flex w-full items-center justify-between border-t border-border/60 pt-3 text-[12px] text-muted-foreground transition-colors hover:text-foreground"
      >
        <span>为什么这么判断</span>
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
