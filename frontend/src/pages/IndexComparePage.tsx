/**
 * 指数对比 / 风格轮动页（V7 T3）
 * ==============================
 * 创业板50（399673）÷ 红利低波50（H30269）的价格比价看板：
 * 今日读数 → 比价走势 → 持仓建议 → 风险提示。
 * 所有数值由后端 `/market/index-compare` 计算，前端不自行推算 R。
 */
import { motion } from 'framer-motion'
import { AlertTriangle, RefreshCw } from 'lucide-react'
import {
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { ApiError } from '../api/client'
import { useIndexCompare } from '../hooks/useIndexCompare'
import BullBearValuation from '../components/BullBearValuation'
import PageHeader from '../components/PageHeader'
import SectionHeader from '../components/SectionHeader'
import {
  ErrorState,
  SkeletonChart,
  SkeletonKpiRow,
  SkeletonLine,
} from '../components/StateViews'

const CYB50_LABEL = '创业板50'
const HL50_LABEL = '红利低波50'

function pct(value: number): string {
  return `${Math.round(value * 100)}%`
}

function zoneTone(zone: string): string {
  if (zone === '低估区') return 'text-brass'
  if (zone === '高估区') return 'text-warning'
  return 'text-foreground'
}

function RatioScale({
  current,
  low,
  high,
}: {
  current: number
  low: number
  high: number
}) {
  const min = Math.min(low, current) * 0.98
  const max = Math.max(high, current) * 1.02
  const span = Math.max(max - min, 1e-9)
  const pos = (value: number) => Math.min(100, Math.max(0, ((value - min) / span) * 100))
  return (
    <div className="mt-4">
      <div className="relative h-2 rounded-full bg-border/50">
        <span
          aria-hidden="true"
          className="absolute inset-y-0 rounded-full bg-brass/20"
          style={{ left: `${pos(low)}%`, width: `${Math.max(0, pos(high) - pos(low))}%` }}
        />
        <span
          aria-hidden="true"
          className="absolute top-1/2 h-4 w-0.5 -translate-y-1/2 bg-foreground"
          style={{ left: `${pos(current)}%` }}
        />
      </div>
      <div className="relative mt-1 h-4 text-[10px] text-muted-foreground">
        <span className="absolute -translate-x-1/2" style={{ left: `${pos(low)}%` }}>
          下界 {low.toFixed(4)}
        </span>
        <span className="absolute -translate-x-1/2 font-medium text-foreground" style={{ left: `${pos(current)}%` }}>
          当前 {current.toFixed(4)}
        </span>
        <span className="absolute -translate-x-1/2" style={{ left: `${pos(high)}%` }}>
          上界 {high.toFixed(4)}
        </span>
      </div>
    </div>
  )
}

function StateRule({
  active,
  title,
  condition,
  target,
}: {
  active: boolean
  title: string
  condition: string
  target: string
}) {
  return (
    <div
      className={`rounded-lg border p-2.5 ${
        active ? 'border-brass/60 bg-brass/[0.06]' : 'border-border/60 bg-muted/15'
      }`}
    >
      <p className={`text-[12px] font-medium ${active ? 'text-brass-strong dark:text-brass' : 'text-foreground'}`}>
        {title}
      </p>
      <p className="mt-1 text-[10px] text-muted-foreground">{condition}</p>
      <p className="mt-1 num text-[12px] text-foreground">{target}</p>
    </div>
  )
}

export default function IndexComparePage() {
  const query = useIndexCompare()
  const data = query.data

  if (query.isLoading && !data) {
    return (
      <div className="mx-auto max-w-7xl px-4 py-6 space-y-6 sm:px-6 lg:px-8" aria-busy="true">
        <div className="space-y-2">
          <SkeletonLine className="h-3 w-40" />
          <SkeletonLine className="h-8 w-56" />
          <SkeletonLine className="h-3 w-80 max-w-full" />
        </div>
        <SkeletonKpiRow count={4} />
        <SkeletonChart height={340} />
      </div>
    )
  }

  if (!data) {
    const notReady = query.error instanceof ApiError && query.error.code === 'DATA_NOT_READY'
    return (
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        <ErrorState
          title={notReady ? '风格指数尚未就绪' : '指数对比加载失败'}
          error={query.error}
          onRetry={() => query.refetch()}
        />
      </div>
    )
  }

  const {
    date,
    R,
    closes,
    percentile,
    zone,
    thresholds,
    headline,
    chart,
    allocation,
    risk,
    disclaimer,
    meta,
    caveats,
  } = data
  const staleNote = meta.is_stale
    ? `红利低波50 截至 ${meta.index_dates.H30269 ?? '—'}，创业板50 截至 ${meta.index_dates.sz399673 ?? '—'}；R 按共同最新日 ${date} 计算。`
    : ''

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3 }}
      className="mx-auto max-w-7xl px-4 py-6 space-y-6 sm:px-6 lg:px-8"
    >
      <PageHeader
        eyebrow="Style Rotation · Growth vs Dividend"
        title="指数对比"
        description={
          <span>
            创业板50（399673）÷ 红利低波50（H30269）的价格比价与仓位纪律
            <span className="mx-2 text-muted-foreground">·</span>
            <span className="num">R = {R.toFixed(4)}</span>
            <span className="mx-2 text-muted-foreground">·</span>
            <span className="num">{date}</span>
          </span>
        }
        actions={
          <button
            onClick={() => query.refetch()}
            disabled={query.isFetching}
            className="btn btn-sm h-9"
            title="重新加载指数对比"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${query.isFetching ? 'animate-spin' : ''}`} />
            刷新{query.isFetching ? '中…' : ''}
          </button>
        }
      />

      {staleNote && (
        <p className="flex items-start gap-1.5 rounded-lg border border-warning/25 bg-warning/[0.06] px-3 py-2 text-[11px] leading-relaxed text-warning">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {staleNote}
        </p>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.15fr_1fr]">
        {/* ========== 01 今日读数 ========== */}
        <section>
          <SectionHeader index="01" title="今日读数" hint="R、两端收盘、历史分位与所处区间" />
          <div className="card p-4 sm:p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <span className="eyebrow">Ratio Readout</span>
                <div className="mt-1.5 flex items-baseline gap-3">
                  <span className="readout text-[42px] leading-none text-foreground">{R.toFixed(4)}</span>
                  <span className={`rounded-full border border-border/70 px-2.5 py-1 text-[12px] font-medium ${zoneTone(zone)}`}>
                    {zone}
                  </span>
                </div>
                <p className="mt-1 text-[11px] text-muted-foreground">
                  {meta.index_names.sz399673} ÷ {meta.index_names.H30269} · {date}
                </p>
              </div>
              <div className="space-y-1 text-right">
                <p className="text-[12px] text-muted-foreground">
                  {CYB50_LABEL} <span className="num text-foreground">{closes.cyb50.toFixed(2)}</span>
                </p>
                <p className="text-[12px] text-muted-foreground">
                  {HL50_LABEL} <span className="num text-foreground">{closes.hl50.toFixed(2)}</span>
                </p>
              </div>
            </div>

            <p className="mt-3 text-[15px] font-medium leading-snug text-foreground">{headline}</p>

            <RatioScale current={R} low={thresholds.low} high={thresholds.high} />

            <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
              <div className="inset px-2.5 py-2">
                <p className="text-[10px] text-muted-foreground">历史分位</p>
                <p className="num mt-1 text-[15px] font-semibold text-foreground">{percentile.toFixed(1)}%</p>
              </div>
              <div className="inset px-2.5 py-2">
                <p className="text-[10px] text-muted-foreground">下界</p>
                <p className="num mt-1 text-[15px] font-semibold text-foreground">{thresholds.low.toFixed(4)}</p>
              </div>
              <div className="inset px-2.5 py-2">
                <p className="text-[10px] text-muted-foreground">上界</p>
                <p className="num mt-1 text-[15px] font-semibold text-foreground">{thresholds.high.toFixed(4)}</p>
              </div>
              <div className="inset px-2.5 py-2">
                <p className="text-[10px] text-muted-foreground">阈值口径</p>
                <p className="mt-1 text-[13px] font-medium text-foreground">{thresholds.method}</p>
              </div>
            </div>

            <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
              统计区间 {meta.common_start} ~ {meta.common_end} · 滚动窗口 {thresholds.window_days} 个交易日 ·
              固定参考 {thresholds.fixed_low.toFixed(2)} / {thresholds.fixed_high.toFixed(2)} ·
              全样本分位 {thresholds.sample_q20.toFixed(4)} / {thresholds.sample_q80.toFixed(4)}
            </p>
          </div>
        </section>

        {/* ========== 02 持仓建议 ========== */}
        <section>
          <SectionHeader index="02" title="持仓建议" hint="目标仓位、分批纪律与触发条件" />
          <div className="card p-4 sm:p-5">
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-lg border border-border/60 bg-muted/20 p-3">
                <p className="text-[12px] text-muted-foreground">{CYB50_LABEL} 目标</p>
                <p className="readout mt-1 text-[30px] leading-none text-foreground">{pct(allocation.cyb50_target)}</p>
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-border/60">
                  <div className="h-full rounded-full bg-brass" style={{ width: `${allocation.cyb50_target * 100}%` }} />
                </div>
              </div>
              <div className="rounded-lg border border-border/60 bg-muted/20 p-3">
                <p className="text-[12px] text-muted-foreground">{HL50_LABEL} 目标</p>
                <p className="readout mt-1 text-[30px] leading-none text-foreground">{pct(allocation.hl50_target)}</p>
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-border/60">
                  <div className="h-full rounded-full bg-chart-1" style={{ width: `${allocation.hl50_target * 100}%` }} />
                </div>
              </div>
            </div>

            <div className="mt-3 grid grid-cols-3 gap-2">
              <StateRule
                active={zone === '低估区'}
                title="低估区"
                condition={`R < ${thresholds.low.toFixed(4)}`}
                target="成长 100% / 红利 0%"
              />
              <StateRule
                active={zone === '均衡区'}
                title="均衡区"
                condition={`${thresholds.low.toFixed(4)} ≤ R ≤ ${thresholds.high.toFixed(4)}`}
                target="成长 50% / 红利 50%"
              />
              <StateRule
                active={zone === '高估区'}
                title="高估区"
                condition={`R > ${thresholds.high.toFixed(4)}`}
                target="成长 0% / 红利 100%"
              />
            </div>

            <div className="mt-3 flex flex-wrap gap-1.5 text-[11px]">
              <span className="rounded-full border border-border/70 bg-muted/30 px-2 py-0.5 text-foreground">
                分 {allocation.batches} 批
              </span>
              <span className="rounded-full border border-border/70 bg-muted/30 px-2 py-0.5 text-foreground">
                单批 ≤ {allocation.step_pct}%
              </span>
              <span className="rounded-full border border-border/70 bg-muted/30 px-2 py-0.5 text-muted-foreground">
                不一次性梭哈
              </span>
            </div>

            <p className="mt-3 text-[12px] leading-relaxed text-foreground">{allocation.action}</p>
            <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">{allocation.note}</p>
            <p className="mt-2 rounded-lg border border-border/60 bg-muted/20 px-2.5 py-2 text-[11px] leading-relaxed text-muted-foreground">
              <span className="text-foreground">改变建议的条件：</span>
              {allocation.rebalance_trigger}
            </p>
          </div>
        </section>
      </div>

      {/* ========== 03 比价走势 ========== */}
      <section>
        <SectionHeader
          index="03"
          title="比价走势"
          hint="铜色线为 R；虚线为滚动阈值；中间色带为均衡区"
        />
        <div className="card p-4 sm:p-5">
          <div className="h-[340px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={chart.history} margin={{ top: 8, right: 18, bottom: 4, left: 0 }}>
                <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" vertical={false} />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 11, fill: 'var(--color-muted-foreground)' }}
                  tickFormatter={(value: string) => value.slice(0, 7)}
                  minTickGap={48}
                  axisLine={{ stroke: 'var(--color-border)' }}
                  tickLine={false}
                />
                <YAxis
                  domain={['auto', 'auto']}
                  tick={{ fontSize: 11, fill: 'var(--color-muted-foreground)' }}
                  tickFormatter={(value: number) => value.toFixed(2)}
                  width={46}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip
                  formatter={(value) => [Number(value).toFixed(4), 'R']}
                  labelFormatter={(label) => `交易日 ${label}`}
                  contentStyle={{
                    background: 'var(--color-card)',
                    border: '1px solid var(--color-border)',
                    borderRadius: 10,
                    fontSize: 12,
                  }}
                />
                <ReferenceArea
                  y1={thresholds.low}
                  y2={thresholds.high}
                  fill="var(--color-brass)"
                  fillOpacity={0.08}
                />
                <ReferenceLine
                  y={thresholds.low}
                  stroke="var(--color-down)"
                  strokeDasharray="6 4"
                  label={{
                    value: `下界 ${thresholds.low.toFixed(4)}`,
                    position: 'insideBottomLeft',
                    fill: 'var(--color-muted-foreground)',
                    fontSize: 11,
                  }}
                />
                <ReferenceLine
                  y={thresholds.high}
                  stroke="var(--color-up)"
                  strokeDasharray="6 4"
                  label={{
                    value: `上界 ${thresholds.high.toFixed(4)}`,
                    position: 'insideTopLeft',
                    fill: 'var(--color-muted-foreground)',
                    fontSize: 11,
                  }}
                />
                <Line
                  type="monotone"
                  dataKey="R"
                  stroke="var(--color-brass)"
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <p className="mt-2 text-[12px] leading-relaxed text-muted-foreground">{chart.readout}</p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {chart.annotations.map((item) => (
              <span key={item} className="rounded-full border border-border/70 bg-muted/25 px-2 py-0.5 text-[11px] text-muted-foreground">
                {item}
              </span>
            ))}
          </div>
        </div>
      </section>

      {/* ========== 04 风险提示 ========== */}
      <section>
        <SectionHeader index="04" title="风险提示与口径" hint="相对强弱不是绝对估值；分红与价格口径分开" />
        <div className="card space-y-2 p-4 sm:p-5">
          <p className="text-[13px] leading-relaxed text-foreground">{risk}</p>
          {caveats.map((item) => (
            <p key={item} className="text-[11px] leading-relaxed text-muted-foreground">
              · {item}
            </p>
          ))}
          <p className="pt-1 text-[11px] leading-relaxed text-muted-foreground">
            数据源：{CYB50_LABEL} {meta.sources.sz399673?.join(' / ') || '—'}；{HL50_LABEL}{' '}
            {meta.sources.H30269?.join(' / ') || '—'}。统计区间 {meta.common_start} ~ {meta.common_end}。
          </p>
          <p className="text-[11px] font-medium leading-relaxed text-foreground">{disclaimer}</p>
        </div>
      </section>

      {/* ========== 05 牛熊位置 × 估值 ========== */}
      <BullBearValuation />
    </motion.div>
  )
}
