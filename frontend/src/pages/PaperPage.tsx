/**
 * 纸面组合跟踪（V6 T11）
 * ======================
 * 每策略一个组合，每日整体重算建仓日至今的净值（不做增量状态机）。
 * 页面同时展示：组合卡片、净值 vs 基准、回撤、持仓、明日待执行清单、与回测差值。
 */
import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { motion } from 'framer-motion'
import {
  CalendarClock,
  RefreshCw,
  TrendingUp,
  Wallet,
} from 'lucide-react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  usePaperDetail,
  usePaperPortfolios,
  usePaperSync,
} from '../hooks/usePaper'
import type {
  PaperDetail,
  PaperFreshness,
  PaperHolding,
  PaperNavPoint,
  PaperPendingOrder,
  PaperPortfolioCard,
  PaperStatus,
  PaperVsBacktest,
} from '../types/paper'
import {
  fmtDate,
  fmtDateTime,
  fmtNumber,
  fmtRatio,
  pctColor,
  shortDate,
} from '../lib/format'
import DataTable, { type DataTableColumn } from '../components/DataTable'
import {
  EmptyState,
  ErrorState,
  SkeletonCard,
  SkeletonChart,
  SkeletonKpiRow,
  SkeletonLine,
  SkeletonTable,
} from '../components/StateViews'
import { useDelayedFlag } from '../hooks/useDelayedFlag'

const NAV_DAYS = 250
/** 手动同步后加速轮询的最长时间（完整区间重算可能跑几十秒） */
const SYNC_POLL_MS = 180_000

const STATUS_META: Record<PaperStatus, { label: string; badge: string }> = {
  pending: { label: '未初始化', badge: 'border-border bg-muted text-muted-foreground' },
  waiting: { label: '等待行情', badge: 'border-warning/40 bg-warning/10 text-warning' },
  active: { label: '跟踪中', badge: 'border-success/40 bg-success/10 text-success' },
  failed: { label: '同步失败', badge: 'border-alert/40 bg-alert/10 text-alert' },
}

function StatusBadge({ status }: { status: PaperStatus }) {
  const meta = STATUS_META[status] ?? STATUS_META.pending
  return (
    <span className={`text-[11px] px-2 py-0.5 rounded-full border font-medium ${meta.badge}`}>
      {meta.label}
    </span>
  )
}

/** 差值（小数比率）→ 百分点，用于"跑赢/跑输回测" */
function fmtPp(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return `${value > 0 ? '+' : ''}${(value * 100).toFixed(2)}pp`
}

function diffBadgeClass(value: number | null | undefined): string {
  if (typeof value !== 'number' || value === 0) {
    return 'border-border bg-muted text-muted-foreground'
  }
  return value > 0
    ? 'border-up/30 bg-up/10 text-up-strong'
    : 'border-down/30 bg-down/10 text-down-strong'
}

function freshnessText(freshness: PaperFreshness): string {
  if (!freshness.ready) {
    return freshness.market_trade_date
      ? `尚未产生净值 · 行情已至 ${freshness.market_trade_date}`
      : '尚未产生净值'
  }
  if (freshness.stale && freshness.lag_days !== null) {
    return `净值截至 ${freshness.latest_trade_date} · 落后行情 ${freshness.lag_days} 天`
  }
  return `净值截至 ${freshness.latest_trade_date} · 更新 ${fmtDateTime(freshness.updated_at)}`
}

function VsBacktestBadge({ value }: { value: PaperVsBacktest | null }) {
  if (!value) {
    return (
      <span className="inline-flex text-[11px] px-2 py-1 rounded-full border border-border bg-muted text-muted-foreground">
        暂无同参数同区间人工回测
      </span>
    )
  }
  const diff = value.total_return_diff
  const text =
    diff === null || diff === 0 ? '与回测持平' : diff > 0 ? '跑赢回测' : '跑输回测'
  return (
    <span
      className={`inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded-full border ${diffBadgeClass(diff)}`}
      title={`纸面 ${fmtRatio(value.total_return, 2)} vs 回测 ${fmtRatio(value.total_return, 2)}（${value.as_of}）`}
    >
      {text} <span className="num font-medium">{fmtPp(diff)}</span>
    </span>
  )
}

