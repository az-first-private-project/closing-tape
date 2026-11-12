/**
 * 一周市场温度计卡（V7 T1）
 * ==========================
 * 当前温度 + 五档评分 + 最近 5 个交易日序列 + 周均值/斜率 + 情绪结论。
 * 展开区显示 7 个子项的原始值与得分，解释温度是怎么来的。
 */
import { useId, useState } from 'react'
import { AlertTriangle, ChevronDown, ChevronUp } from 'lucide-react'
import type {
  MarketEvidence,
  MarketThermometer,
  TemperatureComponent,
} from '../types/market'
import {
  TEMPERATURE_BANDS,
  temperatureBand,
  temperatureColor,
  temperatureLabel,
} from '../lib/market'
import EvidenceList from './EvidenceList'

const GAUGE_RADIUS = 74
const GAUGE_CENTER_Y = 92
const GAUGE_PATH = `M ${100 - GAUGE_RADIUS} ${GAUGE_CENTER_Y} A ${GAUGE_RADIUS} ${GAUGE_RADIUS} 0 0 1 ${100 + GAUGE_RADIUS} ${GAUGE_CENTER_Y}`

function Gauge({ score }: { score: number | null }) {
  const clamped = score === null ? 0 : Math.min(100, Math.max(0, score))
  const band = temperatureBand(score)
  return (
    <div className="relative mx-auto w-full max-w-[220px]">
      {score !== null && (
        <span
          aria-hidden="true"
          className="pointer-events-none absolute left-1/2 top-5 h-24 w-36 -translate-x-1/2 rounded-full opacity-[0.16] blur-3xl"
          style={{ backgroundColor: temperatureColor(score) }}
        />
      )}
      <svg
        viewBox="0 0 200 104"
        className="relative w-full"
        role="img"
        aria-label={score === null ? '市场温度：数据不足' : `市场温度 ${score}，${temperatureLabel(score)}`}
      >
        <path d={GAUGE_PATH} fill="none" stroke="var(--color-border)" strokeWidth={11} strokeLinecap="round" />
        {score !== null && (
          <path
            d={GAUGE_PATH}
            fill="none"
            stroke={temperatureColor(score)}
            strokeWidth={11}
            strokeLinecap="round"
            pathLength={100}
            strokeDasharray={`${clamped} 100`}
            className="transition-[stroke-dasharray] duration-700"
          />
        )}
        {[25, 50, 75].map((tick) => {
          const angle = Math.PI * (1 - tick / 100)
          const r1 = GAUGE_RADIUS - 10
          const r2 = GAUGE_RADIUS - 5
          return (
            <line
              key={tick}
              x1={100 + Math.cos(angle) * r1}
              y1={GAUGE_CENTER_Y - Math.sin(angle) * r1}
              x2={100 + Math.cos(angle) * r2}
              y2={GAUGE_CENTER_Y - Math.sin(angle) * r2}
              stroke="var(--color-border)"
              strokeWidth={1}
            />
          )
        })}
      </svg>
      <div className="absolute inset-x-0 bottom-2 text-center">
        <p className={`readout text-[38px] leading-none ${band?.textClass ?? 'text-muted-foreground'}`}>
          {score === null ? '—' : score}
        </p>
        <p className="mt-1.5 text-[11px] tracking-wide text-muted-foreground">
          {score === null ? '数据不足' : temperatureLabel(score)}
          {score !== null && (
            <span className="ml-1 num text-foreground">
              {temperatureBand(score) ? `${TEMPERATURE_BANDS.findIndex((item) => item.label === band?.label) + 1}/5` : ''}
            </span>
          )}
        </p>
      </div>
    </div>
  )
}

function TemperatureBar({ point }: { point: MarketThermometer['series'][number] }) {
  const color = temperatureColor(point.temp)
  const height = Math.max(8, Math.min(100, point.temp))
  return (
    <div className="flex flex-col items-center gap-1.5" title={`${point.date} ${point.temp} ${point.label}`}>
      <span className="num text-[11px] text-foreground">{point.temp}</span>
      <div className="flex h-16 w-6 items-end overflow-hidden rounded-md bg-border/40">
        <div
          className="w-full rounded-md transition-[height] duration-700"
          style={{ height: `${height}%`, backgroundColor: color }}
        />
      </div>
      <span className="text-[10px] text-muted-foreground">{point.label}</span>
      <span className="num text-[10px] text-muted-foreground/80">{point.date.slice(5)}</span>
    </div>
  )
}

function formatComponentRaw(item: TemperatureComponent): string {
  if (item.raw === null || !Number.isFinite(item.raw)) return '—'
  switch (item.key) {
    case 'turnover':
      return `${item.raw.toFixed(2)}%`
    case 'amount':
      return `${(item.raw / 1e12).toFixed(2)} 万亿`
    case 'limit_up':
      return `${Math.round(item.raw)} 家`
    case 'margin_buy':
    case 'new_high_60':
      return `${(item.raw * 100).toFixed(1)}%`
    case 'rsi14_norm':
      return item.raw.toFixed(1)
    case 'rv20_inv':
      return `${item.raw.toFixed(1)}%`
    default:
      return String(item.raw)
  }
}

