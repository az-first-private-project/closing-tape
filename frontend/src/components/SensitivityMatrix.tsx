/**
 * 参数敏感性矩阵（V6 T10）
 * =========================
 * rebalance_days × max_positions 扫描：每格 = 一次独立回测（同引擎），
 * 热力图看超额/回撤等指标，点击单格可展开完整 T9 回测视图。
 * 扫描中 3s 轮询；标签页隐藏或矩阵完成即停止（见 useBacktestGridDetail）。
 */
import { Fragment, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { LayoutGrid, Play, RotateCw } from 'lucide-react'
import {
  useBacktestDetail,
  useBacktestGrid,
  useBacktestGridDetail,
  useBacktestGridRetry,
} from '../hooks/useBacktest'
import { fetchHealth } from '../api/meta'
import type { AlgoParams } from '../types/algo'
import type { BacktestGridCell, BacktestGridMetrics } from '../types/backtest'
import { trendHeat, type HeatStyle } from '../lib/color'
import { fmtDuration, fmtInt, fmtNumber, fmtRatio } from '../lib/format'
import { EmptyState, ErrorState, LoadingState } from './StateViews'
import BacktestResultView from './BacktestResultView'

interface SensitivityMatrixProps {
  algoCode: string
  strategyParams: AlgoParams
  /** 与单次回测共享的起始日 */
  defaultStart: string
  defaultRebalanceDays: number
  defaultPositions: number
  /** 标签页是否可见；隐藏时停止轮询 */
  active: boolean
}

type MetricKey = 'excess' | 'drawdown' | 'sharpe' | 'win' | 'trades'

const METRIC_OPTIONS: { key: MetricKey; label: string }[] = [
  { key: 'excess', label: '超额收益' },
  { key: 'drawdown', label: '最大回撤' },
  { key: 'sharpe', label: 'Sharpe' },
  { key: 'win', label: '胜率' },
  { key: 'trades', label: '交易笔数' },
]

const MAX_CELLS = 24

function uniqueSorted(values: number[], minimum: number, maximum: number): number[] {
  const seen = new Set<number>()
  const result: number[] = []
  for (const raw of values) {
    const value = Math.min(maximum, Math.max(minimum, Math.round(raw)))
    if (Number.isFinite(value) && !seen.has(value)) {
      seen.add(value)
      result.push(value)
    }
  }
  return result.sort((a, b) => a - b)
}

function todayString(): string {
  const date = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** 比率 → 带符号百分数（抑制 -0.00%） */
function pctText(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  const percent = Math.abs(value * 100) < 0.5 * 10 ** -digits ? 0 : value * 100
  const sign = percent > 0 ? '+' : ''
  return `${sign}${percent.toFixed(digits)}%`
}

function metricText(key: MetricKey, metrics: BacktestGridMetrics): string {
  switch (key) {
    case 'excess':
      return pctText(metrics.excess_return)
    case 'drawdown':
      return pctText(metrics.max_drawdown)
    case 'sharpe':
      return fmtNumber(metrics.sharpe, 2)
    case 'win':
      return fmtRatio(metrics.win_rate, 1)
    case 'trades':
      return fmtInt(metrics.trade_count)
  }
}

function metricHeat(key: MetricKey, metrics: BacktestGridMetrics): HeatStyle {
  if (key === 'excess') return trendHeat((metrics.excess_return ?? 0) * 100, 5)
  if (key === 'drawdown') return trendHeat((metrics.max_drawdown ?? 0) * 100, 20)
  return { background: 'var(--color-card)', color: 'var(--color-foreground)' }
}

const STATUS_META: Record<string, { label: string; dot: string; text: string }> = {
  queued: { label: '排队', dot: 'bg-muted-foreground/50', text: 'text-muted-foreground' },
  running: { label: '运行中', dot: 'bg-brass animate-pulse', text: 'text-brass-strong dark:text-brass' },
  success: { label: '完成', dot: 'bg-up', text: 'text-up' },
  failed: { label: '失败', dot: 'bg-alert', text: 'text-alert' },
}

function statusMeta(status: string) {
  return STATUS_META[status] ?? { label: status, dot: 'bg-muted-foreground/50', text: 'text-muted-foreground' }
}

function AxisPicker({
  label,
  presets,
  selected,
  onToggle,
}: {
  label: string
  presets: number[]
  selected: number[]
  onToggle: (value: number) => void
}) {
  return (
    <div className="space-y-1.5">
      <p className="text-xs text-muted-foreground">{label}</p>
      <div className="flex flex-wrap gap-1.5">
        {presets.map((value) => {
          const active = selected.includes(value)
          return (
            <button
              key={value}
              type="button"
              onClick={() => onToggle(value)}
              aria-pressed={active}
              className={`px-2 py-1 rounded-md border text-xs num transition-colors ${
                active
                  ? 'border-brass/60 bg-brass/[0.08] text-brass-strong dark:text-brass'
                  : 'border-border text-muted-foreground hover:bg-muted'
              }`}
            >
              {value}
            </button>
          )
        })}
      </div>
    </div>
  )
}

export default function SensitivityMatrix({
  algoCode,
  strategyParams,
  defaultStart,
  defaultRebalanceDays,
  defaultPositions,
  active,
}: SensitivityMatrixProps) {
  const [start, setStart] = useState(defaultStart || todayString())
  // 默认结束日取库内最新交易日，避免「行情落后于自然日」时整张矩阵失败
  const [endOverride, setEndOverride] = useState('')
  const healthQuery = useQuery({ queryKey: ['health'], queryFn: fetchHealth, staleTime: 5 * 60 * 1000 })
  const latestTradeDate =
    healthQuery.data?.latest_trade_date || healthQuery.data?.trade_date || ''
  const end = endOverride || latestTradeDate || todayString()
  const [rebalanceValues, setRebalanceValues] = useState(() =>
    uniqueSorted([defaultRebalanceDays, 20, 40], 1, 250),
  )
  const [positionValues, setPositionValues] = useState(() =>
    uniqueSorted([defaultPositions, Math.max(5, Math.round(defaultPositions / 2))], 1, 100),
  )
  const [gridId, setGridId] = useState<string | null>(null)
  const [metric, setMetric] = useState<MetricKey>('excess')
  const [selectedBtId, setSelectedBtId] = useState<string | null>(null)

  const gridMutation = useBacktestGrid()
  const detailQuery = useBacktestGridDetail(gridId, active)
  const retryMutation = useBacktestGridRetry(gridId)
  const detail = detailQuery.data
  const cellDetail = useBacktestDetail(selectedBtId)

  const rebalancePresets = useMemo(
    () => uniqueSorted([5, 10, 15, 20, 30, 40, 60, defaultRebalanceDays], 1, 250),
    [defaultRebalanceDays],
  )
  const positionPresets = useMemo(
    () => uniqueSorted([5, 10, 15, 20, 30, 50, 100, defaultPositions], 1, 100),
    [defaultPositions],
  )

  const cellCount = rebalanceValues.length * positionValues.length
  const scanning = !!gridId && (!detail || detail.progress.done < detail.progress.total)
  const datesValid = !!start && !!end && start < end

  const toggle = (values: number[], value: number): number[] => {
    if (!values.includes(value)) return uniqueSorted([...values, value], 1, 250)
    if (values.length === 1) return values
    return values.filter((item) => item !== value)
  }

  const handleScan = () => {
    setSelectedBtId(null)
    gridMutation.mutate({
      algoCode,
      request: {
        start_date: start,
        end_date: end,
        rebalance_days: rebalanceValues,
        max_positions: positionValues,
        strategy: strategyParams,
      },
    }, {
      onSuccess: (data) => setGridId(data.grid_id),
    })
  }

  const cellMap = useMemo(() => {
    const map = new Map<string, BacktestGridCell>()
    for (const cell of detail?.cells ?? []) {
      map.set(`${cell.rebalance_days}-${cell.max_positions}`, cell)
    }
    return map
  }, [detail])

  const selectedCell = useMemo(
    () => detail?.cells.find((cell) => cell.bt_id === selectedBtId) ?? null,
    [detail, selectedBtId],
  )

  /** 默认参数格 + 最优格 + 邻域极差（全部按超额收益 pp 计） */
  const overfit = useMemo(() => {
    if (!detail) return null
    const usable = detail.cells.filter(
      (cell) => cell.status === 'success' && cell.aligned && cell.metrics?.excess_return != null,
    )
    if (usable.length === 0) return null

    const defaultCell =
      usable.find(
        (cell) =>
          cell.rebalance_days === defaultRebalanceDays &&
          cell.max_positions === defaultPositions,
      ) ?? null
    const best = usable.reduce((winner, cell) =>
      (cell.metrics?.excess_return ?? -Infinity) > (winner.metrics?.excess_return ?? -Infinity)
        ? cell
        : winner,
    )
    if (!defaultCell) return { defaultCell: null, best, neighborRange: null, warning: false }

    const rbIndex = detail.axes.rebalance_days.indexOf(defaultCell.rebalance_days)
    const mpIndex = detail.axes.max_positions.indexOf(defaultCell.max_positions)
    const neighborKeys = new Set<string>()
    for (const [r, m] of [
      [rbIndex - 1, mpIndex],
      [rbIndex + 1, mpIndex],
      [rbIndex, mpIndex - 1],
      [rbIndex, mpIndex + 1],
    ]) {
      const rb = detail.axes.rebalance_days[r]
      const mp = detail.axes.max_positions[m]
      if (rb !== undefined && mp !== undefined) neighborKeys.add(`${rb}-${mp}`)
    }
    neighborKeys.delete(`${defaultCell.rebalance_days}-${defaultCell.max_positions}`)
    const neighborValues = usable
      .filter((cell) => neighborKeys.has(`${cell.rebalance_days}-${cell.max_positions}`))
      .map((cell) => (cell.metrics?.excess_return ?? 0) * 100)
    const neighborRange =
      neighborValues.length >= 2 ? Math.max(...neighborValues) - Math.min(...neighborValues) : null

    const defaultExcess = (defaultCell.metrics?.excess_return ?? 0) * 100
    const bestExcess = (best.metrics?.excess_return ?? 0) * 100
    const warning =
      bestExcess - defaultExcess >= 2 && neighborRange !== null && neighborRange >= 2

    return {
      defaultCell,
      best,
      neighborRange,
      warning,
      defaultExcess,
      bestExcess,
    }
  }, [detail, defaultRebalanceDays, defaultPositions])

  const alignedCells = detail?.cells.filter((cell) => cell.status === 'success' && cell.aligned) ?? []
  const unaligned = detail?.cells.filter((cell) => cell.status === 'success' && !cell.aligned) ?? []

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm font-medium">
        <LayoutGrid className="h-3.5 w-3.5 text-brass" />
        参数敏感性矩阵
        <span className="text-[12px] font-normal text-muted-foreground">
          每格独立回测（同一引擎），用于观察稳健性；策略参数沿用左侧面板当前值
        </span>
      </div>

      {/* 参数区 */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-3">
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
          <span className="text-muted-foreground">结束日期</span>
          <input
            type="date"
            value={end}
            onChange={(event) => setEndOverride(event.target.value)}
            className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm num focus:outline-none focus:ring-2 focus:ring-primary"
          />
        </label>
        <AxisPicker
          label="持仓天数 rebalance_days（多选）"
          presets={rebalancePresets}
          selected={rebalanceValues}
          onToggle={(value) => setRebalanceValues((previous) => toggle(previous, value))}
        />
        <AxisPicker
          label="持仓数 max_positions（多选）"
          presets={positionPresets}
          selected={positionValues}
          onToggle={(value) => setPositionValues((previous) => toggle(previous, value))}
        />
      </div>

      <div className="flex flex-wrap items-center gap-3 text-xs">
        <span className={cellCount > MAX_CELLS ? 'text-alert' : 'text-muted-foreground'}>
          {rebalanceValues.length} × {positionValues.length} = {cellCount} 格
          {cellCount > MAX_CELLS ? `（上限 ${MAX_CELLS} 格，请减少取值）` : ''}
        </span>
        <span className="text-muted-foreground">单格可达分钟级，扫描耗时与格数成正比</span>
        <button
          type="button"
          onClick={handleScan}
          disabled={gridMutation.isPending || !datesValid || cellCount > MAX_CELLS || cellCount === 0}
          className="ml-auto inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm bg-primary text-primary-foreground hover:opacity-90 transition-opacity disabled:opacity-50"
        >
          {gridMutation.isPending || scanning ? (
            <span className="skeleton h-4 w-4 rounded-full" aria-hidden="true" />
          ) : (
            <Play className="w-4 h-4" />
          )}
          {gridMutation.isPending ? '提交中…' : scanning ? '扫描中…' : gridId ? '重新扫描' : '开始扫描'}
        </button>
      </div>

      {!datesValid && <p className="text-xs text-alert">结束日期必须晚于起始日期。</p>}

      {gridMutation.isError && (
        <ErrorState
          title="矩阵创建失败"
          error={gridMutation.error}
          onRetry={handleScan}
          className="border-0 bg-transparent p-6"
        />
      )}

      {!gridId && !gridMutation.isPending && !gridMutation.isError && (
        <EmptyState
          title="尚未扫描参数矩阵"
          description="选择两轴取值后点击「开始扫描」。每格是独立回测，可点击查看完整回测详情；矩阵仅用于观察参数稳健性。"
          className="border-0 shadow-none"
        />
      )}

      {gridId && detailQuery.isError && (
        <ErrorState
          title="矩阵状态获取失败"
          error={detailQuery.error}
          onRetry={() => detailQuery.refetch()}
          className="border-0 bg-transparent p-6"
        />
      )}

      {gridId && !detail && !detailQuery.isError && (
        <LoadingState text="矩阵已提交，等待后台执行…" className="border-0 shadow-none" />
      )}

      {detail && (
        <div className="space-y-4">
          {/* 进度与元信息 */}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span className="font-mono" title={detail.grid_id}>grid_id: {detail.grid_id}</span>
            <span>
              区间 {detail.start_date} ~ {detail.end_date}
            </span>
            <span>
              完成 {detail.progress.done}/{detail.progress.total}
              {detail.progress.failed > 0 ? ` · 失败 ${detail.progress.failed}` : ''}
            </span>
            {detail.progress.failed > 0 && (
              <button
                type="button"
                onClick={() => retryMutation.mutate()}
                disabled={retryMutation.isPending}
                title="只重跑失败格；成功格保持原记录，运行中的格不会被重复提交"
                className="inline-flex items-center gap-1 rounded-md border border-alert/40 bg-alert/10 px-2 py-0.5 text-[12px] text-alert transition-colors hover:bg-alert/15 disabled:opacity-50"
              >
                <RotateCw className={`h-3 w-3 ${retryMutation.isPending ? 'animate-spin' : ''}`} />
                {retryMutation.isPending ? '重试提交中…' : `重试 ${detail.progress.failed} 个失败格`}
              </button>
            )}
            {retryMutation.isError && (
              <span className="text-[12px] text-alert">重试提交失败，请稍后再试</span>
            )}
            <span title="复用格计入其历史计算耗时">计算耗时合计 {fmtDuration(detail.total_elapsed_ms)}</span>
            {gridMutation.data?.start_trimmed && (
              <span className="text-warning">
                起始日已按数据起点裁剪至 {gridMutation.data.effective_start}
              </span>
            )}
          </div>

          {/* 扫描中逐格状态点 */}
          {scanning && (
            <div className="flex flex-wrap gap-1.5">
              {detail.cells.map((cell) => {
                const meta = statusMeta(cell.status)
                return (
                  <span
                    key={cell.bt_id}
                    className="inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-[12px]"
                    title={cell.error || `${cell.rebalance_days} 日 / ${cell.max_positions} 只`}
                  >
                    <span className={`h-1.5 w-1.5 rounded-full ${meta.dot}`} />
                    <span className="num">{cell.rebalance_days}/{cell.max_positions}</span>
                    <span className={meta.text}>{meta.label}</span>
                  </span>
                )
              })}
            </div>
          )}

          {/* 热力图 */}
          {detail.axes.rebalance_days.length > 0 && detail.axes.max_positions.length > 0 && (
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                {METRIC_OPTIONS.map((option) => (
                  <button
                    key={option.key}
                    type="button"
                    onClick={() => setMetric(option.key)}
                    aria-pressed={metric === option.key}
                    className={`px-2 py-1 rounded-md border text-[12px] transition-colors ${
                      metric === option.key
                        ? 'border-brass/60 bg-brass/[0.08] text-brass-strong dark:text-brass'
                        : 'border-border text-muted-foreground hover:bg-muted'
                    }`}
                  >
                    {option.label}
                  </button>
                ))}
                <span className="text-[12px] text-muted-foreground">
                  格内第一行 = 当前指标，第二行 = {metric === 'drawdown' ? '超额' : '回撤'}；点击查看完整回测
                </span>
              </div>

              <div className="overflow-x-auto">
                <div
                  className="min-w-[560px] grid gap-1"
                  style={{
                    gridTemplateColumns: `72px repeat(${detail.axes.rebalance_days.length}, minmax(88px, 1fr))`,
                  }}
                >
                  <div />
                  {detail.axes.rebalance_days.map((rb) => (
                    <div key={`head-${rb}`} className="text-center text-[12px] text-muted-foreground num">
                      {rb} 日
                    </div>
                  ))}
                  {detail.axes.max_positions.map((mp) => (
                    <Fragment key={`row-${mp}`}>
                      <div className="flex items-center justify-end pr-1 text-[12px] text-muted-foreground num">
                        {mp} 只
                      </div>
                      {detail.axes.rebalance_days.map((rb) => {
                        const cell = cellMap.get(`${rb}-${mp}`)
                        const success = cell?.status === 'success' && cell.aligned && !!cell.metrics
                        const heat = success ? metricHeat(metric, cell.metrics as BacktestGridMetrics) : null
                        const selected = cell?.bt_id === selectedBtId
                        return (
                          <button
                            key={`${rb}-${mp}`}
                            type="button"
                            onClick={() => cell && success && setSelectedBtId(cell.bt_id)}
                            disabled={!success}
                            title={
                              cell
                                ? `${rb} 日 / ${mp} 只 · ${statusMeta(cell.status).label}` +
                                  (cell.elapsed_ms != null ? ` · 耗时 ${fmtDuration(cell.elapsed_ms)}` : '') +
                                  (cell.reused ? ' · 复用已有回测' : '') +
                                  (cell.error ? `\n${cell.error}` : '')
                                : '该格尚未返回'
                            }
                            style={heat ?? undefined}
                            className={`min-h-[64px] rounded-md border p-1.5 text-center transition-shadow ${
                              selected ? 'border-primary ring-2 ring-primary' : 'border-border'
                            } ${success ? 'cursor-pointer hover:brightness-110' : 'cursor-default'} ${
                              cell && !cell.aligned && cell.status === 'success' ? 'opacity-60' : ''
                            }`}
                          >
                            {success ? (
                              <>
                                <span className="block text-xs font-semibold num">
                                  {metricText(metric, cell.metrics as BacktestGridMetrics)}
                                </span>
                                <span className="block text-[12px] num opacity-80">
                                  {metric === 'drawdown'
                                    ? `超额 ${pctText(cell.metrics?.excess_return)}`
                                    : `回撤 ${pctText(cell.metrics?.max_drawdown)}`}
                                </span>
                                <span className="mt-1 flex items-center justify-center gap-1 text-[12px] opacity-60">
                                  {cell.reused && <span>复用</span>}
                                  <span className="num">{fmtDuration(cell.elapsed_ms)}</span>
                                </span>
                              </>
                            ) : (
                              <span className="flex h-full flex-col items-center justify-center gap-1 text-[12px]">
                                <span className="flex items-center gap-1">
                                  <span className={`h-1.5 w-1.5 rounded-full ${statusMeta(cell?.status ?? 'queued').dot}`} />
                                  {statusMeta(cell?.status ?? 'queued').label}
                                </span>
                                {cell?.status === 'failed' && <span className="text-[12px] text-alert">查看原因见提示</span>}
                                {cell && cell.status === 'success' && !cell.aligned && (
                                  <span className="text-[12px] text-warning">区间/日历不一致</span>
                                )}
                              </span>
                            )}
                          </button>
                        )
                      })}
                    </Fragment>
                  ))}
                </div>
              </div>

              {unaligned.length > 0 && (
                <p className="text-[12px] text-warning">
                  有 {unaligned.length} 格与矩阵主区间/日历不一致，已从对比中剔除（仍可点击查看单格回测）。
                </p>
              )}

              {/* 过拟合提示 */}
              {overfit && (
                <div className="rounded-lg border border-border bg-muted/30 p-3 text-[12px] space-y-1">
                  {overfit.defaultCell ? (
                    <>
                      <p>
                        默认参数格（{overfit.defaultCell.rebalance_days} 日 /{' '}
                        {overfit.defaultCell.max_positions} 只）超额{' '}
                        {pctText(overfit.defaultCell.metrics?.excess_return)}；矩阵最优格（
                        {overfit.best.rebalance_days} 日 / {overfit.best.max_positions} 只）超额{' '}
                        {pctText(overfit.best.metrics?.excess_return)}；
                        {overfit.neighborRange === null
                          ? '邻域样本不足，无法评估稳健度。'
                          : `邻域稳健度（相邻格超额极差）${overfit.neighborRange.toFixed(2)} pp。`}
                      </p>
                      {overfit.warning && (
                        <p className="text-alert">
                          默认参数明显劣于最优且邻域波动大：默认参数为约定口径，矩阵仅用于观察稳健性，
                          <strong>不建议把最优格当作实盘参数</strong>（极可能是过拟合）。
                        </p>
                      )}
                    </>
                  ) : (
                    <p className="text-muted-foreground">
                      本次矩阵未包含默认参数格（{defaultRebalanceDays} 日 / {defaultPositions} 只），无法做默认 vs 最优对照。
                    </p>
                  )}
                </div>
              )}

              <p className="text-[12px] text-muted-foreground">
                矩阵只展示已成功且区间对齐的 {alignedCells.length} 格；单格失败不影响其余格，失败原因见格子提示。
              </p>
            </div>
          )}

          {/* 单格完整回测 */}
          {selectedCell && (
            <section className="rounded-lg border border-border p-3 space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h4 className="text-sm font-medium">
                  格详情 · {selectedCell.rebalance_days} 日 / {selectedCell.max_positions} 只
                  {selectedCell.reused && (
                    <span className="ml-1.5 text-[12px] text-muted-foreground">（复用已有回测）</span>
                  )}
                </h4>
                <span className="text-[12px] text-muted-foreground num">
                  单格耗时 {fmtDuration(selectedCell.elapsed_ms)}
                </span>
              </div>
              {cellDetail.isLoading ? (
                <LoadingState text="加载回测详情…" className="border-0 shadow-none" />
              ) : cellDetail.isError ? (
                <ErrorState
                  title="回测详情加载失败"
                  error={cellDetail.error}
                  onRetry={() => cellDetail.refetch()}
                  className="border-0 bg-transparent p-6"
                />
              ) : cellDetail.data ? (
                <BacktestResultView result={cellDetail.data} />
              ) : null}
            </section>
          )}
        </div>
      )}
    </div>
  )
}
