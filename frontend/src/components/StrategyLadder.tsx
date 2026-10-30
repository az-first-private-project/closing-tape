/**
 * 策略天梯（V6 T5）
 * =================
 * 信号级 5/10/20 日前瞻收益：回答"最近哪个策略能用"。
 * - 口径：T 日收盘出信号、T+1 开盘买入，收益含复权，超额对 sh000300；
 * - 样本 < 30 标 `low_sample`，数值弱化并提示"低样本"；
 * - 未成熟 / 不可成交样本由后端剔除，顶部说明剔除数量；
 * - sparkline 为近 20 个出信号日的胜率，纯内联 SVG，固定 0–100% 纵轴。
 */
import { useMemo, useState } from 'react'
import { Info } from 'lucide-react'
import { useLadder } from '../hooks/useAlgo'
import type { LadderHorizonStats, LadderItem, LadderSparkPoint } from '../types/algo'
import { ErrorState, EmptyState } from './StateViews'
import FreshnessBadge from './FreshnessBadge'
import { fmtInt, fmtPct, fmtRatio, pctColor } from '../lib/format'

const WINDOWS = [20, 60, 120] as const
const HORIZONS = [5, 10, 20] as const

const WINDOW_LABEL: Record<number, string> = { 20: '近 20 日', 60: '近 60 日', 120: '近 120 日' }

/** 胜率 sparkline：固定 0–100% 纵轴，缺失点断开 */
function WinRateSparkline({ points }: { points: LadderSparkPoint[] }) {
  if (points.length === 0) {
    return <span className="text-[11px] text-muted-foreground">—</span>
  }
  const width = 104
  const height = 30
  const padding = 3
  const step = points.length > 1 ? width / (points.length - 1) : 0
  const y = (value: number) =>
    padding + (1 - Math.min(1, Math.max(0, value))) * (height - padding * 2)

  const segments: { x: number; y: number }[][] = []
  let current: { x: number; y: number }[] = []
  points.forEach((point, index) => {
    if (point.win_rate === null) {
      if (current.length > 0) segments.push(current)
      current = []
      return
    }
    current.push({ x: index * step, y: y(point.win_rate) })
  })
  if (current.length > 0) segments.push(current)

  const last = points[points.length - 1]
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="w-[104px] h-[30px]"
      role="img"
      aria-label={`近 ${points.length} 个信号日胜率走势`}
    >
      <title>{`${last.trade_date} 胜率 ${fmtRatio(last.win_rate)}`}</title>
      <line
        x1={0}
        x2={width}
        y1={y(0.5)}
        y2={y(0.5)}
        stroke="var(--color-border)"
        strokeWidth={1}
        strokeDasharray="2 3"
      />
      {segments.map((segment, index) =>
        segment.length === 1 ? (
          <circle
            key={index}
            cx={segment[0].x}
            cy={segment[0].y}
            r={1.8}
            fill="var(--color-chart-1)"
          />
        ) : (
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
        ),
      )}
    </svg>
  )
}

/** 样本状态提示：0 样本时区分「待成熟」与「全部不可成交/缺数据」 */
function sampleHint(stats: LadderHorizonStats): '待成熟' | '无有效样本' | '低样本' | null {
  if (stats.sample_signals === 0) {
    return stats.immature > 0 ? '待成熟' : '无有效样本'
  }
  return stats.low_sample ? '低样本' : null
}

/** 单个 horizon 的「平均超额 / 胜率 / 样本」堆叠单元格 */
function HorizonCell({
  stats,
  active,
  totalSignals,
}: {
  stats: LadderHorizonStats
  active: boolean
  totalSignals: number
}) {
  const hint = totalSignals === 0 ? null : sampleHint(stats)
  return (
    <div className={`flex flex-col items-end gap-0.5 ${active ? 'text-foreground' : ''}`}>
      <span className={`num text-sm font-medium ${pctColor(stats.avg_excess)}`}>
        {fmtPct(stats.avg_excess)}
      </span>
      <span className="text-[11px] text-muted-foreground num">
        胜 {fmtRatio(stats.win_rate)}
      </span>
      <span className={`text-[11px] num ${hint === '低样本' ? 'text-warning' : 'text-muted-foreground'}`}>
        n={fmtInt(stats.sample_signals)}
        {hint && <span title={hint === '低样本' ? '有效样本少于 30，结论不稳定' : undefined}> · {hint}</span>}
      </span>
    </div>
  )
}

