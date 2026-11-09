/**
 * 回测面板（契约第 6 节 + T10 参数敏感性）
 * =========================
 * 单次回测：起始日 / 持仓天数 / 持仓数 → POST /backtest/{algo_code}
 * 参数敏感性：rebalance_days × max_positions 矩阵 → POST /backtest/{algo_code}/grid
 * 两个标签页共用同一份回测结果视图（BacktestResultView）。
 */
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { Download, FlaskConical } from 'lucide-react'
import { useBacktest } from '../hooks/useBacktest'
import { downloadBacktestCsv } from '../api/backtest'
import { ApiError } from '../api/client'
import { getActiveBacktest, loadPanelState, savePanelState } from '../lib/backtestSession'
import type { AlgoParams } from '../types/algo'
import { EmptyState, ErrorState, LoadingState } from './StateViews'
import BacktestResultView from './BacktestResultView'
import SensitivityMatrix from './SensitivityMatrix'

interface BacktestPanelProps {
  algoCode: string
  algoName?: string
  /** 当前参数面板里的策略参数快照，回测时原样传给后端 */
  strategyParams: AlgoParams
  /** 参数面板与上方展示 run 是否不一致（U01） */
  panelDirty?: boolean
  changedKeys?: string[]
  /** 上方展示结果的 run_id，用于明确回测参数与结果的差异 */
  resultRunId?: string | null
}

type PanelTab = 'single' | 'grid'

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

