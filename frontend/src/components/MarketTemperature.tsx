/**
 * 市场温度计（V6 T3）
 * =====================
 * 一个 0–100 的温度分 + 半圆仪表 + 六个子项细条 + 60 日 sparkline。
 * - 仪表弧线为纯 SVG（pathLength/strokeDasharray），不依赖图表库；
 * - `confidence !== "ok"` 时顶部提示条说明缺了哪些子项（可信度关键，不允许省略）；
 * - 缺失子项显示 "—"，不显示 NaN / 0 分。
 */
import { ApiError } from '../api/client'
import { useTemperature, useTemperatureSeries } from '../hooks/useTemperature'
import type {
  MarketTemperature as MarketTemperatureData,
  TemperatureComponent,
  TemperatureSeriesPoint,
} from '../types/market'
import { temperatureBand, temperatureColor, temperatureLabel } from '../lib/market'
import { fmtDate } from '../lib/format'

const GAUGE_RADIUS = 78
const GAUGE_CENTER_Y = 100
const GAUGE_PATH = `M ${100 - GAUGE_RADIUS} ${GAUGE_CENTER_Y} A ${GAUGE_RADIUS} ${GAUGE_RADIUS} 0 0 1 ${100 + GAUGE_RADIUS} ${GAUGE_CENTER_Y}`

/** 子项原值格式化（单位由后端 unit 声明） */
function formatRaw(component: TemperatureComponent): string {
  if (component.raw === null || !Number.isFinite(component.raw)) return '—'
  switch (component.key) {
    case 'up_ratio':
    case 'above_ma20':
      return `${(component.raw * 100).toFixed(1)}%`
    case 'limit_up':
    case 'limit_down':
      return `${Math.round(component.raw)} 家`
    case 'amount_ratio':
      return `${component.raw.toFixed(2)}×`
    case 'index_vol':
      return `${component.raw.toFixed(1)}%`
    default:
      return String(component.raw)
  }
}

/** 子项对温度的方向：≥55 升温、≤45 降温、其余中性 */
function scoreGlyph(score: number | null): { glyph: string; className: string } {
  if (score === null) return { glyph: '—', className: 'text-flat' }
  if (score >= 55) return { glyph: '↑', className: 'text-up' }
  if (score <= 45) return { glyph: '↓', className: 'text-down' }
  return { glyph: '→', className: 'text-flat' }
}

function Gauge({ score }: { score: number | null }) {
  const clamped = score === null ? 0 : Math.min(100, Math.max(0, score))
  const band = temperatureBand(score)
  return (
    <div className="relative mx-auto w-full max-w-[260px]">
      <svg
        viewBox="0 0 200 112"
        className="w-full"
        role="img"
        aria-label={score === null ? '市场温度：数据不足' : `市场温度 ${score.toFixed(1)}，${temperatureLabel(score)}`}
      >
        <path
          d={GAUGE_PATH}
          fill="none"
          stroke="var(--color-border)"
          strokeWidth={14}
          strokeLinecap="round"
        />
        {score !== null && (
          <path
            d={GAUGE_PATH}
            fill="none"
            stroke={temperatureColor(score)}
            strokeWidth={14}
            strokeLinecap="round"
            pathLength={100}
            strokeDasharray={`${clamped} 100`}
          />
        )}
      </svg>
      <div className="absolute inset-x-0 bottom-1 text-center">
        <p className={`text-5xl font-bold num leading-none ${band?.textClass ?? 'text-muted-foreground'}`}>
          {score === null ? '—' : score.toFixed(1)}
        </p>
        <p className="mt-1.5 text-xs text-muted-foreground">
          {score === null ? '数据不足' : temperatureLabel(score)}
        </p>
      </div>
      <div className="absolute inset-x-0 bottom-0 flex justify-between px-1 text-[10px] text-muted-foreground">
        <span>0</span>
        <span>100</span>
      </div>
    </div>
  )
}

/** 内联 sparkline：固定 0–100 纵轴，保证跨日形状可比；缺失日断开折线 */
function TemperatureSparkline({ points }: { points: TemperatureSeriesPoint[] }) {
  if (points.length === 0) {
    return <p className="text-[11px] text-muted-foreground">暂无历史温度序列</p>
  }
  const width = 240
  const height = 44
  const padding = 2
  const step = points.length > 1 ? width / (points.length - 1) : 0
  const y = (score: number) =>
    padding + (1 - Math.min(100, Math.max(0, score)) / 100) * (height - padding * 2)

  const segments: { x: number; y: number }[][] = []
  let current: { x: number; y: number }[] = []
  points.forEach((point, index) => {
    if (point.temp_score === null) {
      if (current.length > 0) segments.push(current)
      current = []
      return
    }
    current.push({ x: index * step, y: y(point.temp_score) })
  })
  if (current.length > 0) segments.push(current)

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="w-full h-11"
      preserveAspectRatio="none"
      role="img"
      aria-label="近 60 个交易日温度走势"
    >
      <line
        x1={0}
        x2={width}
        y1={y(50)}
        y2={y(50)}
        stroke="var(--color-border)"
        strokeWidth={1}
        strokeDasharray="2 3"
        vectorEffect="non-scaling-stroke"
      />
      {segments.map((segment, index) => (
        <polyline
          key={index}
          points={segment.map((point) => `${point.x},${point.y}`).join(' ')}
          fill="none"
          stroke="var(--color-chart-1)"
          strokeWidth={1.5}
          strokeLinejoin="round"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />
      ))}
    </svg>
  )
}

