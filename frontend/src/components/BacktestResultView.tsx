/**
 * 回测结果视图（V6 T10 从 BacktestPanel 抽出）
 * =============================================
 * 单次回测与参数矩阵格详情共用：指标卡 + 净值曲线 + T9 派生视图 + 成交明细。
 * 数据全部来自已落库回测，本组件不发起任何回测请求。
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ExternalLink } from 'lucide-react'
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
import type { BacktestMetrics, BacktestResult, BacktestTrade } from '../types/backtest'
import { fmtDate, fmtInt, fmtNumber, fmtRatio, shortDate } from '../lib/format'
import DataTable, { type DataTableColumn } from './DataTable'
import BacktestExtras from './BacktestExtras'

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

export default function BacktestResultView({ result }: { result: BacktestResult }) {
  const navigate = useNavigate()
  const [showTrades, setShowTrades] = useState(false)

  const stockLink = (code: string) => `/stocks/${code}?bt=${result.bt_id}`

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
          className="inline-flex items-center gap-0.5 text-[11px] text-brass-strong hover:underline dark:text-brass"
        >
          在 K 线查看
          <ExternalLink className="h-3 w-3" />
        </button>
      ),
    },
  ]

  return (
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
          className="inline-flex items-center gap-1.5 text-[13px] font-medium transition-colors hover:text-brass"
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
  )
}
