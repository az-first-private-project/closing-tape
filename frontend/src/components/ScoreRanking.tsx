/**
 * 板块综合评分榜（契约 3.1 sectors / stats）
 * ============================================
 * 关键口径：
 *  - 评分统一 0–10 分制，评分条宽度 = score/10 * 100%；
 *  - 未评分（composite_score === null）显示「未评分」，绝不用 5 分兜底；
 *  - 展示已评分板块数 / 总板块数。
 *  - P3-2：每行接入近 30 个评分日综合分 sparkline（批量接口一次取回
 *    Top 10 的序列）；无历史时显示「—」，缺分日期断线，不用 0 补齐。
 *
 * 视觉：榜单化的排行条——前三名铜色奖牌，三面评分压缩为微型量条，
 * 走势线提示动能方向，综合分作为每行最重的读数。
 */
import { useMemo } from 'react'
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
import { useSectorScoreSparkline } from '../hooks/useDashboard'
import { fmtPct, fmtScore, pctColor, scorePercent } from '../lib/format'
import { chartColor } from '../lib/color'
import Sparkline from './Sparkline'

interface Props {
  sectors: SectorQuote[]
  stats?: DashboardStats
  /** 仪表盘解析后的交易日；sparkline 以此为截止日 */
  tradeDate?: string
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
    <div
      className="flex w-7 flex-col items-center gap-1"
      title={`${title}: ${value === null ? '未评分' : `${value.toFixed(1)}/10`}`}
    >
      <span className="text-[9px] leading-none text-muted-foreground/80">{title.slice(0, 1)}</span>
      <div className="h-1 w-full overflow-hidden rounded-full bg-border/50">
        <div
          className={`h-full rounded-full ${color} transition-[width] duration-700`}
          style={{ width: `${scorePercent(value)}%` }}
        />
      </div>
      <span className="num text-[9px] leading-none text-muted-foreground">
        {value === null ? '—' : value.toFixed(1)}
      </span>
    </div>
  )
}

/** 单行走势：Top 10 由整页一次批量请求覆盖，单行不再发请求 */
function ScoreSparkline({
  sectorName,
  scores,
  points,
  loading,
  failed,
}: {
  sectorName: string
  scores?: (number | null)[]
  points: number
  loading: boolean
  failed: boolean
}) {
  const validCount = scores?.filter((value) => value !== null).length ?? 0
  if (loading) {
    return <span className="inline-block h-[22px] w-[72px]" aria-hidden="true" />
  }
  // 0/1 个有效点画不出走势：统一显示「—」，不渲染灰色假线
  if (validCount <= 1) {
    return (
      <span
        className="text-[11px] text-muted-foreground"
        title={failed ? '评分历史加载失败' : validCount === 0 ? '暂无评分历史' : '评分历史不足'}
      >
        —
      </span>
    )
  }
  return (
    <Sparkline
      values={scores}
      days={30}
      width={72}
      height={22}
      domain={[0, 10]}
      color="var(--color-brass)"
      label={`${sectorName} · 近 ${points || scores.length} 个评分日综合评分`}
      formatValue={(value) => value.toFixed(1)}
      className="ml-auto w-[72px]"
    />
  )
}