function rankBadge(item: LadderItem) {
  if (item.sample_signals === 0) {
    return <span className="text-muted-foreground">—</span>
  }
  return (
    <span
      className={`inline-flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold num ${
        item.rank === 1
          ? 'bg-warning/15 text-warning border border-warning/40'
          : 'bg-muted text-foreground border border-border'
      }`}
    >
      {item.rank}
    </span>
  )
}

export default function StrategyLadder({
  onSelectAlgo,
  selectedCode,
}: {
  onSelectAlgo: (algoCode: string) => void
  selectedCode?: string
}) {
  const [window, setWindow] = useState<number>(60)
  const [horizon, setHorizon] = useState<number>(20)
  const query = useLadder(window, horizon)
  const data = query.data

  const items = data?.items ?? []
  const selectedStats = useMemo(
    () => items.map((item) => item.horizons[String(horizon)]),
    [items, horizon],
  )

  return (
    <section className="card p-4 sm:p-5 space-y-4" aria-labelledby="ladder-title">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="ladder-title" className="text-base font-semibold flex items-center gap-2">
            策略天梯
            <span className="text-[11px] font-normal px-2 py-0.5 rounded-full border border-border bg-muted text-muted-foreground">
              信号级 {horizon} 日收益
            </span>
          </h2>
          <p className="text-xs text-muted-foreground mt-1">
            T 日收盘出信号 → T+1 开盘买入，含复权；超额对比沪深 300（{data?.benchmark ?? 'sh000300'}）。
            点击行选择下方策略。
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <div
            className="inline-flex rounded-lg border border-border overflow-hidden"
            role="group"
            aria-label="回看窗口"
          >
            {WINDOWS.map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={window === value}
                onClick={() => setWindow(value)}
                className={`px-2.5 py-1.5 text-xs transition-colors ${
                  window === value
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-background text-muted-foreground hover:bg-muted'
                }`}
              >
                {WINDOW_LABEL[value]}
              </button>
            ))}
          </div>

          <div
            className="inline-flex rounded-lg border border-border overflow-hidden"
            role="group"
            aria-label="收益周期"
          >
            {HORIZONS.map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={horizon === value}
                onClick={() => setHorizon(value)}
                className={`px-2.5 py-1.5 text-xs transition-colors ${
                  horizon === value
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-background text-muted-foreground hover:bg-muted'
                }`}
              >
                {value} 日
              </button>
            ))}
          </div>

          {data?.freshness && <FreshnessBadge items={[data.freshness]} title="前瞻统计" />}
        </div>
      </div>

      {data && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
          <span>
            数据截至 <span className="num text-foreground">{data.trade_date}</span>（窗口自 {data.window_start}）
          </span>
          <span className="inline-flex items-center gap-1">
            <Info className="w-3 h-3" />
            未成熟 {fmtInt(data.immature_total)} 条、不可成交 {fmtInt(data.unfillable_total)} 条已剔除
          </span>
          <span>胜率 = 信号级 ret &gt; 0 占比，与回测 FIFO 回合胜率口径不同</span>
        </div>
      )}

      {query.isLoading && !data ? (
        <p className="text-sm text-muted-foreground py-8 text-center">正在加载信号前瞻统计…</p>
      ) : query.isError ? (
        <ErrorState title="策略天梯加载失败" error={query.error} onRetry={() => query.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState
          title="暂无前瞻统计"
          description="需要先产生非回测选股 run（每日 16:30），17:10 的 signal_perf 任务会统计其 T+1 起的 5/10/20 日收益。"
        />
      ) : (
        <div className="overflow-x-auto -mx-1">
          <table className="w-full min-w-[760px] text-sm">
            <thead>
              <tr className="text-[11px] text-muted-foreground border-b border-border">
                <th className="text-left font-medium py-2 pl-1 w-14">排名</th>
                <th className="text-left font-medium py-2 min-w-[10rem]">策略</th>
                {HORIZONS.map((value) => (
                  <th
                    key={value}
                    className={`text-right font-medium py-2 px-2 ${horizon === value ? 'text-primary' : ''}`}
                  >
                    {value} 日
                  </th>
                ))}
                <th className="text-right font-medium py-2 pr-1">近 20 期 {horizon} 日胜率</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item, index) => {
                const stats = selectedStats[index]
                const hint = sampleHint(item)
                return (
                  <tr
                    key={item.algo_code}
                    tabIndex={0}
                    role="button"
                    aria-label={`选择策略 ${item.algo_name}`}
                    onClick={() => onSelectAlgo(item.algo_code)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault()
                        onSelectAlgo(item.algo_code)
                      }
                    }}
                    className={`border-b border-border/60 cursor-pointer transition-colors hover:bg-muted/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary ${
                      selectedCode === item.algo_code ? 'bg-primary/5' : ''
                    }`}
                  >
                    <td className="py-2.5 pl-1">{rankBadge(item)}</td>
                    <td className="py-2.5">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="font-medium truncate">{item.algo_name}</span>
                        <span className="text-[10px] font-mono text-muted-foreground uppercase">
                          {item.algo_code}
                        </span>
                        {item.total_signals === 0 ? (
                          <span
                            className="shrink-0 text-[10px] px-1.5 py-0.5 rounded border border-border bg-muted text-muted-foreground"
                            title="所选窗口内没有该策略的非回测信号"
                          >
                            无信号
                          </span>
                        ) : hint === '低样本' ? (
                          <span
                            className="shrink-0 text-[10px] px-1.5 py-0.5 rounded border border-warning/40 bg-warning/10 text-warning"
                            title={`有效样本 ${item.sample_signals} 条，少于 30 条`}
                          >
                            低样本
                          </span>
                        ) : hint ? (
                          <span
                            className="shrink-0 text-[10px] px-1.5 py-0.5 rounded border border-border bg-muted text-muted-foreground"
                            title={hint === '待成熟' ? '信号已有，但收益窗口尚未走完' : '已成熟样本均不可成交或缺少行情'}
                          >
                            {hint}
                          </span>
                        ) : null}
                      </div>
                      <div className="text-[11px] text-muted-foreground num mt-0.5">
                        中位 {fmtPct(item.median_ret)} · 最好 {fmtPct(item.best)} · 最差 {fmtPct(item.worst)}
                      </div>
                    </td>
                    {HORIZONS.map((value) => (
                      <td
                        key={value}
                        className={`text-right py-2.5 px-2 align-top ${horizon === value ? 'bg-muted/40' : ''}`}
                      >
                        <HorizonCell
                          stats={item.horizons[String(value)]}
                          active={horizon === value}
                          totalSignals={item.total_signals}
                        />
                      </td>
                    ))}
                    <td className="text-right py-2.5 pr-1">
                      <div className="flex flex-col items-end gap-1">
                        <WinRateSparkline points={stats?.spark ?? []} />
                        <span className={`text-[11px] num ${hint === '低样本' ? 'text-warning' : 'text-muted-foreground'}`}>
                          {item.total_signals === 0 ? '无信号' : hint ?? '样本充足'}
                        </span>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-[11px] text-muted-foreground">
        5/10/20 日均按各自的成熟窗口统计；停牌或一字板导致 T+1 无法成交的信号不计入胜率与均值。
        样本不足 30 条时排序仅作参考。
      </p>
    </section>
  )
}