function PaperCard({
  card,
  selected,
  syncing,
  onSelect,
  onSync,
}: {
  card: PaperPortfolioCard
  selected: boolean
  syncing: boolean
  onSelect: (code: string) => void
  onSync: (code: string) => void
}) {
  const selectable = card.status !== 'pending'
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      onClick={() => selectable && onSelect(card.algo_code)}
      className={`card p-4 ${selectable ? 'card-lift cursor-pointer' : ''} ${
        selected ? 'card-selected' : ''
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-muted-foreground/70">
            {card.algo_code}
          </p>
          <h3 className="mt-1 truncate text-[13.5px] font-semibold" title={card.name}>
            {card.name}
          </h3>
          <p className="mt-0.5 text-[10px] text-muted-foreground">
            {card.rebalance_days ? `每 ${card.rebalance_days} 日调仓` : '调仓周期待定'}
            {card.max_positions ? ` · 持有 ${card.max_positions} 只` : ''}
          </p>
        </div>
        <StatusBadge status={card.status} />
      </div>

      <div className="mt-4 flex items-baseline gap-3">
        <span className="readout text-[30px]" title="累计净值（起点=1）">
          {card.nav !== null ? fmtNumber(card.nav, 4) : '—'}
        </span>
        <span className={`text-sm font-medium num ${pctColor(card.total_return)}`}>
          {card.total_return !== null ? fmtRatio(card.total_return, 2) : '—'}
        </span>
      </div>
      <p className="mt-1 text-[11px] text-muted-foreground">
        累计净值 · 起点 {card.start_date ? fmtDate(card.start_date) : '待确定'}
      </p>

      <dl className="mt-3 grid grid-cols-3 gap-2 text-xs">
        <div>
          <dt className="text-muted-foreground">超额收益</dt>
          <dd className={`num mt-0.5 ${pctColor(card.excess_return)}`}>
            {card.excess_return !== null ? fmtRatio(card.excess_return, 2) : '—'}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">最大回撤</dt>
          <dd className="num mt-0.5 text-down">
            {card.max_drawdown !== null ? fmtRatio(card.max_drawdown, 2) : '—'}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">跟踪交易日</dt>
          <dd className="num mt-0.5">{card.tracking_days}</dd>
        </div>
      </dl>

      <div className="mt-3">
        <VsBacktestBadge value={card.vs_backtest} />
      </div>

      <div className="mt-3 flex items-center justify-between gap-2 border-t border-border/60 pt-3">
        <span
          className={`text-[11px] ${card.freshness.stale ? 'text-warning' : 'text-muted-foreground'}`}
          title={freshnessText(card.freshness)}
        >
          {freshnessText(card.freshness)}
        </span>
        {card.status === 'pending' ? (
          <button
            type="button"
            disabled={syncing}
            onClick={(event) => {
              event.stopPropagation()
              onSync(card.algo_code)
            }}
            className="btn btn-primary btn-sm shrink-0"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            {syncing ? '同步中…' : '立即同步'}
          </button>
        ) : (
          <span className="shrink-0 text-[11px] text-brass-strong dark:text-brass">查看明细 →</span>
        )}
      </div>
    </motion.div>
  )
}

function PendingOrders({
  orders,
  rebalanceDays,
}: {
  orders: PaperPendingOrder[]
  rebalanceDays: number | null
}) {
  return (
    <section className="card border-warning/30 p-4" aria-labelledby="pending-title"
      style={{ background: 'color-mix(in srgb, var(--color-warning) 4%, var(--color-card))' }}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="pending-title" className="font-semibold flex items-center gap-2">
          <CalendarClock className="w-4 h-4 text-warning" />
          明日待执行
        </h3>
        <span className="text-[11px] text-muted-foreground">
          按 T+1 开盘价成交，停牌 / 一字板不可成交
        </span>
      </div>
      {orders.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">
          明日无调仓动作{rebalanceDays ? `（策略每 ${rebalanceDays} 个交易日换仓）` : ''}。
        </p>
      ) : (
        <ul className="mt-3 space-y-2">
          {orders.map((order, index) => (
            <li
              key={`${order.side}-${order.code}-${index}`}
              className="flex flex-wrap items-center gap-2 rounded-lg border border-border/70 bg-card/60 px-3 py-2"
            >
              <span
                className={`text-xs font-semibold ${order.side === 'buy' ? 'text-up' : 'text-down'}`}
              >
                {order.side === 'buy' ? '买入' : '卖出'}
              </span>
              <Link
                to={`/stocks/${order.code}`}
                className="font-mono text-[10px] text-brass-strong hover:underline dark:text-brass"
              >
                {order.code}
              </Link>
              <span className="text-sm">{order.name}</span>
              <span className="text-[11px] text-muted-foreground">{order.reason}</span>
              <span className="ml-auto text-[11px]">
                {order.fillable === true ? (
                  <span className="text-success">可成交</span>
                ) : order.fillable === false ? (
                  <span className="text-alert">
                    不可成交{order.block_reason ? ` · ${order.block_reason}` : ''}
                  </span>
                ) : (
                  <span className="text-warning">待 T+1 开盘校验</span>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function VsBacktestPanel({ value }: { value: PaperVsBacktest }) {
  const items = [
    { label: '累计收益', paper: value.total_return, diff: value.total_return_diff },
    { label: '超额收益', paper: value.excess_return, diff: value.excess_return_diff },
    { label: '最大回撤', paper: value.max_drawdown, diff: value.max_drawdown_diff },
  ]
  return (
    <div className="card p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">与同区间人工回测的差值</h3>
        <span className="text-[11px] text-muted-foreground">
          回测 <span className="font-mono">{value.bt_id.slice(0, 8)}</span> · 截至 {value.as_of}
        </span>
      </div>
      <div className="mt-3 grid grid-cols-1 sm:grid-cols-3 gap-2">
        {items.map((item) => (
          <div key={item.label} className="rounded-lg border border-border px-3 py-2">
            <p className="text-[11px] text-muted-foreground">{item.label}</p>
            <p className={`mt-1 text-sm font-semibold num ${pctColor(item.diff)}`}>
              {fmtPp(item.diff)}
            </p>
            <p className="text-[11px] text-muted-foreground num">
              纸面 {fmtRatio(item.paper, 2)}
            </p>
          </div>
        ))}
      </div>
      <p className="mt-2 text-[11px] text-muted-foreground">
        差值为「纸面 − 回测」，正数表示纸面跑赢回测口径；只应来自区间末点差异。
      </p>
    </div>
  )
}

const holdingColumns: DataTableColumn<PaperHolding>[] = [
  {
    key: 'code',
    header: '代码',
    value: (row) => row.code,
    sortable: true,
    render: (row) => (
      <Link to={`/stocks/${row.code}`} className="font-mono text-brass-strong hover:underline dark:text-brass">
        {row.code}
      </Link>
    ),
  },
  { key: 'name', header: '名称', value: (row) => row.name },
  {
    key: 'weight',
    header: '权重',
    align: 'right',
    sortable: true,
    value: (row) => row.weight,
    render: (row) => fmtRatio(row.weight, 2),
  },
  {
    key: 'cost',
    header: '成本',
    align: 'right',
    value: (row) => row.cost,
    render: (row) => fmtNumber(row.cost, 3),
  },
  {
    key: 'price',
    header: '现价',
    align: 'right',
    value: (row) => row.price,
    render: (row) => fmtNumber(row.price, 3),
  },
  {
    key: 'pnl',
    header: '浮动盈亏',
    align: 'right',
    sortable: true,
    value: (row) => row.pnl_pct,
    render: (row) => (
      <span className={`num ${pctColor(row.pnl_pct)}`}>{fmtRatio(row.pnl_pct, 2)}</span>
    ),
  },
]

function DrawdownChart({ series }: { series: PaperNavPoint[] }) {
  const data = useMemo(
    () =>
      series.map((point) => ({
        date: point.date,
        drawdown: point.drawdown === null ? null : point.drawdown * 100,
      })),
    [series],
  )
  return (
    <div>
      <h3 className="text-sm font-medium mb-2">回撤（%）</h3>
      <ResponsiveContainer width="100%" height={140}>
        <AreaChart data={data} margin={{ top: 4, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
          <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={shortDate} minTickGap={30} />
          <YAxis
            tick={{ fontSize: 11 }}
            tickFormatter={(value) => `${Number(value).toFixed(1)}%`}
            domain={['auto', 0]}
          />
          <Tooltip
            formatter={(value) => [`${Number(value).toFixed(2)}%`, '回撤']}
            labelFormatter={(label) => `日期 ${label}`}
          />
          <Area
            type="monotone"
            dataKey="drawdown"
            stroke="var(--color-down)"
            fill="var(--color-down)"
            fillOpacity={0.18}
            strokeWidth={1.5}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

function PaperDetailPanel({
  detail,
  syncing,
  onSync,
}: {
  detail: PaperDetail
  syncing: boolean
  onSync: () => void
}) {
  const { summary } = detail
  const metricItems = [
    {
      label: '累计净值',
      value: summary.nav !== null ? fmtNumber(summary.nav, 4) : '—',
      tone: '',
    },
    {
      label: '累计收益',
      value: fmtRatio(summary.total_return, 2),
      tone: pctColor(summary.total_return),
    },
    {
      label: '超额收益',
      value: fmtRatio(summary.excess_return, 2),
      tone: pctColor(summary.excess_return),
    },
    {
      label: '最大回撤',
      value: fmtRatio(summary.max_drawdown, 2),
      tone: 'text-down',
    },
    { label: '跟踪交易日', value: String(detail.tracking_days), tone: '' },
    {
      label: '现金占比',
      value: fmtRatio(detail.cash_ratio, 1),
      tone: '',
    },
  ]

  return (
    <motion.section
      id="paper-detail"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="space-y-4 scroll-mt-20"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Wallet className="h-4 w-4 text-brass" aria-hidden="true" />
            <span className="eyebrow">Portfolio Detail</span>
          </div>
          <h2 className="mt-1.5 flex flex-wrap items-center gap-2 text-[19px] font-semibold tracking-[-0.02em]">
            {detail.name}
            <StatusBadge status={detail.status} />
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            <span className="font-mono uppercase">{detail.algo_code}</span> · 建仓 {detail.start_date ?? '—'} ·
            每 {detail.params.rebalance_days ?? '—'} 日调仓 · 持有 {detail.params.max_positions ?? '—'} 只
          </p>
          <p className={`mt-1 text-[11px] ${detail.freshness.stale ? 'text-warning' : 'text-muted-foreground'}`}>
            {freshnessText(detail.freshness)}
          </p>
        </div>
        <button
          type="button"
          onClick={onSync}
          disabled={syncing}
          className="btn btn-sm h-9"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${syncing ? 'animate-spin' : ''}`} />
          {syncing ? '后台重算中…' : '手动同步'}
        </button>
      </div>

      <PendingOrders
        orders={detail.pending_orders}
        rebalanceDays={detail.params.rebalance_days}
      />

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
        {metricItems.map((item) => (
          <div key={item.label} className="inset px-3 py-2.5">
            <p className="text-[10px] tracking-wide text-muted-foreground">{item.label}</p>
            <p className={`num mt-1 text-[14px] font-semibold ${item.tone}`}>{item.value}</p>
          </div>
        ))}
      </div>

      {detail.vs_backtest && <VsBacktestPanel value={detail.vs_backtest} />}

      {detail.series.length > 0 ? (
        <>
          <div className="card p-4">
            <h3 className="text-sm font-medium mb-2">净值 vs 基准</h3>
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={detail.series} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={shortDate} minTickGap={30} />
                <YAxis
                  tick={{ fontSize: 11 }}
                  domain={['auto', 'auto']}
                  tickFormatter={(value) => Number(value).toFixed(2)}
                />
                <Tooltip
                  formatter={(value, name) => [
                    Number(value).toFixed(4),
                    name === 'nav' ? '纸面净值' : '基准净值',
                  ]}
                  labelFormatter={(label) => `日期 ${label}`}
                />
                <Legend formatter={(value) => (value === 'nav' ? '纸面净值' : '基准净值')} />
                <Line
                  type="monotone"
                  dataKey="nav"
                  stroke="var(--color-primary)"
                  strokeWidth={2}
                  dot={false}
                  name="nav"
                />
                <Line
                  type="monotone"
                  dataKey="benchmark"
                  stroke="var(--color-warning)"
                  strokeWidth={1.5}
                  dot={false}
                  name="benchmark"
                />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="card p-4">
            <DrawdownChart series={detail.series} />
          </div>
        </>
      ) : (
        <EmptyState
          title="尚无净值序列"
          description="建仓日之后还没有可用的下一交易日行情；17:30 任务会自动补齐。"
        />
      )}

      <section className="space-y-2" aria-labelledby="holdings-title">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 id="holdings-title" className="text-sm font-medium">
            当前持仓
          </h3>
          <span className="text-[11px] text-muted-foreground">
            成本含手续费 · 权重按收盘总资产
          </span>
        </div>
        <DataTable
          columns={holdingColumns}
          rows={detail.holdings}
          rowKey={(row) => row.code}
          initialSort={{ key: 'weight', direction: 'desc' }}
          emptyText="当前空仓"
        />
      </section>

      <p className="text-[11px] text-muted-foreground leading-relaxed">{detail.caliber}</p>
    </motion.section>
  )
}

