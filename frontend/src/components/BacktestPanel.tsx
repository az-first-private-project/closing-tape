/**
 * 回测面板（契约第 6 节 + T10 参数敏感性）
 * =========================
 * 单次回测：起始日 / 持仓天数 / 持仓数 → POST /backtest/{algo_code}
 * 参数敏感性：rebalance_days × max_positions 矩阵 → POST /backtest/{algo_code}/grid
 * 两个标签页共用同一份回测结果视图（BacktestResultView）。
 */
import { useState } from 'react'
import { Download, FlaskConical } from 'lucide-react'
import { useBacktest } from '../hooks/useBacktest'
import { downloadBacktestCsv } from '../api/backtest'
import { ApiError } from '../api/client'
import type { AlgoParams } from '../types/algo'
import { EmptyState, ErrorState } from './StateViews'
import BacktestResultView from './BacktestResultView'
import SensitivityMatrix from './SensitivityMatrix'

interface BacktestPanelProps {
  algoCode: string
  algoName?: string
  /** 当前参数面板里的策略参数快照，回测时原样传给后端 */
  strategyParams: AlgoParams
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

export default function BacktestPanel({ algoCode, algoName, strategyParams }: BacktestPanelProps) {
  const mutation = useBacktest()

  const [tab, setTab] = useState<PanelTab>('single')
  const [start, setStart] = useState(defaultStartDate)
  // B 系列是短线策略，默认持有 5 个交易日；A3/A4 维持 20 日
  const [rebalanceDays, setRebalanceDays] = useState(() => (algoCode.startsWith('b') ? 5 : 20))
  // 持仓数与「返回数量」绑定：回测持有算法本次返回的全部标的，不再单独设一个默认 20
  const strategyLimit = Number(strategyParams.limit)
  const boundPositions =
    Number.isFinite(strategyLimit) && strategyLimit > 0
      ? Math.min(100, Math.max(1, Math.round(strategyLimit)))
      : 20
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)

  const result = mutation.data

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
    `rounded-[6px] px-2.5 py-1 text-[10px] font-medium transition-all duration-300 ${
      active
        ? 'bg-card text-foreground shadow-[0_1px_2px_rgba(0,0,0,0.08)] ring-1 ring-border/70'
        : 'text-muted-foreground hover:text-foreground'
    }`

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
              role="tab"
              aria-selected={tab === 'single'}
              onClick={() => setTab('single')}
              className={tabClass(tab === 'single')}
            >
              单次回测
            </button>
            <button
              role="tab"
              aria-selected={tab === 'grid'}
              onClick={() => setTab('grid')}
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

      {/* 单次回测 */}
      <div className={tab === 'single' ? 'space-y-4' : 'hidden'}>
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
              disabled={mutation.isPending}
              className="btn btn-primary w-full"
            >
              {mutation.isPending ? (
                <span className="skeleton h-4 w-4 rounded-full" aria-hidden="true" />
              ) : (
                <FlaskConical className="w-4 h-4" />
              )}
              {mutation.isPending ? '回测中…' : '运行回测'}
            </button>
          </div>
        </div>

        {exportError && <p className="text-xs text-alert">{exportError}</p>}

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
          <BacktestResultView result={result} />
        )}
      </div>

      {/* 参数敏感性矩阵 */}
      <div className={tab === 'grid' ? 'block' : 'hidden'}>
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