export default function ScoreRanking({ sectors, stats, tradeDate, onSelect }: Props) {
  // 只对「已评分」板块排序取 Top 10
  const { scored, top } = useMemo(() => {
    const scoredRows = sectors.filter((sector) => sector.composite_score !== null)
    const topRows = [...scoredRows]
      .sort((left, right) => (right.composite_score ?? 0) - (left.composite_score ?? 0))
      .slice(0, 10)
    return { scored: scoredRows, top: topRows }
  }, [sectors])
  const topCodes = useMemo(() => top.map((sector) => sector.sector_code), [top])
  const history = useSectorScoreSparkline(topCodes, 30, tradeDate, top.length > 0)
  const historyMap = useMemo(() => {
    const map = new Map<string, (number | null)[]>()
    for (const item of history.data?.items ?? []) {
      map.set(item.sector_code, item.scores)
    }
    return map
  }, [history.data])

  if (!sectors.length) {
    return (
      <div className="card flex h-[300px] items-center justify-center p-6 text-[12px] text-muted-foreground">
        暂无板块评分数据
      </div>
    )
  }

  const scoredCount = stats?.scored_sectors ?? scored.length
  const totalCount = stats?.total_sectors ?? sectors.length
  const historyLoading = history.isLoading && !history.data

  return (
    <div className="card p-4">
      <div className="section-head">
        <h3 className="section-title">
          综合评分
          <span className="font-mono text-[10px] font-normal tracking-[0.14em] text-muted-foreground">
            TOP 10
          </span>
        </h3>
        <span className="num text-[10px] text-muted-foreground">
          已评分 {scoredCount}/{totalCount}
        </span>
      </div>

      {top.length === 0 ? (
        <div className="flex h-[240px] items-center justify-center px-4 text-center text-[12px] leading-relaxed text-muted-foreground">
          评分任务尚未运行，暂无评分数据。
          <br />
          评分由后端每日盘后计算，完成后此处会展示真实评分。
        </div>
      ) : (
        <>
          {/* 评分明细 */}
          <div className="mb-4 space-y-0.5">
            {top.map((sector, index) => (
              <motion.button
                key={sector.sector_code}
                type="button"
                initial={{ opacity: 0, x: -12 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: index * 0.035, ease: [0.16, 1, 0.3, 1] }}
                onClick={onSelect ? () => onSelect(sector.sector_code) : undefined}
                className={`row-accent group relative grid w-full grid-cols-[1.5rem_minmax(0,1fr)_auto_2.6rem] items-center gap-3
                  rounded-lg px-2 py-1.5 text-left transition-colors duration-200 hover:bg-muted/40
                  sm:grid-cols-[1.5rem_minmax(0,1fr)_auto_4.75rem_2.6rem]
                  ${onSelect ? 'cursor-pointer' : ''}`}
              >
                {/* 排名 */}
                <span className={`medal h-6 w-6 !text-[10px] ${index < 3 ? 'medal-brass' : ''}`}>
                  {index + 1}
                </span>

                {/* 板块名 + 涨跌 */}
                <div className="min-w-0">
                  <p className="truncate text-[12.5px] font-medium">{sector.sector_name}</p>
                  <p className={`num text-[10px] ${pctColor(sector.pct_chg)}`}>
                    {fmtPct(sector.pct_chg)}
                  </p>
                </div>

                {/* 三维分数条 */}
                <div className="flex items-center gap-1.5">
                  {DIMENSIONS.map((dimension) => (
                    <ScoreBar
                      key={dimension.key}
                      value={sector[dimension.key]}
                      color={dimension.color}
                      title={dimension.title}
                    />
                  ))}
                </div>

                {/* 30 个评分日综合分走势（小屏隐藏，避免挤压名称与评分） */}
                <div className="hidden text-right sm:block">
                  <ScoreSparkline
                    sectorName={sector.sector_name}
                    scores={historyMap.get(sector.sector_code)}
                    points={history.data?.dates.length ?? 0}
                    loading={historyLoading}
                    failed={history.isError}
                  />
                </div>

                {/* 综合分 */}
                <div className="text-right">
                  <p className="num text-[17px] font-semibold leading-none tracking-[-0.02em]">
                    {sector.composite_score === null ? (
                      <span className="text-[10px] font-normal text-muted-foreground">未评分</span>
                    ) : (
                      fmtScore(sector.composite_score)
                    )}
                  </p>
                </div>
              </motion.button>
            ))}
          </div>

          {/* 综合评分横向对比图 */}
          <div className="border-t border-border/60 pt-3">
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={top} layout="vertical" margin={{ left: 4, right: 16, top: 4, bottom: 0 }}>
                <CartesianGrid strokeDasharray="2 4" stroke="var(--color-border)" horizontal={false} />
                <XAxis type="number" domain={[0, 10]} tick={{ fontSize: 10 }} tickLine={false} axisLine={false} />
                <YAxis
                  type="category"
                  dataKey="sector_name"
                  tick={{ fontSize: 10 }}
                  tickLine={false}
                  axisLine={false}
                  width={68}
                />
                <Tooltip
                  cursor={{ fill: 'color-mix(in srgb, var(--color-brass) 8%, transparent)' }}
                  formatter={(value) => [`${Number(value).toFixed(1)}/10`, '综合评分']}
                />
                <Bar dataKey="composite_score" radius={[0, 3, 3, 0]} name="综合评分" barSize={9}>
                  {top.map((_, index) => (
                    <Cell key={index} fill={chartColor(index)} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </div>
  )
}