export default function PaperPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const selected = searchParams.get('algo') ?? ''
  const [syncing, setSyncing] = useState<{ algo: string; until: number } | null>(null)
  const [syncError, setSyncError] = useState<string | null>(null)

  const polling = syncing !== null
  const listQuery = usePaperPortfolios(polling)
  const detailQuery = usePaperDetail(selected || null, NAV_DAYS, polling && syncing?.algo === selected)
  const syncMutation = usePaperSync()
  const showListSkeleton = useDelayedFlag(listQuery.isLoading)
  const showDetailSkeleton = useDelayedFlag(detailQuery.isLoading)

  useEffect(() => {
    if (!syncing) return
    const left = syncing.until - Date.now()
    if (left <= 0) {
      setSyncing(null)
      return
    }
    const timer = window.setTimeout(() => setSyncing(null), left)
    return () => window.clearTimeout(timer)
  }, [syncing])

  const items = listQuery.data?.items ?? []
  const selectedCard = items.find((item) => item.algo_code === selected) ?? null

  const selectAlgo = (code: string) => {
    const next = new URLSearchParams(searchParams)
    next.set('algo', code)
    setSearchParams(next)
    requestAnimationFrame(() => {
      document.getElementById('paper-detail')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }

  const handleSync = async (code: string) => {
    setSyncError(null)
    try {
      await syncMutation.mutateAsync(code)
      setSyncing({ algo: code, until: Date.now() + SYNC_POLL_MS })
    } catch (error) {
      setSyncError(error instanceof Error ? error.message : '同步触发失败')
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3 }}
      className="max-w-6xl mx-auto px-4 py-6 space-y-6"
    >
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="rise">
          <div className="flex items-center gap-2.5">
            <span aria-hidden="true" className="h-px w-6 bg-brass" />
            <span className="eyebrow">Paper Portfolio</span>
          </div>
          <h1 className="page-title mt-2.5">纸面组合跟踪</h1>
          <p className="mt-2 max-w-3xl text-[12px] leading-relaxed text-muted-foreground">
            按策略首次实盘 run 自动建仓，每个交易日收盘后把建仓日至今整段重算并覆盖净值（不做增量）。
            净值口径与回测完全一致：后复权、佣金/印花税/滑点、整手向下取整。
          </p>
          <p className="mt-1.5 text-[10px] text-muted-foreground/80">
            17:30 自动执行；非交易日跳过。手动同步为完整区间重算，耗时与区间长度成正比。
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            void listQuery.refetch()
            if (selected) void detailQuery.refetch()
          }}
          disabled={listQuery.isFetching}
          className="btn btn-sm h-9"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${listQuery.isFetching ? 'animate-spin' : ''}`} />
          刷新{listQuery.isFetching ? '中…' : ''}
        </button>
      </div>

      {syncError && (
        <p className="text-xs text-alert">同步触发失败：{syncError}</p>
      )}
      {syncing && (
        <p className="text-xs text-warning">
          {syncing.algo.toUpperCase()} 已转入后台重算，完成后自动刷新（最长等待 3 分钟）。
        </p>
      )}

      {listQuery.isLoading ? (
        <div
          className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4"
          aria-busy="true"
          style={{ visibility: showListSkeleton ? 'visible' : 'hidden' }}
        >
          {Array.from({ length: 6 }).map((_, index) => (
            <SkeletonCard key={index} lines={5} />
          ))}
        </div>
      ) : listQuery.isError ? (
        <ErrorState
          title="纸面组合加载失败"
          error={listQuery.error}
          onRetry={() => listQuery.refetch()}
        />
      ) : items.length === 0 ? (
        <EmptyState title="暂无纸面组合" description="等待 17:30 任务或上方策略同步后自动出现。" />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
          {items.map((card) => (
            <PaperCard
              key={card.algo_code}
              card={card}
              selected={card.algo_code === selected}
              syncing={syncing?.algo === card.algo_code}
              onSelect={selectAlgo}
              onSync={handleSync}
            />
          ))}
        </div>
      )}

      {!selected ? (
        <EmptyState
          title="选择上方组合查看明细"
          description="明细包含净值 vs 基准、回撤、当前持仓与明日待执行清单。"
        />
      ) : selectedCard?.status === 'pending' ? (
        <EmptyState
          title={`${selectedCard.name} 尚未初始化`}
          description="该策略还没有纸面组合。点击立即同步，或等待 17:30 任务自动创建。"
          action={
            <button
              type="button"
              onClick={() => handleSync(selectedCard.algo_code)}
              disabled={syncing?.algo === selectedCard.algo_code}
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50"
            >
              <RefreshCw className="w-4 h-4" />
              立即同步
            </button>
          }
        />
      ) : detailQuery.isLoading ? (
        <div
          className="space-y-4"
          aria-busy="true"
          style={{ visibility: showDetailSkeleton ? 'visible' : 'hidden' }}
        >
          <div className="space-y-2">
            <SkeletonLine className="h-6 w-48" />
            <SkeletonLine className="h-3 w-72 max-w-full" />
          </div>
          <SkeletonKpiRow count={6} />
          <SkeletonChart height={300} />
          <SkeletonTable rows={5} cols={6} />
        </div>
      ) : detailQuery.isError ? (
        <ErrorState
          title="组合明细加载失败"
          error={detailQuery.error}
          onRetry={() => detailQuery.refetch()}
        />
      ) : detailQuery.data ? (
        <PaperDetailPanel
          detail={detailQuery.data}
          syncing={syncing?.algo === selected}
          onSync={() => handleSync(selected)}
        />
      ) : null}
    </motion.div>
  )
}
