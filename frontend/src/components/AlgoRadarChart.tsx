/**
 * 通用因子雷达图（Recharts）
 * ===========================
 * 个股详情页用于展示 6 维因子评分（0–10）；null 值按 0 绘制并在图下方标注「—」。
 */
import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
} from 'recharts'

/** 雷达图维度 */
export interface RadarDatum {
  dimension: string
  value: number | null
}

interface Props {
  title?: string
  data: RadarDatum[]
  /** 量程上限，默认 10（评分统一 0–10 分制） */
  max?: number
  color?: string
  height?: number
}

export default function AlgoRadarChart({ title = '因子雷达', data, max = 10, color = 'var(--color-primary)', height = 300 }: Props) {
  if (data.length === 0) return null

  const chartData = data.map((item) => ({
    dimension: item.dimension,
    value: item.value === null ? 0 : item.value,
  }))

  return (
    <div className="card p-4">
      <h3 className="font-semibold text-sm mb-1">{title}</h3>
      <ResponsiveContainer width="100%" height={height}>
        <RadarChart data={chartData}>
          <PolarGrid stroke="var(--color-border)" />
          <PolarAngleAxis dataKey="dimension" tick={{ fontSize: 11 }} />
          <PolarRadiusAxis domain={[0, max]} tick={{ fontSize: 10 }} />
          <Tooltip formatter={(value) => [`${Number(value).toFixed(1)}/${max}`, '评分']} />
          <Radar dataKey="value" stroke={color} fill={color} fillOpacity={0.2} strokeWidth={2} name="评分" />
        </RadarChart>
      </ResponsiveContainer>
      {/* 数值图例（null 显示 —） */}
      <div className="grid grid-cols-3 gap-2 mt-1">
        {data.map((item) => (
          <div key={item.dimension} className="flex items-center justify-between text-[11px] px-1">
            <span className="text-muted-foreground">{item.dimension}</span>
            <span className="font-medium num">{item.value === null ? '—' : item.value.toFixed(1)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
