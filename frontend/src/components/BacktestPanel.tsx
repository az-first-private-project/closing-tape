/**
 * 回测面板（契约第 6 节）
 * =========================
 * 输入起始日 / 持仓天数 / 持仓数 → POST /backtest/{algo_code}
 * → 绩效指标卡 + 净值曲线（策略 vs 基准）+ 成交明细（可折叠）+ 导出 CSV。
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Download, ExternalLink, FlaskConical, Loader2 } from 'lucide-react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useBacktest } from '../hooks/useBacktest'
import { downloadBacktestCsv } from '../api/backtest'
import { ApiError } from '../api/client'
import type { AlgoParams } from '../types/algo'
import type { BacktestMetrics } from '../types/backtest'
import { fmtDate, fmtInt, fmtNumber, fmtRatio, shortDate } from '../lib/format'
import { EmptyState, ErrorState } from './StateViews'
import DataTable, { type DataTableColumn } from './DataTable'
import BacktestExtras from './BacktestExtras'
import type { BacktestTrade } from '../types/backtest'

interface BacktestPanelProps {
  algoCode: string
  algoName?: string
  /** 当前参数面板里的策略参数快照，回测时原样传给后端 */
  strategyParams: AlgoParams
}

/** 本地日期 → YYYY-MM-DD */
function localDateString(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** 默认起始日：一年前 */
function defaultStartDate(): string {
  const date = new Date()
  date.setFullYear(date.getFullYear() - 1)
  return localDateString(date)
}

/** 指标卡 */
function MetricCard({
  label,
  value,
  tone = 'default',
}: {
  label: string
  value: string
  tone?: 'default' | 'up' | 'down'
}) {
  const color =
    tone === 'up' ? 'text-up' : tone === 'down' ? 'text-down' : 'text-foreground'
  return (
    <div className="card p-3">
      <p className="text-[11px] text-muted-foreground">{label}</p>
      <p className={`mt-1 text-sm font-semibold num ${color}`}>{value}</p>
    </div>
  )
}

/** 收益类指标 → 涨跌色（A 股口径：红涨绿跌） */
function returnTone(value: number | null): 'default' | 'up' | 'down' {
  if (value === null || Number.isNaN(value)) return 'default'
  if (value > 0) return 'up'
  if (value < 0) return 'down'
  return 'default'
}

/** 绩效指标卡组 */
function MetricsGrid({ metrics }: { metrics: BacktestMetrics }) {
  const items: { label: string; value: string; tone?: 'default' | 'up' | 'down' }[] = [
    { label: '总收益率', value: fmtRatio(metrics.total_return, 2), tone: returnTone(metrics.total_return) },
    { label: '年化收益', value: fmtRatio(metrics.annual_return, 2), tone: returnTone(metrics.annual_return) },
    { label: '最大回撤', value: fmtRatio(metrics.max_drawdown, 2), tone: returnTone(metrics.max_drawdown) },
    { label: '超额收益', value: fmtRatio(metrics.excess_return, 2), tone: returnTone(metrics.excess_return) },
    { label: '基准收益', value: fmtRatio(metrics.benchmark_return, 2), tone: returnTone(metrics.benchmark_return) },
    { label: '夏普比率', value: fmtNumber(metrics.sharpe, 2) },
    { label: '索提诺比率', value: fmtNumber(metrics.sortino, 2) },
    { label: 'Calmar', value: fmtNumber(metrics.calmar, 2) },
    { label: '胜率', value: fmtRatio(metrics.win_rate, 1) },
    { label: '盈亏比', value: fmtNumber(metrics.profit_loss_ratio, 2) },
    { label: '换手率', value: fmtNumber(metrics.turnover, 2) },
    { label: '交易次数', value: fmtInt(metrics.trade_count) },
  ]
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-2">
      {items.map((item) => (
        <MetricCard key={item.label} label={item.label} value={item.value} tone={item.tone} />
      ))}
    </div>
  )
}