function ComponentList({ thermometer }: { thermometer: MarketThermometer }) {
  const items = thermometer.components?.items ?? []
  if (items.length === 0) {
    return <p className="py-2 text-[12px] text-muted-foreground">暂无温度子项明细</p>
  }
  return (
    <div className="space-y-2.5">
      {items.map((item) => (
        <div key={item.key} className="rounded-lg border border-border/60 bg-muted/20 p-2.5">
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-[12px] text-muted-foreground">{item.label}</span>
            <span className="num text-[12px] text-foreground">{formatComponentRaw(item)}</span>
          </div>
          <div className="mt-1.5 flex items-center gap-2">
            <div className="h-1 flex-1 overflow-hidden rounded-full bg-border/50">
              {item.score !== null && (
                <div
                  className="h-full rounded-full transition-[width] duration-700"
                  style={{ width: `${item.score}%`, backgroundColor: temperatureColor(item.score) }}
                />
              )}
            </div>
            <span className="w-8 text-right num text-[11px] text-muted-foreground">
              {item.score === null ? '—' : item.score.toFixed(0)}
            </span>
          </div>
          <p className="mt-1 truncate text-[11px] text-muted-foreground/80" title={item.note}>
            {item.note}
          </p>
        </div>
      ))}
    </div>
  )
}

export default function ThermometerCard({
  thermometer,
  evidence,
}: {
  thermometer: MarketThermometer
  evidence: MarketEvidence[]
}) {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  const currentBand = temperatureBand(thermometer.current_temp)
  const showComponents = (thermometer.components?.items?.length ?? 0) > 0

  return (
    <section className="card relative overflow-hidden p-4 sm:p-5">
      <div className="flex items-start justify-between gap-2">
        <div>
          <span className="eyebrow">Market Thermometer</span>
          <h2 className="mt-1.5 text-[15px] font-semibold tracking-[-0.01em]">一周市场温度</h2>
        </div>
        <div className="text-right">
          <p className="num text-[11px] text-muted-foreground">
            {thermometer.series[thermometer.series.length - 1]?.date ?? '—'}
          </p>
          <p className="text-[10px] text-muted-foreground">近 5 个交易日</p>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-[minmax(0,170px)_1fr] items-center gap-4">
        <Gauge score={thermometer.current_temp} />
        <div className="space-y-2">
          <div className="flex flex-wrap gap-1.5">
            <span className="rounded-full border border-border/70 bg-muted/40 px-2 py-0.5 text-[11px] text-foreground">
              周均 <span className="num">{thermometer.week_avg ?? '—'}</span>
            </span>
            <span className="rounded-full border border-border/70 bg-muted/40 px-2 py-0.5 text-[11px] text-foreground">
              周高 <span className="num">{thermometer.week_max ?? '—'}</span>
            </span>
            <span className="rounded-full border border-border/70 bg-muted/40 px-2 py-0.5 text-[11px] text-foreground">
              周低 <span className="num">{thermometer.week_min ?? '—'}</span>
            </span>
          </div>
          <div className="flex items-center gap-2 text-[11px]">
            <span className="text-muted-foreground">一周趋势</span>
            <span className="font-medium text-foreground">{thermometer.week_trend}</span>
            {thermometer.week_slope !== null && (
              <span className="num text-muted-foreground">
                {thermometer.week_slope > 0 ? '+' : ''}
                {thermometer.week_slope}
              </span>
            )}
          </div>
          <p className="text-[12px] leading-relaxed text-muted-foreground">
            {thermometer.emotion_conclusion}
          </p>
        </div>
      </div>

      <div className="mt-3 flex items-end justify-between gap-1 rounded-lg border border-border/60 bg-muted/20 px-2.5 py-2">
        {thermometer.series.length > 0 ? (
          thermometer.series.map((point) => <TemperatureBar key={point.date} point={point} />)
        ) : (
          <p className="py-4 text-[12px] text-muted-foreground">暂无一周温度序列</p>
        )}
      </div>

      {/* 五档刻度带：当前档高亮 */}
      <div className="mt-2 grid grid-cols-5 gap-1">
        {TEMPERATURE_BANDS.map((band, index) => {
          const active = currentBand?.label === band.label
          return (
            <span
              key={band.label}
              className={`rounded-md border px-1 py-1 text-center text-[10px] ${
                active ? 'border-transparent font-medium text-foreground' : 'border-border/60 text-muted-foreground'
              }`}
              style={active ? { backgroundColor: band.color, color: '#fff' } : undefined}
            >
              {index + 1} {band.label}
            </span>
          )
        })}
      </div>

      {thermometer.extreme_note && (
        <p className="mt-2 flex items-start gap-1.5 rounded-lg border border-warning/25 bg-warning/[0.06] px-2.5 py-2 text-[11px] leading-relaxed text-warning">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {thermometer.extreme_note}
        </p>
      )}

      {thermometer.confidence && thermometer.confidence !== 'ok' && (
        <p className="mt-2 text-[11px] text-muted-foreground">
          {thermometer.confidence === 'insufficient'
            ? '温度数据不足，暂不给出总分。'
            : '部分子项缺数据，总分由可用子项重新加权。'}
        </p>
      )}

      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls={panelId}
        className="mt-3 flex w-full items-center justify-between border-t border-border/60 pt-3 text-[12px] text-muted-foreground transition-colors hover:text-foreground"
      >
        <span>为什么是这个温度</span>
        {open ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
      </button>
      {open && (
        <div id={panelId} className="mt-1">
          {showComponents ? <ComponentList thermometer={thermometer} /> : <EvidenceList items={evidence} />}
        </div>
      )}
    </section>
  )
}