export default function MarketTemperature({ tradeDate }: { tradeDate?: string }) {
  const temperature = useTemperature(tradeDate)
  const series = useTemperatureSeries(60, tradeDate)
  const data: MarketTemperatureData | undefined = temperature.data

  if (temperature.isLoading && !data) {
    return (
      <div className="card p-5">
        <div className="h-4 w-24 rounded bg-muted animate-pulse" />
        <div className="mt-4 grid grid-cols-1 lg:grid-cols-[260px_1fr] gap-5">
          <div className="h-32 rounded-lg bg-muted/60 animate-pulse" />
          <div className="h-32 rounded-lg bg-muted/60 animate-pulse" />
        </div>
      </div>
    )
  }

  if (!data) {
    const notReady = temperature.error instanceof ApiError && temperature.error.code === 'DATA_NOT_READY'
    return (
      <div className="card p-4 text-sm text-muted-foreground">
        {notReady ? '市场温度尚未生成（每日管线 temperature 步骤未运行，或历史未回填）。' : '市场温度加载失败。'}
        <button
          type="button"
          onClick={() => temperature.refetch()}
          className="ml-2 text-primary hover:underline"
        >
          重试
        </button>
      </div>
    )
  }

  const items = data.components?.items ?? []
  const missing = items.filter((item) => !item.available)
  const score = data.temp_score
  const band = temperatureBand(score)
  const seriesPoints = series.data?.series ?? []
  const validSeries = seriesPoints.filter((point) => point.temp_score !== null)
  const seriesMin = validSeries.length > 0
    ? Math.min(...validSeries.map((point) => point.temp_score as number))
    : null
  const seriesMax = validSeries.length > 0
    ? Math.max(...validSeries.map((point) => point.temp_score as number))
    : null

  return (
    <section className="card p-4 sm:p-5 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-baseline gap-2">
          <h2 className="font-semibold">市场温度</h2>
          <span className="text-xs text-muted-foreground">{fmtDate(data.trade_date)}</span>
        </div>
        <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
          {band && <span className={band.textClass}>{band.label}</span>}
          <span>
            参与合成 {data.components?.available_count ?? 0}/{data.components?.total_count ?? 0} 项
          </span>
        </div>
      </div>

      {data.confidence !== 'ok' && (
        <div className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
          <span className="font-medium">
            {data.confidence === 'insufficient' ? '温度数据不足，暂不给出总分。' : '部分子项缺数据，总分由可用子项重新加权。'}
          </span>
          {missing.length > 0 && <span className="ml-1">缺少：{missing.map((item) => item.label).join('、')}</span>}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-[260px_1fr] gap-5 items-center">
        <Gauge score={score} />

        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-2.5">
          {items.map((item) => {
            const glyph = scoreGlyph(item.score)
            return (
              <div key={item.key} className="rounded-lg border border-border/60 bg-muted/30 p-3">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    {item.label}
                    {item.direction < 0 && (
                      <span className="rounded border border-border px-1 text-[10px] leading-4">降温</span>
                    )}
                  </span>
                  <span className="text-sm font-medium num">{formatRaw(item)}</span>
                </div>
                <div className="mt-1.5 flex items-center gap-2">
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-border/60">
                    {item.score !== null && (
                      <div
                        className="h-full rounded-full"
                        style={{ width: `${item.score}%`, backgroundColor: temperatureColor(item.score) }}
                      />
                    )}
                  </div>
                  <span className="w-7 text-right text-xs num text-muted-foreground">
                    {item.score === null ? '—' : item.score.toFixed(0)}
                  </span>
                  <span className={`w-3 text-center text-xs ${glyph.className}`}>{glyph.glyph}</span>
                </div>
                <p className="mt-1 truncate text-[11px] text-muted-foreground" title={item.note}>
                  {item.note}
                </p>
              </div>
            )
          })}
        </div>
      </div>

      <div className="border-t border-border/60 pt-2">
        <div className="mb-1 flex items-center justify-between text-[11px] text-muted-foreground">
          <span>近 {seriesPoints.length} 个交易日</span>
          {seriesMin !== null && seriesMax !== null && (
            <span className="num">
              区间 {seriesMin.toFixed(1)} – {seriesMax.toFixed(1)}
            </span>
          )}
        </div>
        <TemperatureSparkline points={seriesPoints} />
      </div>
    </section>
  )
}