export default function BacktestPanel({ algoCode, algoName, strategyParams }: BacktestPanelProps) {
  const navigate = useNavigate()
  const mutation = useBacktest()

  const [start, setStart] = useState(defaultStartDate)
  // B 系列是短线策略，默认持有 5 个交易日；A3/A4 维持 20 日
  const [rebalanceDays, setRebalanceDays] = useState(() => (algoCode.startsWith('b') ? 5 : 20))
  // 持仓数与「返回数量」绑定：回测持有算法本次返回的全部标的，不再单独设一个默认 20
  const strategyLimit = Number(strategyParams.limit)
  const boundPositions =
    Number.isFinite(strategyLimit) && strategyLimit > 0
      ? Math.min(100, Math.max(1, Math.round(strategyLimit)))
      : 20
  const [showTrades, setShowTrades] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)

  const result = mutation.data

  /** 发起回测 */
  const handleRun = () => {
    setShowTrades(false)
    setExportError(null)
    mutation.mutate({
      algoCode,
      options: {
        start: start || undefined,
        // 与后端校验一致：rebalance_days 1–250，max_positions 1–100
        rebalance_days: Math.min(250, Math.max(1, Math.round(rebalanceDays))),
        max_positions: boundPositions,
        params: strategyParams,
      },
    })
  }

  /** 导出净值曲线 CSV（服务端生成） */
  const handleExport = async () => {
    if (!result) return
    setExporting(true)
    setExportError(null)
    try {
      await downloadBacktestCsv(result.bt_id)
    } catch (error) {
      setExportError(error instanceof ApiError ? error.message : '导出失败，请稍后重试')
    } finally {
      setExporting(false)
    }
  }

  const stockLink = (code: string) => (
    result ? `/stocks/${code}?bt=${result.bt_id}` : `/stocks/${code}`
  )

  const tradeColumns: DataTableColumn<BacktestTrade>[] = [
    { key: 'date', header: '日期', value: (row) => row.date, sortable: true, render: (row) => fmtDate(row.date) },
    { key: 'code', header: '代码', value: (row) => row.code, render: (row) => <span className="font-mono">{row.code}</span> },
    { key: 'name', header: '名称', value: (row) => row.name, align: 'left' },
    {
      key: 'side',
      header: '方向',
      value: (row) => row.side,
      align: 'center',
      render: (row) => (
        <span className={row.side === 'buy' ? 'text-up font-medium' : 'text-down font-medium'}>
          {row.side === 'buy' ? '买入' : row.side === 'sell' ? '卖出' : row.side}
        </span>
      ),
    },
    { key: 'price', header: '价格', value: (row) => row.price, align: 'right', sortable: true, render: (row) => fmtNumber(row.price, 2) },
    { key: 'shares', header: '股数', value: (row) => row.shares, align: 'right', sortable: true, render: (row) => fmtInt(row.shares) },
    { key: 'amount', header: '金额', value: (row) => row.amount, align: 'right', render: (row) => fmtNumber(row.amount, 0) },
    { key: 'fee', header: '费用', value: (row) => row.fee, align: 'right', render: (row) => fmtNumber(row.fee, 2) },
    { key: 'reason', header: '原因', value: (row) => row.reason, align: 'left' },
    {
      key: 'kline',
      header: 'K 线',
      align: 'center',
      render: (row) => (
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation()
            navigate(stockLink(row.code))
          }}
          className="inline-flex items-center gap-0.5 text-[11px] text-primary hover:underline"
        >
          在 K 线查看
          <ExternalLink className="h-3 w-3" />
        </button>
      ),
    },
  ]

  return (
    <div className="card p-4 space-y-4">
      {/* 标题 */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <FlaskConical className="w-4 h-4 text-primary" />
          <h3 className="font-semibold text-sm">策略回测</h3>
          {algoName && <span className="text-xs text-muted-foreground">· {algoName}</span>}
        </div>
        {result && (
          <button
            onClick={handleExport}
            disabled={exporting}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs border border-border hover:bg-muted transition-colors disabled:opacity-50"
          >
            <Download className="w-3.5 h-3.5" />
            {exporting ? '导出中…' : '导出净值 CSV'}
          </button>
        )}
      </div>

      {/* 参数表单 */}
      <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
        <label className="text-xs space-y-1">
          <span className="text-muted-foreground">起始日期</span>
          <input
            type="date"
            value={start}
            onChange={(event) => setStart(event.target.value)}
            className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm num focus:outline-none focus:ring-2 focus:ring-primary"
          />
        </label>
        <label className="text-xs space-y-1">
          <span className="text-muted-foreground">持仓天数</span>
          <input
            type="number"
            min={1}
            max={250}
            value={rebalanceDays}
            onChange={(event) => setRebalanceDays(Number(event.target.value) || 1)}
            className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm num focus:outline-none focus:ring-2 focus:ring-primary"
          />
        </label>
        <label className="text-xs space-y-1">
          <span className="text-muted-foreground">持仓数</span>
          <input
            type="number"
            min={1}
            max={200}
            value={boundPositions}
            readOnly
            title="与选股返回数量绑定，改上方「返回数量上限」即可"
            className="w-full px-3 py-2 rounded-lg border border-border bg-muted text-sm num text-muted-foreground cursor-not-allowed"
          />
        </label>
        <div className="flex items-end">
          <button
            onClick={handleRun}
            disabled={mutation.isPending}
            className="w-full inline-flex items-center justify-center gap-1.5 px-4 py-2 rounded-lg text-sm bg-primary text-primary-foreground hover:opacity-90 transition-opacity disabled:opacity-50"
          >
            {mutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <FlaskConical className="w-4 h-4" />}
            {mutation.isPending ? '回测中…' : '运行回测'}
          </button>
        </div>
      </div>

      {exportError && <p className="text-xs text-alert">{exportError}</p>}

      {/* 错误 / 空态 / 结果 */}
      {mutation.isError ? (
        <ErrorState
          title="回测执行失败"
          error={mutation.error}
          onRetry={handleRun}
          className="border-0 bg-transparent p-6"
        />
      ) : !result ? (
        <EmptyState
          title="尚未运行回测"
          description="选择起始日期与持仓天数后点击「运行回测」；持仓数跟随选股返回数量。回测 ≠ 实盘，结果仅供研究参考。"
          className="border-0 shadow-none"
        />
      ) : (
        <div className="space-y-4">
          {/* 回测元信息 */}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span className="font-mono" title={result.bt_id}>bt_id: {result.bt_id}</span>
            <span>
              回测区间：{result.start_date} ~ {result.end_date}
            </span>
            <span>
              持仓周期 {fmtInt(result.params.rebalance_days)} 个交易日 · 持有 {fmtInt(result.params.max_positions)} 只 · 基准{' '}
              {result.params.benchmark ?? '—'}
            </span>
          </div>

          <MetricsGrid metrics={result.metrics} />

          {/* 净值曲线 */}
          {result.equity_curve.length > 0 && (
            <div>
              <h4 className="text-sm font-medium mb-2">净值曲线（策略 vs 基准）</h4>
              <ResponsiveContainer width="100%" height={320}>
                <LineChart data={result.equity_curve} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                  <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={shortDate} minTickGap={30} />
                  <YAxis tick={{ fontSize: 11 }} domain={['auto', 'auto']} tickFormatter={(value) => Number(value).toFixed(2)} />
                  <Tooltip
                    formatter={(value, name) => [Number(value).toFixed(4), name === 'nav' ? '策略净值' : '基准净值']}
                    labelFormatter={(label) => `日期 ${label}`}
                  />
                  <Legend formatter={(value) => (value === 'nav' ? '策略净值' : '基准净值')} />
                  <Line type="monotone" dataKey="nav" stroke="var(--color-primary)" strokeWidth={2} dot={false} name="nav" />
                  <Line type="monotone" dataKey="benchmark" stroke="var(--color-warning)" strokeWidth={1.5} dot={false} name="benchmark" />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* T9 派生视图：月收益 / 回撤 / 滚动超额 / 行业分布 */}
          <BacktestExtras result={result} />

          {/* 成交明细（可折叠） */}
          <div>
            <button
              onClick={() => setShowTrades((previous) => !previous)}
              className="inline-flex items-center gap-1.5 text-sm font-medium hover:text-primary transition-colors"
            >
              {showTrades ? '▾' : '▸'} 成交明细（{result.trades.length} 笔）
            </button>
            {showTrades && (
              <div className="mt-3">
                <DataTable
                  columns={tradeColumns}
                  rows={result.trades}
                  rowKey={(row, index) => `${row.date}-${row.code}-${row.side}-${index}`}
                  csvFilename={`trades_${result.bt_id}.csv`}
                  emptyText="本次回测没有成交记录"
                  maxHeight="360px"
                  onRowClick={(row) => navigate(stockLink(row.code))}
                />
              </div>
            )}
          </div>

          <p className="text-[11px] text-muted-foreground">
            ⚠️ 回测基于历史数据与理想化成交假设（T+1 成交、含手续费/印花税/滑点），不代表未来收益。
          </p>
        </div>
      )}
    </div>
  )
}
