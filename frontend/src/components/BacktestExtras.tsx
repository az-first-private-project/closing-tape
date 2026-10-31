/**
 * 回测派生视图（V6 T9）
 * =========================
 * 月收益热力图 / 回撤曲线 / 20 日滚动超额 / 持仓行业分布。
 * 数据全部来自后端已落库回测的派生结果，本组件不重算、不请求。
 */
import { useMemo, useState, type ReactNode } from 'react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { chartColor, trendHeat } from '../lib/color'
import { fmtPct, pctColor, shortDate } from '../lib/format'
import type {
  BacktestResult,
  IndustrySnapshot,
  IndustryWeight,
  MonthlyReturn,
} from '../types/backtest'

const MONTHS = Array.from({ length: 12 }, (_, index) => index + 1)
const CASH_LABEL = '现金'
const MERGED_LABEL = '其他行业'
const MAX_INDUSTRY_LEGS = 4
const MAX_EXPOSURE_DATES = 12

function Section({
  title,
  subtitle,
  defaultOpen = true,
  children,
}: {
  title: string
  subtitle?: ReactNode
  defaultOpen?: boolean
  children: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <section className="rounded-lg border border-border">
      <button
        type="button"
        onClick={() => setOpen((previous) => !previous)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm font-medium transition-colors hover:bg-muted/40 rounded-lg"
      >
        <span className="flex items-center gap-1.5">
          <span className="w-3 text-muted-foreground">{open ? '▾' : '▸'}</span>
          {title}
        </span>
        {subtitle && <span className="text-[11px] font-normal text-muted-foreground">{subtitle}</span>}
      </button>
      {open && <div className="px-3 pb-3 pt-1">{children}</div>}
    </section>
  )
}

function EmptyHint({ text }: { text: string }) {
  return <p className="py-6 text-center text-xs text-muted-foreground">{text}</p>
}

interface HeatRow {
  year: string
  months: (MonthlyReturn | undefined)[]
  total: number | null
}

function useMonthlyHeat(monthly: MonthlyReturn[]) {
  return useMemo(() => {
    const byYear = new Map<string, Map<number, MonthlyReturn>>()
    for (const item of monthly) {
      const [yearText, monthText] = item.ym.split('-')
      const month = Number(monthText)
      if (!yearText || !Number.isFinite(month) || month < 1 || month > 12) continue
      if (!byYear.has(yearText)) byYear.set(yearText, new Map())
      byYear.get(yearText)!.set(month, item)
    }
    const years = [...byYear.keys()].sort().reverse()
    const rows: HeatRow[] = years.map((year) => {
      const map = byYear.get(year)!
      const months = MONTHS.map((month) => map.get(month))
      const returns = months
        .map((item) => item?.ret)
        .filter((value): value is number => typeof value === 'number')
      const growth = returns.reduce((accumulator, value) => accumulator * (1 + value / 100), 1)
      return { year, months, total: returns.length > 0 ? (growth - 1) * 100 : null }
    })
    const averages = MONTHS.map((month) => {
      const values = years
        .map((year) => byYear.get(year)!.get(month)?.ret)
        .filter((value): value is number => typeof value === 'number')
      return values.length > 0
        ? values.reduce((accumulator, value) => accumulator + value, 0) / values.length
        : null
    })
    return { rows, averages }
  }, [monthly])
}

function signed(value: number, digits = 1): string {
  // 先归一化，避免 -0.04 → "-0.0" 这类显示噪声
  const rounded = Number(value.toFixed(digits))
  return `${rounded > 0 ? '+' : ''}${rounded.toFixed(digits)}`
}

function MonthlyHeatmap({ monthly }: { monthly: MonthlyReturn[] }) {
  const { rows, averages } = useMonthlyHeat(monthly)
  if (rows.length === 0) return <EmptyHint text="暂无月度收益数据" />

  return (
    <div className="space-y-2">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[780px] border-separate border-spacing-0.5 text-[11px]">
          <thead>
            <tr className="text-muted-foreground">
              <th className="w-12 px-1 py-1 text-left font-normal">年份</th>
              {MONTHS.map((month) => (
                <th key={month} className="px-1 py-1 text-center font-normal">
                  {month}月
                </th>
              ))}
              <th className="w-16 px-1 py-1 text-right font-normal">全年</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.year}>
                <th className="px-1 text-left font-medium num">{row.year}</th>
                {row.months.map((item, index) => {
                  const value = item?.ret
                  const heat = typeof value === 'number' ? trendHeat(value, 5) : null
                  return (
                    <td key={index} className="p-0">
                      <div
                        className="relative flex h-9 items-center justify-center rounded"
                        style={heat ? { background: heat.background, color: heat.color } : undefined}
                        title={
                          item
                            ? `${item.ym} 收益 ${fmtPct(item.ret)}，基准 ${fmtPct(item.benchmark)}，超额 ${fmtPct(item.excess)}`
                            : `${row.year}-${String(index + 1).padStart(2, '0')} 无数据`
                        }
                      >
                        {typeof value === 'number' ? (
                          <span className="num font-medium">{signed(value)}</span>
                        ) : (
                          <span className="text-muted-foreground/50">—</span>
                        )}
                        {item?.excess != null && (
                          <span className="absolute right-1 top-0.5 text-[9px] num opacity-70">
                            {signed(item.excess)}
                          </span>
                        )}
                      </div>
                    </td>
                  )
                })}
                <td className={`px-1 text-right font-medium num ${pctColor(row.total)}`}>
                  {row.total == null ? '—' : fmtPct(row.total, 1)}
                </td>
              </tr>
            ))}
            <tr>
              <th className="px-1 text-left font-normal text-muted-foreground">月均</th>
              {averages.map((value, index) => (
                <td key={index} className={`px-1 text-center num ${pctColor(value)}`}>
                  {value == null ? '—' : signed(value)}
                </td>
              ))}
              <td />
            </tr>
          </tbody>
        </table>
      </div>
      <p className="text-[11px] text-muted-foreground">
        单元格为当月收益（%），右上角小字为超额收益；色阶以 ±5% 饱和，全年为逐月复利。
      </p>
    </div>
  )
}