export default function BacktestPanel({
  algoCode,
  algoName,
  strategyParams,
  panelDirty = false,
  changedKeys = [],
  resultRunId,
}: BacktestPanelProps) {
  const mutation = useBacktest(algoCode)
  const [savedPanel] = useState(() => loadPanelState(algoCode))
  const [savedTask] = useState(() => getActiveBacktest(algoCode))
  const appliedTaskRef = useRef<string | null>(savedTask?.btId ?? null)

  const [tab, setTab] = useState<PanelTab>(savedPanel?.tab ?? 'single')
  const [start, setStart] = useState(
    () => savedTask?.start || savedPanel?.start || defaultStartDate(),
  )
  // B 系列是短线策略，默认持有 5 个交易日；A3/A4 维持 20 日
  const [rebalanceDays, setRebalanceDays] = useState(
    () =>
      savedTask?.rebalanceDays ??
      savedPanel?.rebalanceDays ??
      (algoCode.startsWith('b') ? 5 : 20),
  )
  // 持仓数与「返回数量」绑定：回测持有算法本次返回的全部标的，不再单独设一个默认 20
  const strategyLimit = Number(strategyParams.limit)
  const boundPositions =
    Number.isFinite(strategyLimit) && strategyLimit > 0
      ? Math.min(100, Math.max(1, Math.round(strategyLimit)))
      : 20
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)

  const result = mutation.data

  // POST 在离开页面后才返回时，任务记录晚到，补全面板输入
  useEffect(() => {
    const task = mutation.task
    if (!task || appliedTaskRef.current === task.btId) return
    appliedTaskRef.current = task.btId
    if (task.start) setStart(task.start)
    if (typeof task.rebalanceDays === 'number') setRebalanceDays(task.rebalanceDays)
  }, [mutation.task])

  // 面板输入随策略持久化，路由切换或刷新后恢复
  useEffect(() => {
    savePanelState(algoCode, { tab, start, rebalanceDays })
  }, [algoCode, tab, start, rebalanceDays])

  const statusLabel = mutation.submitting
    ? '提交中'
    : mutation.status === 'running'
      ? '运行中'
      : mutation.status === 'queued'
        ? '排队中'
        : '连接中'

  const errorTitle =
    mutation.error instanceof ApiError && mutation.error.code === 'BACKTEST_TIMEOUT'
      ? '回测等待超时'
      : mutation.retry
        ? '回测状态获取失败'
        : '回测执行失败'

  const loadingText = mutation.submitting
    ? '正在提交回测任务…'
    : mutation.status === 'running'
      ? '回测运行中，可离开页面，返回后自动续接…'
      : mutation.status === 'queued'
        ? '回测已排队，正在等待后台执行…'
        : mutation.isPending
          ? '正在连接回测任务…'
          : '正在加载回测结果…'

  /** 发起回测 */
  const handleRun = () => {
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

  const tabClass = (active: boolean) =>
    `rounded-[6px] px-2.5 py-1 text-[12px] font-medium transition-all duration-300 ${
      active
        ? 'bg-card text-foreground shadow-[0_1px_2px_rgba(0,0,0,0.08)] ring-1 ring-border/70'
        : 'text-muted-foreground hover:text-foreground'
    }`

  /** 标签页键盘操作：左右方向键切换并移动焦点 */
  const handleTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
    event.preventDefault()
    const next: PanelTab = tab === 'single' ? 'grid' : 'single'
    setTab(next)
    document.getElementById(`bt-tab-${next}`)?.focus()
  }

  return (
    <div className="card p-4 space-y-4">
      {/* 标题与标签页 */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <FlaskConical className="h-3.5 w-3.5 text-brass" />
          <h3 className="text-[13px] font-semibold">策略回测</h3>
          {algoName && <span className="text-xs text-muted-foreground">· {algoName}</span>}
        </div>
        <div className="flex items-center gap-2">
          <div role="tablist" aria-label="回测视图" className="flex gap-0.5 rounded-lg border border-border/70 bg-muted/25 p-0.5">
            <button
              id="bt-tab-single"
              role="tab"
              aria-selected={tab === 'single'}
              aria-controls="bt-panel-single"
              tabIndex={tab === 'single' ? 0 : -1}
              onClick={() => setTab('single')}
              onKeyDown={handleTabKeyDown}
              className={tabClass(tab === 'single')}
            >
              单次回测
            </button>
            <button
              id="bt-tab-grid"
              role="tab"
              aria-selected={tab === 'grid'}
              aria-controls="bt-panel-grid"
              tabIndex={tab === 'grid' ? 0 : -1}
              onClick={() => setTab('grid')}
              onKeyDown={handleTabKeyDown}
              className={tabClass(tab === 'grid')}
            >
              参数敏感性
            </button>
          </div>
          {tab === 'single' && result && (
            <button
              onClick={handleExport}
              disabled={exporting}
              className="btn btn-sm"
            >
              <Download className="w-3.5 h-3.5" />
              {exporting ? '导出中…' : '导出净值 CSV'}
            </button>
          )}
        </div>
      </div>

      {panelDirty && (
        <div
          className="rounded-lg border border-warning/45 bg-warning/10 px-3 py-2 text-[12px] leading-relaxed text-warning"
          role="status"
        >
          左侧参数面板已修改
          {changedKeys.length > 0 && <span className="num">（{changedKeys.join('、')}）</span>}
          ：回测将使用<b>面板当前参数</b>；上方选股结果仍对应
          {resultRunId ? (
            <span className="font-mono"> run …{resultRunId.slice(-10)}</span>
          ) : (
            ' 上一次 run'
          )}
          。
        </div>
      )}

      {/* 单次回测 */}
      <div
        id="bt-panel-single"
        role="tabpanel"
        aria-labelledby="bt-tab-single"
        className={tab === 'single' ? 'space-y-4' : 'hidden'}
      >
        <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
          <label className="text-xs space-y-1">
            <span className="text-muted-foreground">起始日期</span>
            <input
              type="date"
              value={start}
              onChange={(event) => setStart(event.target.value)}
              className="field num"
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
              className="field num"
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
              className="field num cursor-not-allowed bg-muted/60 text-muted-foreground"
            />
          </label>
          <div className="flex items-end">
            <button
              onClick={handleRun}
              disabled={mutation.isBusy}
              className="btn btn-primary w-full"
            >
              {mutation.isBusy ? (
                <span className="skeleton h-4 w-4 rounded-full" aria-hidden="true" />
              ) : (
                <FlaskConical className="w-4 h-4" />
              )}
              {mutation.isPending ? '回测中…' : mutation.isBusy ? '加载中…' : '运行回测'}
            </button>
          </div>
        </div>

        {exportError && <p className="text-xs text-alert">{exportError}</p>}

        {mutation.isPending && (
          <div
            role="status"
            className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-brass/30 bg-brass/[0.06] px-3 py-2 text-[12px] text-brass-strong dark:text-brass"
          >
            <span className="inline-flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-brass animate-pulse" aria-hidden="true" />
              后台回测{statusLabel}
            </span>
            <span className="text-muted-foreground">可离开页面，返回后自动续接</span>
          </div>
        )}

        {mutation.isError ? (
          <ErrorState
            title={errorTitle}
            error={mutation.error}
            onRetry={mutation.retry ?? handleRun}
            className="border-0 bg-transparent p-6"
          />
        ) : mutation.isBusy ? (
          <LoadingState text={loadingText} className="border-0 shadow-none" />
        ) : !result ? (
          <EmptyState
            title="尚未运行回测"
            description="选择起始日期与持仓天数后点击「运行回测」；持仓数跟随选股返回数量。回测 ≠ 实盘，结果仅供研究参考。"
            className="border-0 shadow-none"
          />
        ) : (
          <BacktestResultView result={result} />
        )}
      </div>

      {/* 参数敏感性矩阵 */}
      <div
        id="bt-panel-grid"
        role="tabpanel"
        aria-labelledby="bt-tab-grid"
        className={tab === 'grid' ? 'block' : 'hidden'}
      >
        <SensitivityMatrix
          algoCode={algoCode}
          strategyParams={strategyParams}
          defaultStart={start}
          defaultRebalanceDays={rebalanceDays}
          defaultPositions={boundPositions}
          active={tab === 'grid'}
        />
      </div>
    </div>
  )
}
