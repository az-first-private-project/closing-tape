/**
 * Sparkline（V6 T14）
 * =================
 * 纯 SVG 微型走势线，零依赖、无 canvas、无浮层。
 * - null 断点跳过并分段；0/1 个有效点画灰色平线；
 * - 默认颜色由首末有效值方向决定（红涨绿跌），可显式覆盖；
 * - hover 通过原生 <title> 展示末值与区间，不引 tooltip 库；
 * - viewBox + preserveAspectRatio="none" 自适应容器宽度，描边用
 *   vectorEffect 避免被横向拉伸。
 */
import { useMemo } from 'react'
import { colorVar } from '../lib/format'
import { buildSparklineGeometry } from '../lib/sparkline'

export interface SparklineProps {
  values: (number | null)[]
  days?: number
  width?: number
  height?: number
  color?: string
  baseline?: number
  fill?: boolean
  label?: string
  /** 固定纵轴（如胜率 0–1、温度 0–100），缺省按数据自适应 */
  domain?: [number, number]
  formatValue?: (value: number) => string
  className?: string
}

function defaultFormat(value: number): string {
  if (Number.isInteger(value)) return String(value)
  return value.toFixed(2)
}

export default function Sparkline({
  values,
  days = 30,
  width = 120,
  height = 32,
  color,
  baseline,
  fill = false,
  label,
  domain,
  formatValue = defaultFormat,
  className = 'w-full',
}: SparklineProps) {
  const geometry = useMemo(
    () =>
      buildSparklineGeometry({
        values,
        days,
        width,
        height,
        domain,
        baseline: baseline ?? null,
      }),
    [values, days, width, height, domain, baseline],
  )

  const { segments, min, max, last, missingCount, points, flat, dataInsufficient } = geometry
  const stroke = dataInsufficient
    ? 'var(--color-flat)'
    : color ?? (flat ? 'var(--color-flat)' : colorVar((last ?? 0) - (geometry.first ?? 0)))

  const rangeText =
    min !== null && max !== null ? `${formatValue(min)} – ${formatValue(max)}` : '无有效数据'
  const titleParts = [
    label ?? '走势',
    `末值 ${last === null ? '—' : formatValue(last)}`,
    `区间 ${rangeText}`,
    `${points} 点`,
  ]
  if (missingCount > 0) titleParts.push(`${missingCount} 点缺数据`)
  if (dataInsufficient) titleParts.push('数据不足')
  const title = titleParts.join(' · ')

  return (
    <svg
      viewBox={`0 0 ${geometry.width} ${geometry.height}`}
      preserveAspectRatio="none"
      className={className}
      style={{ height: geometry.height }}
      role="img"
      aria-label={title}
      data-sparkline="true"
      data-points={points}
      data-missing={missingCount}
    >
      <title>{title}</title>

      {geometry.baselineY !== null && (
        <line
          x1={0}
          x2={geometry.width}
          y1={geometry.baselineY}
          y2={geometry.baselineY}
          stroke="var(--color-border)"
          strokeWidth={1}
          strokeDasharray="2 3"
          vectorEffect="non-scaling-stroke"
        />
      )}

      {fill &&
        !dataInsufficient &&
        segments.map((segment, index) => {
          if (segment.length < 2) return null
          const first = segment[0]
          const end = segment[segment.length - 1]
          const path = [
            `M ${first.x},${first.y}`,
            ...segment.slice(1).map((point) => `L ${point.x},${point.y}`),
            `L ${end.x},${geometry.height}`,
            `L ${first.x},${geometry.height}`,
            'Z',
          ].join(' ')
          return <path key={`area-${index}`} d={path} fill={stroke} fillOpacity={0.12} stroke="none" />
        })}

      {segments.map((segment, index) => {
        if (segment.length === 1) {
          const point = segment[0]
          return (
            <line
              key={`dot-${index}`}
              x1={point.x - 0.6}
              x2={point.x + 0.6}
              y1={point.y}
              y2={point.y}
              stroke={stroke}
              strokeWidth={1.8}
              strokeLinecap="round"
              vectorEffect="non-scaling-stroke"
            />
          )
        }
        return (
          <polyline
            key={`line-${index}`}
            points={segment.map((point) => `${point.x},${point.y}`).join(' ')}
            fill="none"
            stroke={stroke}
            strokeWidth={1.5}
            strokeLinejoin="round"
            strokeLinecap="round"
            vectorEffect="non-scaling-stroke"
          />
        )
      })}
    </svg>
  )
}
