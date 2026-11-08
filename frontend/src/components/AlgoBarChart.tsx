/**
 * 通用横向柱状图（Recharts）
 * ===========================
 * 用于选股结果的 Top N 指标对比；数据为空或全为 null 时不渲染。
 */
import { useReducedMotion } from 'framer-motion'
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { chartColor } from '../lib/color'

/** 图表数据项 */
export interface BarChartDatum {
  name: string
  value: number | null
}

interface Props {
  title: string
  data: BarChartDatum[]
  /** 数值单位，展示在 tooltip 中 */
  unit?: string
  height?: number
}

export default function AlgoBarChart({ title, data, unit = '', height = 300 }: Props) {
  const reducedMotion = useReducedMotion()
  const usable = data.filter((item) => item.value !== null)
  if (usable.length === 0) return null

  return (
    <div className="card p-4">
      <h3 className="font-semibold text-sm mb-3">{title}</h3>
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={usable} layout="vertical" margin={{ left: 8, right: 16 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
          <XAxis type="number" tick={{ fontSize: 11 }} />
          <YAxis dataKey="name" type="category" tick={{ fontSize: 11 }} width={64} />
          <Tooltip formatter={(value) => [`${Number(value).toFixed(2)}${unit}`, '数值']} />
          <Bar dataKey="value" radius={[0, 4, 4, 0]} isAnimationActive={!reducedMotion}>
            {usable.map((_, index) => (
              <Cell key={index} fill={chartColor(index)} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