function DrawdownChart({ result }: { result: BacktestResult }) {
  const curve = result.drawdown_curve
  if (curve.length === 0) return <EmptyHint text="暂无回撤数据" />
  return (
    <ResponsiveContainer width="100%" height={220}>
      <AreaChart data={curve} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
        <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={shortDate} minTickGap={40} />
        <YAxis
          tick={{ fontSize: 11 }}
          domain={['auto', 0]}
          tickFormatter={(value) => `${Number(value).toFixed(0)}%`}
        />
        <Tooltip
          formatter={(value) => [`${Number(value).toFixed(2)}%`, '回撤']}
          labelFormatter={(label) => `日期 ${label}`}
        />
        <Area
          type="monotone"
          dataKey="drawdown"
          name="回撤"
          stroke="var(--color-down)"
          fill="var(--color-down)"
          fillOpacity={0.22}
          strokeWidth={1.5}
          dot={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}

function RollingExcessChart({ result }: { result: BacktestResult }) {
  const points = result.rolling_excess_20d
  const valid = points.filter((item) => item.value != null).length
  if (valid === 0) return <EmptyHint text="样本不足 20 个交易日，暂无滚动超额" />
  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={points} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
        <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={shortDate} minTickGap={40} />
        <YAxis tick={{ fontSize: 11 }} tickFormatter={(value) => `${Number(value).toFixed(0)}%`} />
        <Tooltip
          formatter={(value) => [`${Number(value).toFixed(2)}%`, '滚动超额']}
          labelFormatter={(label) => `日期 ${label}`}
        />
        <ReferenceLine y={0} stroke="var(--color-muted-foreground)" strokeDasharray="4 4" />
        <Line
          type="monotone"
          dataKey="value"
          name="滚动超额"
          stroke="var(--color-primary)"
          strokeWidth={1.6}
          dot={false}
          connectNulls={false}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}

type DisplaySnapshot = IndustrySnapshot & { label: string; merged?: boolean }

interface ExposureView {
  display: DisplaySnapshot[]
  topNames: string[]
  hasMerged: boolean
}

function buildExposureView(snapshots: IndustrySnapshot[]): ExposureView | null {
  if (snapshots.length === 0) return null
  const ordered = [...snapshots].reverse()
  let display: DisplaySnapshot[]

  if (ordered.length <= MAX_EXPOSURE_DATES) {
    display = ordered.map((item) => ({ ...item, label: item.date }))
  } else {
    const keep = ordered.slice(0, MAX_EXPOSURE_DATES - 1)
    const rest = ordered.slice(MAX_EXPOSURE_DATES - 1)
    const names = new Set<string>()
    rest.forEach((item) => item.industries.forEach((entry) => names.add(entry.name)))
    const industries: IndustryWeight[] = [...names]
      .map((name) => {
        const entries = rest.map((item) => item.industries.find((entry) => entry.name === name))
        const weight = entries.reduce((accumulator, entry) => accumulator + (entry?.weight ?? 0), 0) / rest.length
        const value = entries.reduce((accumulator, entry) => accumulator + (entry?.value ?? 0), 0) / rest.length
        const count = Math.round(
          entries.reduce((accumulator, entry) => accumulator + (entry?.count ?? 0), 0) / rest.length,
        )
        return { name, weight, value: Math.round(value * 100) / 100, count }
      })
      .filter((entry) => entry.weight > 0.0001)
      .sort((a, b) => b.weight - a.weight)
    const cashEntry = industries.find((entry) => entry.name === CASH_LABEL)
    const stockCount = Math.round(
      rest.reduce((accumulator, item) => accumulator + item.stock_count, 0) / rest.length,
    )
    display = [
      ...keep.map((item) => ({ ...item, label: item.date })),
      {
        date: '',
        label: `其余 ${rest.length} 期`,
        merged: true,
        total_value: 0,
        cash: Math.round((cashEntry?.value ?? 0) * 100) / 100,
        cash_weight: cashEntry?.weight ?? 0,
        stock_count: stockCount,
        other_count: Math.round(
          rest.reduce((accumulator, item) => accumulator + item.other_count, 0) / rest.length,
        ),
        industries,
      },
    ]
  }

  const averageByName = new Map<string, number>()
  for (const snapshot of display) {
    for (const entry of snapshot.industries) {
      if (entry.name === CASH_LABEL) continue
      averageByName.set(entry.name, (averageByName.get(entry.name) ?? 0) + entry.weight / display.length)
    }
  }
  const topNames = [...averageByName.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, MAX_INDUSTRY_LEGS)
    .map(([name]) => name)

  const hasMerged = display.some((snapshot) =>
    snapshot.industries.some(
      (entry) => entry.name !== CASH_LABEL && !topNames.includes(entry.name) && entry.weight > 0.0001,
    ),
  )
  return { display, topNames, hasMerged }
}

function IndustryExposure({ snapshots }: { snapshots: IndustrySnapshot[] }) {
  const view = useMemo(() => buildExposureView(snapshots), [snapshots])
  if (!view) return <EmptyHint text="暂无持仓行业数据" />

  const colorByName = new Map<string, string>(
    view.topNames.map((name, index) => [name, chartColor(index)]),
  )
  const mergedColor = chartColor(view.topNames.length)
  const cashColor = 'var(--color-muted-foreground)'

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        {view.display.map((snapshot) => {
          const segments = view.topNames.map((name) => ({
            name,
            weight: snapshot.industries.find((entry) => entry.name === name)?.weight ?? 0,
            color: colorByName.get(name)!,
          }))
          const otherWeight = snapshot.industries
            .filter((entry) => entry.name !== CASH_LABEL && !view.topNames.includes(entry.name))
            .reduce((accumulator, entry) => accumulator + entry.weight, 0)
          if (otherWeight > 0.0001) {
            segments.push({ name: MERGED_LABEL, weight: otherWeight, color: mergedColor })
          }
          const cashWeight = snapshot.industries.find((entry) => entry.name === CASH_LABEL)?.weight ?? 0
          segments.push({ name: CASH_LABEL, weight: cashWeight, color: cashColor })

          const tooltip = [
            `${snapshot.merged ? snapshot.label : `调仓日 ${snapshot.label}`}`,
            ...segments
              .filter((segment) => segment.weight > 0.0001)
              .map((segment) => `${segment.name} ${segment.weight.toFixed(2)}%`),
            ...(snapshot.other_count > 0 ? [`无行业标的 ${snapshot.other_count} 只`] : []),
          ].join('\n')

          return (
            <div key={snapshot.label} className="grid grid-cols-[72px_1fr_84px] items-center gap-2">
              <span className={`text-[11px] num ${snapshot.merged ? 'text-muted-foreground' : ''}`}>
                {snapshot.merged ? snapshot.label : shortDate(snapshot.label)}
              </span>
              <div
                className="flex h-4 w-full overflow-hidden rounded-sm bg-muted"
                title={tooltip}
                role="img"
                aria-label={tooltip.replace(/\n/g, '，')}
              >
                {segments.map((segment) => (
                  <div
                    key={segment.name}
                    className="h-full"
                    style={{
                      width: `${segment.weight}%`,
                      minWidth: segment.weight > 0.0001 ? 1 : 0,
                      background: segment.color,
                      opacity: segment.name === CASH_LABEL ? 0.4 : 1,
                    }}
                  />
                ))}
              </div>
              <span className="text-right text-[10px] text-muted-foreground num">
                现金 {cashWeight.toFixed(1)}%
              </span>
            </div>
          )
        })}
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
        {view.topNames.map((name) => (
          <span key={name} className="inline-flex items-center gap-1">
            <span className="h-2 w-2 rounded-sm" style={{ background: colorByName.get(name) }} />
            {name}
          </span>
        ))}
        {view.hasMerged && (
          <span className="inline-flex items-center gap-1">
            <span className="h-2 w-2 rounded-sm" style={{ background: mergedColor }} />
            {MERGED_LABEL}
          </span>
        )}
        <span className="inline-flex items-center gap-1">
          <span className="h-2 w-2 rounded-sm opacity-40" style={{ background: cashColor }} />
          {CASH_LABEL}
        </span>
      </div>
      <p className="text-[11px] text-muted-foreground">
        按调仓日重建持仓市值（收盘价）并汇总申万一级行业；现金显式列出，行业缺失的标的计入「其他」。
        超过 {MAX_EXPOSURE_DATES} 个调仓日时，较早的时点合并为「其余 N 期」均值。
      </p>
    </div>
  )
}

export default function BacktestExtras({ result }: { result: BacktestResult }) {
  if (!result.extras_available) {
    return (
      <div className="rounded-lg border border-border bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
        该回测记录没有净值曲线或成交明细，无法派生月度收益、回撤与行业分布。
      </div>
    )
  }

  const worst = result.metrics.max_drawdown
  const rollingValid = result.rolling_excess_20d.filter((item) => item.value != null).length

  return (
    <div className="space-y-2">
      <Section title="月度收益热力图" subtitle={`${result.monthly_returns.length} 个月`}>
        <MonthlyHeatmap monthly={result.monthly_returns} />
      </Section>

      <Section title="回撤曲线" subtitle={`最大回撤 ${fmtPct(worst == null ? null : worst * 100, 2)}`}>
        <DrawdownChart result={result} />
      </Section>

      <Section
        title="滚动超额收益（20 日）"
        subtitle={`有效 ${rollingValid}/${result.rolling_excess_20d.length} 个交易日`}
      >
        <RollingExcessChart result={result} />
      </Section>

      <Section title="持仓行业分布" subtitle={`${result.industry_exposure.length} 个调仓日`} defaultOpen={false}>
        <IndustryExposure snapshots={result.industry_exposure} />
      </Section>
    </div>
  )
}
