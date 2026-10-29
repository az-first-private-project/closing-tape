/**
 * 板块综合评分榜（契约 3.1 sectors / stats）
 * ============================================
 * 关键口径：
 *  - 评分统一 0–10 分制，评分条宽度 = score/10 * 100%；
 *  - 未评分（composite_score === null）显示「未评分」，绝不用 5 分兜底；
 *  - 展示已评分板块数 / 总板块数。
 */
import { motion } from 'framer-motion'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { DashboardStats, SectorQuote } from '../types/market'
import { fmtPct, fmtScore, pctColor, scorePercent } from '../lib/format'
import { chartColor } from '../lib/color'

interface Props {
  sectors: SectorQuote[]
  stats?: DashboardStats
  onSelect?: (sectorCode: string) => void
}

/** 三维评分配置 */
const DIMENSIONS = [
  { key: 'technical_score', label: '技', title: '技术面', color: 'bg-chart-1' },
  { key: 'sentiment_score', label: '情', title: '情绪面', color: 'bg-chart-4' },
  { key: 'news_score', label: '消', title: '消息面', color: 'bg-chart-2' },
] as const

/** 单个评分条（null → 不画条，显示 —） */
function ScoreBar({ value, color, title }: { value: number | null; color: string; title: string }) {
  return (
    <div className="flex flex-col items-center" title={`${title}: ${value === null ? '未评分' : `${value.toFixed(1)}/10`}`}>
      <span className="text-[11px] text-muted-foreground">{title.slice(0, 1)}</span>
      <div className="w-8 h-1.5 bg-muted rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full transition-[width]`} style={{ width: `${scorePercent(value)}%` }} />
      </div>
      <span className="text-[11px] num">{value === null ? '—' : value.toFixed(1)}</span>
    </div>
  )
}

export default function ScoreRanking({ sectors, stats, onSelect }: Props) {
  if (!sectors.length) {
    return (
      <div className="card p-6 flex items-center justify-center h-[300px] text-muted-foreground text-sm">
        暂无板块评分数据
      </div>
    )
  }

  // 只对「已评分」板块排序取 Top 10
  const scored = sectors.filter((sector) => sector.composite_score !== null)
  const top = [...scored]
    .sort((left, right) => (right.composite_score ?? 0) - (left.composite_score ?? 0))
    .slice(0, 10)
  const scoredCount = stats?.scored_sectors ?? scored.length
  const totalCount = stats?.total_sectors ?? sectors.length

  return (
    <div className="card p-4">
      <div className="flex flex-wrap items-center justify-between gap-1 mb-3">
        <h3 className="font-semibold">🏆 综合评分 Top 10</h3>
        <span className="text-[11px] text-muted-foreground">
          已评分 {scoredCount}/{totalCount} 个板块
        </span>
      </div>

      {top.length === 0 ? (
        <div className="flex items-center justify-center h-[240px] text-muted-foreground text-sm text-center px-4">
          评分任务尚未运行，暂无评分数据。
          <br />
          评分由后端每日盘后计算，完成后此处会展示真实评分。
        </div>
      ) : (
        <>
          {/* 评分明细 */}
          <div className="space-y-1 mb-4">
            {top.map((sector, index) => (
              <motion.button
                key={sector.sector_code}
                type="button"
                initial={{ opacity: 0, x: -16 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: index * 0.04 }}
                onClick={onSelect ? () => onSelect(sector.sector_code) : undefined}
                className={`w-full flex items-center gap-3 p-2 rounded-lg transition-colors text-left ${
                  onSelect ? 'cursor-pointer hover:bg-muted' : 'hover:bg-muted'
                }`}
              >
                {/* 排名 */}
                <span
                  className={`w-6 h-6 shrink-0 rounded-full flex items-center justify-center text-xs font-bold num ${
                    index < 3
                      ? 'bg-warning/10 text-warning'
                      : 'bg-muted text-muted-foreground'
                  }`}
                >
                  {index + 1}
                </span>

                {/* 板块名 + 涨跌 */}
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{sector.sector_name}</p>
                  <p
                    className={`text-xs num ${pctColor(sector.pct_chg)}`}
                  >
                    {fmtPct(sector.pct_chg)}
                  </p>
                </div>

                {/* 三维分数条 */}
                <div className="flex items-center gap-1">
                  {DIMENSIONS.map((dimension) => (
                    <ScoreBar
                      key={dimension.key}
                      value={sector[dimension.key]}
                      color={dimension.color}
                      title={dimension.title}
                    />
                  ))}
                </div>

                {/* 综合分 */}
                <div className="text-right min-w-[3rem]">
                  <p className="text-lg font-bold num leading-tight">
                    {sector.composite_score === null ? (
                      <span className="text-xs text-muted-foreground font-normal">未评分</span>
                    ) : (
                      fmtScore(sector.composite_score)
                    )}
                  </p>
                  <p className="text-[11px] text-muted-foreground">综合</p>
                </div>
              </motion.button>
            ))}
          </div>

          {/* 综合评分横向对比图 */}
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={top} layout="vertical" margin={{ left: 8, right: 12 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
              <XAxis type="number" domain={[0, 10]} tick={{ fontSize: 11 }} />
              <YAxis type="category" dataKey="sector_name" tick={{ fontSize: 11 }} width={64} />
              <Tooltip formatter={(value) => [`${Number(value).toFixed(1)}/10`, '综合评分']} />
              <Bar dataKey="composite_score" radius={[0, 4, 4, 0]} name="综合评分">
                {top.map((_, index) => (
                  <Cell key={index} fill={chartColor(index)} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </>
      )}
    </div>
  )
}
