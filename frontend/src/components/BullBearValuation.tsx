/**
 * 牛熊位置 × 估值（V7 T4）
 * =========================
 * 6 个宽基指数的四面板：
 *  1. 归一化长周期走势（对数轴，3 年 / 10 年 / 全周期）；
 *  2. 回撤 × 估值：红利在右上，成长在左下（气泡大小 = 近 1 年波动率）；
 *  3. 动量：6 个指数 1 年全为负（1M/3M/6M/12M）；
 *  4. 21 年回撤史（沪深300）+ ERP 当前值/分位。
 *
 * 文件硬约束：只允许 6 个指数，不出现上证50/中证1000；
 * PE 不是历史分位；ERP 只有近 10 年样本；不输出买卖建议。
 */
import { useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { AlertTriangle, RefreshCw } from 'lucide-react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  Cell,
  LabelList,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from 'recharts'
import { useBullBearValuation } from '../hooks/useBullBearValuation'
import SectionHeader from './SectionHeader'
import { ErrorState, SkeletonChart, SkeletonLine } from './StateViews'
import type { BroadNormalizedPoint, BroadValuationIndex } from '../types/market'

type RangeKey = '3y' | '10y' | 'full'
type ValuationKey = 'pe' | 'erp'

const RANGE_OPTIONS: Array<{ key: RangeKey; label: string }> = [
  { key: '3y', label: '近 3 年' },
  { key: '10y', label: '近 10 年' },
  { key: 'full', label: '全周期' },
]

const RANGE_READOUT: Record<RangeKey, string> = {
  '3y': '近 3 年：分化更明显，红利抗跌、成长回撤更深。',
  '10y': '近 10 年：高点下移、波动收敛，红利与成长的差距在扩大。',
  full: '全周期：2007 / 2015 的高点之后都经历了长时间修复；2026 年高点明显低于前两轮。',
}

const LINE_COLORS: Record<string, string> = {
  sh000300: 'var(--color-chart-1)',
  sh000922: 'var(--color-brass)',
  sh000001: 'var(--color-chart-3)',
  sh000905: 'var(--color-chart-4)',
  sz399006: 'var(--color-up)',
  sh000688: 'var(--color-chart-5)',
}

const TERMS: Array<{ term: string; plain: string }> = [
  { term: 'PE', plain: '市盈率；数字越高，市场为每 1 元盈利付的钱越多。' },
  { term: '盈利收益率', plain: '1/PE；花 100 块买这篮子公司，一年大约赚回多少块。' },
  { term: '200 日均线', plain: '过去 200 个交易日的平均成本线；在下方说明多数人还没解套。' },
  { term: '回撤', plain: '从历史高点跌下来的幅度；-35.7% 就是从 100 块跌到约 64 块。' },
  { term: 'ERP / 股债溢价', plain: '股票盈利收益率减 10 年国债收益率；越高说明股票相对债券越便宜。' },
  { term: '年化波动率', plain: '价格上下晃动的剧烈程度；越高越颠簸。' },
  { term: '对数轴', plain: '等距代表等倍数，不是等点数；让早期暴涨和近期震荡都能看清。' },
  { term: '归一化', plain: '把各指数起点都设成 100，方便看相对变化；不代表绝对收益。' },
  { term: '历史分位', plain: '当前值在历史序列里的位置；78.9% 分位表示比过去约 79% 的时间更高。' },
]

function fmtPct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(digits)}%`
}

function QuadrantTooltip({
  active,
  payload,
}: {
  active?: boolean
  payload?: Array<{ payload: BroadValuationIndex }>
}) {
  if (!active || !payload?.length) return null
  const item = payload[0].payload
  return (
    <div className="rounded-lg border border-border/70 bg-card px-3 py-2 text-[11px] shadow-lg">
      <p className="font-medium text-foreground">{item.name}</p>
      <p className="mt-1 text-muted-foreground">PE(TTM) <span className="num text-foreground">{item.pe}</span></p>
      <p className="text-muted-foreground">距历史高点 <span className="num text-foreground">{fmtPct(item.drawdown)}</span></p>
      <p className="text-muted-foreground">YTD <span className="num text-foreground">{fmtPct(item.ytd)}</span></p>
      <p className="text-muted-foreground">近 1 年波动 <span className="num text-foreground">{item.volatility.toFixed(1)}%</span></p>
    </div>
  )
}

function Tag({ children, tone = 'data' }: { children: string; tone?: 'data' | 'read' }) {
  return (
    <span
      className={`rounded-full border px-1.5 py-0.5 text-[10px] ${
        tone === 'data'
          ? 'border-border/70 text-muted-foreground'
          : 'border-brass/40 text-brass-strong dark:text-brass'
      }`}
    >
      {children}
    </span>
  )
}

function HeatCell({ value, maxAbs }: { value: number | null; maxAbs: number }) {
  if (value === null || !Number.isFinite(value)) {
    return <div className="rounded-md bg-muted/30 px-1 py-1.5 text-center text-[10px] text-muted-foreground">—</div>
  }
  const alpha = 0.1 + 0.55 * Math.min(1, Math.abs(value) / Math.max(maxAbs, 1e-9))
  const background = value >= 0
    ? `rgba(190, 43, 34, ${alpha})`
    : `rgba(61, 107, 143, ${alpha})`
  return (
    <div
      className="rounded-md px-1 py-1.5 text-center text-[11px]"
      style={{ backgroundColor: background, color: alpha > 0.38 ? '#fff' : 'var(--color-foreground)' }}
      title={`${value >= 0 ? '上涨' : '下跌'} ${Math.abs(value).toFixed(2)}%`}
    >
      {fmtPct(value, 2)}
    </div>
  )
}

export default function BullBearValuation() {
  const query = useBullBearValuation()
  const [range, setRange] = useState<RangeKey>('full')
  const [valuation, setValuation] = useState<ValuationKey>('pe')
  const data = query.data

  const avgValues = useMemo(() => {
    if (!data) return { drawdown: -35.7, earnings: 5.2 }
    const count = data.indices.length || 1
    return {
      drawdown: data.indices.reduce((sum, item) => sum + item.drawdown, 0) / count,
      earnings: data.indices.reduce((sum, item) => sum + item.earnings_yield, 0) / count,
    }
  }, [data])

  const maxMomentum = useMemo(() => {
    if (!data) return 1
    const values = data.momentum.flatMap((item) => [item.m1, item.m3, item.m6, item.m12])
      .filter((value): value is number => value !== null && Number.isFinite(value))
      .map((value) => Math.abs(value))
    return Math.max(1, ...values)
  }, [data])

  if (query.isLoading && !data) {
    return (
      <section className="space-y-3" aria-busy="true">
        <div className="space-y-2">
          <SkeletonLine className="h-3 w-48" />
          <SkeletonLine className="h-7 w-72" />
          <SkeletonLine className="h-3 w-96 max-w-full" />
        </div>
        <SkeletonChart height={360} />
      </section>
    )
  }

  if (!data) {
    return (
      <ErrorState
        title="牛熊估值数据加载失败"
        error={query.error}
        onRetry={() => query.refetch()}
      />
    )
  }

  const {
    required_sentence,
    summary,
    erp,
    indices,
    normalized,
    momentum,
    drawdown_history,
    key_nodes,
    insights,
    analogy,
    caveats,
    disclaimer,
    meta,
    snapshot_date,
    price_as_of,
  } = data

  return (
    <section className="space-y-3">
      <SectionHeader
        index="05"
        title="牛熊位置 × 估值"
        hint="6 个宽基指数 · PE/ERP 快照 2026-11-13 · 价格/回撤/动量来自本地指数库"
      />

      {/* 一句话结论 + 三个大数字 */}
      <motion.div
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
        className="card relative overflow-hidden p-4 sm:p-5"
      >
        <span aria-hidden="true" className="absolute inset-x-0 top-0 h-1 bg-brass/70" />
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <span className="eyebrow">Bull / Bear × Valuation</span>
            <p className="mt-1.5 text-[22px] font-semibold leading-snug tracking-[-0.01em] text-foreground">
              {required_sentence}
            </p>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <Tag>数据</Tag>
            <Tag tone="read">解读</Tag>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-[12px] text-muted-foreground">
          <span>
            位置：<span className="num text-foreground">{summary.above_ma200}/{summary.total}</span> 站上 200 日线
          </span>
          <span>
            平均距历史高点：<span className="num text-foreground">{summary.avg_drawdown.toFixed(1)}%</span>
          </span>
          <span>
            ERP：<span className="num text-foreground">{erp.erp.toFixed(2)}%</span> ·
            近 10 年 <span className="num text-foreground">{erp.percentile.toFixed(1)}%</span> 分位
          </span>
          <span className="text-muted-foreground/80">数据截至 {snapshot_date}</span>
        </div>
        <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">{analogy}</p>
      </motion.div>

      {/* 3 个数字卡片 */}
      <motion.div
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3, delay: 0.06 }}
        className="grid grid-cols-1 gap-3 sm:grid-cols-3"
      >
        <div className="card p-4">
          <div className="flex items-center justify-between gap-2">
            <span className="eyebrow">Position</span><Tag>数据</Tag>
          </div>
          <p className="mt-2 readout text-[26px] leading-none text-foreground">
            {summary.above_ma200}/{summary.total}
          </p>
          <p className="mt-1 text-[12px] text-muted-foreground">站上 200 日均线</p>
          <p className="mt-2 text-[12px] leading-relaxed text-foreground">
            6 个指数都在地面以下，平均距历史高点 {summary.avg_drawdown.toFixed(1)}%；距近 5 年高点{' '}
            {summary.avg_drawdown_5y.toFixed(1)}%。
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">解读：这是回调 / 偏冷区，不是牛趋势中。</p>
        </div>
        <div className="card p-4">
          <div className="flex items-center justify-between gap-2">
            <span className="eyebrow">Dispersion</span><Tag>数据</Tag>
          </div>
          <p className="mt-2 readout text-[26px] leading-none text-foreground">
            {indices.find((item) => item.code === 'sh000922')?.drawdown.toFixed(1)}% /{' '}
            {indices.find((item) => item.code === 'sz399006')?.drawdown.toFixed(1)}%
          </p>
          <p className="mt-1 text-[12px] text-muted-foreground">中证红利 / 创业板指 距历史高点</p>
          <p className="mt-2 text-[12px] leading-relaxed text-foreground">
            红利端 PE 9.14、YTD +0.1%；成长端 PE 44.09、YTD -24.3%。
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">解读：便宜得不均匀，不能只看沪深300。</p>
        </div>
        <div className="card p-4">
          <div className="flex items-center justify-between gap-2">
            <span className="eyebrow">Valuation Anchor</span><Tag>数据</Tag>
          </div>
          <p className="mt-2 readout text-[26px] leading-none text-foreground">{erp.percentile.toFixed(1)}%</p>
          <p className="mt-1 text-[12px] text-muted-foreground">ERP 近 10 年分位</p>
          <p className="mt-2 text-[12px] leading-relaxed text-foreground">
            ERP {erp.erp.toFixed(2)}%，处近 10 年 {erp.percentile.toFixed(1)}% 分位。
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">解读：赔率变好，但温度计不是秒表。</p>
        </div>
      </motion.div>

      {/* 四面板 */}
      <motion.div
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3, delay: 0.12 }}
        className="grid grid-cols-1 gap-4 xl:grid-cols-2"
      >
        {/* 1. 归一化走势 */}
        <div className="card p-4">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <span className="eyebrow">Normalized · Log Scale</span>
              <h3 className="mt-1 text-[14px] font-semibold text-foreground">长周期归一化：高点在下移</h3>
            </div>
            <div className="flex rounded-lg border border-border/70 p-0.5">
              {RANGE_OPTIONS.map((option) => (
                <button
                  key={option.key}
                  type="button"
                  onClick={() => setRange(option.key)}
                  className={`rounded-md px-2 py-1 text-[11px] transition-colors ${
                    range === option.key
                      ? 'bg-brass/15 font-medium text-brass-strong dark:text-brass'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
          <div className="mt-3 h-[300px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={normalized[range]} margin={{ top: 8, right: 10, bottom: 4, left: 0 }}>
                <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" vertical={false} />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 10, fill: 'var(--color-muted-foreground)' }}
                  tickFormatter={(value: string) => value.slice(0, 7)}
                  minTickGap={42}
                  axisLine={{ stroke: 'var(--color-border)' }}
                  tickLine={false}
                />
                <YAxis
                  scale="log"
                  domain={['auto', 'auto']}
                  tick={{ fontSize: 10, fill: 'var(--color-muted-foreground)' }}
                  tickFormatter={(value: number) => value.toFixed(0)}
                  width={42}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip
                  formatter={(value) => Number(value).toFixed(1)}
                  labelFormatter={(label) => `月份 ${label}`}
                  contentStyle={{
                    background: 'var(--color-card)',
                    border: '1px solid var(--color-border)',
                    borderRadius: 10,
                    fontSize: 11,
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 10 }} />
                {Object.entries(meta.index_names).map(([code, name]) => (
                  <Line
                    key={code}
                    type="monotone"
                    dataKey={code}
                    name={name}
                    stroke={LINE_COLORS[code]}
                    strokeWidth={1.7}
                    dot={false}
                    connectNulls={false}
                    isAnimationActive={false}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            对数刻度：等距代表等倍数，不是等点数；各指数起点不同，归一化后的绝对高度不可横向比较。
          </p>
          <p className="mt-1 flex items-start gap-1 text-[11px] leading-relaxed text-foreground">
            <Tag tone="read">解读</Tag>
            <span>{RANGE_READOUT[range]}</span>
          </p>
        </div>

        {/* 2. 四象限 */}
        <div className="card p-4">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <span className="eyebrow">Drawdown × Earnings Yield</span>
              <h3 className="mt-1 text-[14px] font-semibold text-foreground">回撤 × 估值：红利在右上，成长在左下</h3>
            </div>
            <div className="flex rounded-lg border border-border/70 p-0.5">
              <button
                type="button"
                onClick={() => setValuation('pe')}
                className={`rounded-md px-2 py-1 text-[11px] ${
                  valuation === 'pe' ? 'bg-brass/15 font-medium text-brass-strong dark:text-brass' : 'text-muted-foreground'
                }`}
              >
                PE(TTM)
              </button>
              <button
                type="button"
                onClick={() => setValuation('erp')}
                className={`rounded-md px-2 py-1 text-[11px] ${
                  valuation === 'erp' ? 'bg-brass/15 font-medium text-brass-strong dark:text-brass' : 'text-muted-foreground'
                }`}
              >
                股债溢价 ERP
              </button>
            </div>
          </div>
          <div className="mt-3 h-[280px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ top: 18, right: 18, bottom: 8, left: 0 }}>
                <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
                <XAxis
                  type="number"
                  dataKey="drawdown"
                  name="距历史高点 %"
                  domain={[-50, 0]}
                  tick={{ fontSize: 10, fill: 'var(--color-muted-foreground)' }}
                  tickFormatter={(value: number) => `${value}%`}
                  axisLine={{ stroke: 'var(--color-border)' }}
                  tickLine={false}
                />
                <YAxis
                  type="number"
                  dataKey="earnings_yield"
                  name="盈利收益率 %"
                  domain={[0, 12]}
                  tick={{ fontSize: 10, fill: 'var(--color-muted-foreground)' }}
                  tickFormatter={(value: number) => `${value}%`}
                  width={38}
                  axisLine={false}
                  tickLine={false}
                />
                <ZAxis type="number" dataKey="volatility" range={[80, 420]} />
                <ReferenceLine x={avgValues.drawdown} stroke="var(--color-border)" strokeDasharray="4 4" />
                <ReferenceLine y={avgValues.earnings} stroke="var(--color-border)" strokeDasharray="4 4" />
                <Tooltip content={<QuadrantTooltip />} />
                <Scatter data={indices} name="指数">
                  {indices.map((item) => (
                    <Cell key={item.code} fill={LINE_COLORS[item.code]} fillOpacity={0.85} />
                  ))}
                  <LabelList
                    dataKey="name"
                    position="top"
                    style={{ fontSize: 10, fill: 'var(--color-muted-foreground)' }}
                  />
                </Scatter>
              </ScatterChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-1 grid grid-cols-2 gap-1 text-[10px] text-muted-foreground">
            <span>↖ 深跌但更便宜</span>
            <span className="text-right">↗ 抗跌且便宜</span>
            <span>↙ 深跌且更贵</span>
            <span className="text-right">↘ 抗跌但更贵</span>
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            横轴：距各自历史高点；纵轴：1/PE；气泡大小：近 1 年波动率。虚线为 6 个指数的均值参考线。
          </p>
          {valuation === 'pe' ? (
            <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
              PE 是当前 TTM 绝对值，不是各指数自身的历史估值分位；不能据此说“处于历史低位”。
            </p>
          ) : (
            <p className="mt-1 rounded-lg border border-brass/30 bg-brass/[0.06] px-2 py-1.5 text-[11px] leading-relaxed text-foreground">
              ERP 是市场级股债性价比，不参与 6 个指数的四象限；当前 {erp.erp.toFixed(2)}%、近 10 年{' '}
              {erp.percentile.toFixed(1)}% 分位。两者不是同一件事。
            </p>
          )}
        </div>

        {/* 3. 动量：6 个指数 1 年全为负 */}
        <div className="card p-4">
          <span className="eyebrow">Momentum Heatmap</span>
          <h3 className="mt-1 text-[14px] font-semibold text-foreground">动量：6 个指数 1 年全为负</h3>
          <div className="mt-3 grid grid-cols-[1.3fr_repeat(4,1fr)] gap-1 text-[10px] text-muted-foreground">
            <span>指数</span><span className="text-center">1 月</span><span className="text-center">3 月</span><span className="text-center">6 月</span><span className="text-center">1 年</span>
            {momentum.map((item) => (
              <div key={item.code} className="contents">
                <span className="flex items-center truncate text-[11px] text-foreground">{item.name}</span>
                <HeatCell value={item.m1} maxAbs={maxMomentum} />
                <HeatCell value={item.m3} maxAbs={maxMomentum} />
                <HeatCell value={item.m6} maxAbs={maxMomentum} />
                <HeatCell value={item.m12} maxAbs={maxMomentum} />
              </div>
            ))}
          </div>
          <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
            红涨蓝跌；区间收益由本地价格指数计算，非文件 PE 快照。当前 6 个指数的 1 年动量全部为负。
          </p>
        </div>

        {/* 4. 回撤史 + ERP */}
        <div className="card p-4">
          <span className="eyebrow">Drawdown History + ERP</span>
          <h3 className="mt-1 text-[14px] font-semibold text-foreground">21 年回撤：两轮大顶后修复漫长</h3>
          <div className="mt-3 h-[190px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={drawdown_history} margin={{ top: 6, right: 10, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" vertical={false} />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 10, fill: 'var(--color-muted-foreground)' }}
                  tickFormatter={(value: string) => value.slice(0, 7)}
                  minTickGap={42}
                  axisLine={{ stroke: 'var(--color-border)' }}
                  tickLine={false}
                />
                <YAxis
                  domain={[-70, 0]}
                  tick={{ fontSize: 10, fill: 'var(--color-muted-foreground)' }}
                  tickFormatter={(value: number) => `${value}%`}
                  width={38}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip
                  formatter={(value) => [`${Number(value).toFixed(1)}%`, '回撤']}
                  labelFormatter={(label) => `月份 ${label}`}
                  contentStyle={{
                    background: 'var(--color-card)',
                    border: '1px solid var(--color-border)',
                    borderRadius: 10,
                    fontSize: 11,
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="drawdown"
                  stroke="var(--color-chart-1)"
                  strokeWidth={1.6}
                  fill="var(--color-chart-1)"
                  fillOpacity={0.16}
                  isAnimationActive={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {key_nodes.map((node) => (
              <span
                key={`${node.date}-${node.label}`}
                className={`rounded-full border px-1.5 py-0.5 text-[10px] ${
                  node.type === 'top'
                    ? 'border-up/35 text-up'
                    : 'border-chart-1/40 text-chart-1'
                }`}
                title={node.date}
              >
                {node.label}
              </span>
            ))}
          </div>
          <div className="mt-3 rounded-lg border border-border/60 bg-muted/20 p-2.5">
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-[12px] text-muted-foreground">ERP / 股债溢价</span>
              <span className="num text-[13px] font-semibold text-foreground">
                {erp.erp.toFixed(2)}% · {erp.percentile.toFixed(1)}% 分位
              </span>
            </div>
            <div
              className="relative mt-2 h-2 rounded-full"
              style={{
                background: 'linear-gradient(90deg, color-mix(in srgb, var(--color-chart-1) 30%, transparent), color-mix(in srgb, var(--color-brass) 35%, transparent), color-mix(in srgb, var(--color-up) 35%, transparent))',
              }}
            >
              <span
                aria-hidden="true"
                className="absolute top-1/2 h-4 w-0.5 -translate-y-1/2 bg-foreground"
                style={{ left: `${Math.min(100, Math.max(0, erp.percentile))}%` }}
              />
            </div>
            <div className="mt-1 flex justify-between text-[10px] text-muted-foreground">
              <span>贵</span><span>便宜</span>
            </div>
            <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground">
              ERP 样本 {erp.sample_start} ~ {erp.sample_end}（仅近 10 年，不是 21 年）；分位越高＝股票相对债券越便宜。
            </p>
            <p className="mt-0.5 text-[10px] text-muted-foreground">
              股息溢价 {erp.dividend_premium.toFixed(3)}% · {erp.dividend_percentile.toFixed(1)}% 分位
            </p>
          </div>
        </div>
      </motion.div>

      {/* 5 个洞察 */}
      <motion.div
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3, delay: 0.18 }}
        className="card p-4 sm:p-5"
      >
        <div className="flex items-center justify-between gap-2">
          <span className="eyebrow">Five Takeaways</span>
          <Tag tone="read">解读</Tag>
        </div>
        <div className="mt-2 grid grid-cols-1 gap-2 lg:grid-cols-2">
          {insights.map((item, index) => (
            <div key={item} className="flex gap-2 rounded-lg border border-border/60 bg-muted/15 p-2.5">
              <span className="medal medal-brass shrink-0">{index + 1}</span>
              <p className="text-[12px] leading-relaxed text-foreground">{item}</p>
            </div>
          ))}
        </div>
      </motion.div>

      {/* 术语卡 */}
      <details className="card p-4">
        <summary className="cursor-pointer text-[12px] font-medium text-foreground">
          9 个术语，大白话解释
        </summary>
        <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {TERMS.map((item) => (
            <div key={item.term} className="rounded-lg border border-border/60 bg-muted/15 p-2.5">
              <p className="text-[12px] font-medium text-foreground">{item.term}</p>
              <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{item.plain}</p>
            </div>
          ))}
        </div>
      </details>

      {/* 口径与合规 */}
      <div className="card space-y-1.5 p-4">
        <div className="flex items-center gap-2">
          <AlertTriangle className="h-3.5 w-3.5 text-warning" />
          <span className="text-[12px] font-medium text-foreground">口径与风险</span>
        </div>
        {caveats.map((item) => (
          <p key={item} className="text-[11px] leading-relaxed text-muted-foreground">· {item}</p>
        ))}
        <p className="pt-1 text-[11px] text-muted-foreground">
          数据截止 {snapshot_date}；当前 PE/ERP 快照来源腾讯自选股行情；价格/回撤/动量来自本地指数库
          （新浪 / 中证指数），价格共同最新日 {price_as_of}；ERP 样本 {erp.sample_start} 至 {erp.sample_end}。
        </p>
        <p className="text-[11px] font-medium leading-relaxed text-foreground">{disclaimer}</p>
      </div>
    </section>
  )
}
