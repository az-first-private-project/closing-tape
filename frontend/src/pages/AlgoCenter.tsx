/**
 * 选股中心（契约第 5、6 节）
 * ===========================
 * - 算法卡片（/algo/list，参数 schema 驱动，A3/A4 + B1–B5）；
 * - 参数面板修改不自动请求，「执行算法」按钮触发 useMutation；
 * - 结果区：Run 元信息（run_id/耗时/universe/signal/data_version/参数快照/from_cache）、
 *   动态列结果表格、Top10 评分图、CSV 导出、历史 run 切换；
 * - 回测面板：POST /backtest/{algo_code}，绩效指标 + 净值曲线 + 成交明细 + 导出。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { motion } from 'framer-motion'
import { CalendarDays, Download, History } from 'lucide-react'
import { useAlgoList, useAlgoRun, useAlgoRuns, useAlgoRunDetail } from '../hooks/useAlgo'
import { useTradeCalendar } from '../hooks/useDashboard'
import { downloadAlgoCsv } from '../api/algo'
import { ApiError } from '../api/client'
import type { AlgoMeta, AlgoParams, AlgoResult } from '../types/algo'
import { rowScore } from '../types/algo'
import AlgoCard from '../components/AlgoCard'
import AlgoParamPanel from '../components/AlgoParamPanel'
import AlgoDataTable from '../components/AlgoDataTable'
import AlgoBarChart from '../components/AlgoBarChart'
import RunMetaPanel, { type RunSource } from '../components/RunMetaPanel'
import PageHeader from '../components/PageHeader'
import Disclaimer from '../components/Disclaimer'
import BacktestPanel from '../components/BacktestPanel'
import StrategyLadder from '../components/StrategyLadder'
import ConsensusBoard from '../components/ConsensusBoard'
import { EmptyState, ErrorState, LoadingState } from '../components/StateViews'
import { diffPanelAgainstRun } from '../lib/algoParams'
import { fmtInt } from '../lib/format'

/** 从 params_schema 生成默认参数（布尔默认值转字符串传入 query） */
function buildDefaultParams(algo: AlgoMeta | null): AlgoParams {
  const params: AlgoParams = {}
  algo?.params_schema.forEach((item) => {
    if (item.default === null || item.default === undefined) return
    params[item.key] = typeof item.default === 'boolean' ? String(item.default) : item.default
  })
  return params
}

/** run_id 缩短展示 */
function shortRunId(runId: string): string {
  return runId.length > 10 ? `…${runId.slice(-10)}` : runId
}

export default function AlgoCenter() {
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()
  /** 已应用过的 ?algo=，避免列表后台刷新时重置用户改过的参数 */
  const appliedAlgoParam = useRef<string | null>(null)

  // ---- 算法清单 ----
  const algoListQuery = useAlgoList()
  const algoList = algoListQuery.data ?? []

  // ---- 页面状态 ----
  const [selectedCode, setSelectedCode] = useState('')
  const [params, setParams] = useState<AlgoParams>({})
  /** 空字符串 = 自动（该策略最近数据就绪日）；否则为指定交易日 */
  const [selectedDate, setSelectedDate] = useState('')
  /** 非空表示正在查看某次历史 run 的明细 */
  const [historyRunId, setHistoryRunId] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)

  // ---- 数据请求 ----
  const runMutation = useAlgoRun()
  const runsQuery = useAlgoRuns(selectedCode || null)
  const historyQuery = useAlgoRunDetail(historyRunId)
  const calendar = useTradeCalendar(1100)

  /** 交易日历：只保留开市日，倒序（最新在前） */
  const openDays = useMemo(() => {
    const days = calendar.data?.days ?? []
    return days
      .filter((day) => day.is_open)
      .map((day) => day.cal_date)
      .sort()
      .reverse()
  }, [calendar.data])

  const selectedAlgo = useMemo<AlgoMeta | null>(
    () => algoList.find((algo) => algo.algo_code === selectedCode) ?? null,
    [algoList, selectedCode],
  )

  // 列表加载后：优先应用 ?algo= 深链（命令面板/外部链接），否则默认选中第一个启用中的算法
  useEffect(() => {
    if (algoList.length === 0) return
    const requested = searchParams.get('algo')
    if (requested) {
      const requestedAlgo = algoList.find((algo) => algo.algo_code === requested)
      if (requestedAlgo) {
        if (appliedAlgoParam.current !== requested) {
          appliedAlgoParam.current = requested
          setSelectedCode(requested)
          setParams(buildDefaultParams(requestedAlgo))
          setHistoryRunId(null)
          setExportError(null)
        }
        return
      }
    }
    appliedAlgoParam.current = null
    if (selectedCode && algoList.some((algo) => algo.algo_code === selectedCode)) return
    const first = algoList.find((algo) => algo.is_active !== false) ?? algoList[0]
    setSelectedCode(first.algo_code)
    setParams(buildDefaultParams(first))
  }, [algoList, searchParams, selectedCode])

  // #ladder 深链（/ladder 重定向、外部链接）：等开场幕布交还滚动后再定位
  useEffect(() => {
    if (location.hash !== '#ladder') return
    const started = performance.now()
    let frame = 0
    const scrollWhenReady = () => {
      const shell = document.querySelector('.app-shell')
      if (shell?.classList.contains('is-intro-ready') || performance.now() - started > 5000) {
        document.getElementById('ladder')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
        return
      }
      frame = requestAnimationFrame(scrollWhenReady)
    }
    frame = requestAnimationFrame(scrollWhenReady)
    return () => cancelAnimationFrame(frame)
  }, [location.hash])

  // ---- 交互 ----
  const handleSelect = useCallback(
    (code: string) => {
      const algo = algoList.find((item) => item.algo_code === code) ?? null
      setSelectedCode(code)
      setParams(buildDefaultParams(algo))
      setHistoryRunId(null)
      setExportError(null)
      runMutation.reset()
      // 手动换策略后 URL 不再代表当前策略，清掉深链参数
      if (searchParams.has('algo')) {
        appliedAlgoParam.current = null
        const next = new URLSearchParams(searchParams)
        next.delete('algo')
        setSearchParams(next, { replace: true })
      }
    },
    [algoList, runMutation, searchParams, setSearchParams],
  )

  const handleParamChange = useCallback((key: string, value: number | string) => {
    setParams((previous) => ({ ...previous, [key]: value }))
  }, [])

  /** 天梯行点击：选中策略并滚到下方参数/结果区 */
  const handleLadderSelect = useCallback(
    (code: string) => {
      handleSelect(code)
      requestAnimationFrame(() => {
        document.getElementById('algo-detail')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      })
    },
    [handleSelect],
  )

  const handleReset = useCallback(() => {
    setParams(buildDefaultParams(selectedAlgo))
  }, [selectedAlgo])

  /** 执行算法；forceRefresh=true 时显式忽略缓存 */
  const handleExecute = useCallback(
    (forceRefresh = false) => {
      if (!selectedCode) return
      setHistoryRunId(null)
      setExportError(null)
      // 发起新执行即清空旧结果，避免把旧 run 误读为本次参数的结果
      runMutation.reset()
      runMutation.mutate({
        algoCode: selectedCode,
        params,
        forceRefresh,
        tradeDate: selectedDate || undefined,
      })
    },
    [params, runMutation, selectedCode, selectedDate],
  )

  /** 切换交易日：旧结果不再对应当前条件，清空以免误读 */
  const handleDateChange = useCallback(
    (value: string) => {
      setSelectedDate(value)
      setHistoryRunId(null)
      setExportError(null)
      runMutation.reset()
    },
    [runMutation],
  )

  // ---- 结果来源：历史 run 明细 或 本次执行结果 ----
  const result: AlgoResult | null = historyRunId ? (historyQuery.data ?? null) : (runMutation.data ?? null)
  const resultLoading = historyRunId ? historyQuery.isLoading : runMutation.isPending
  const resultError = historyRunId ? historyQuery.error : runMutation.error
  const runOptions = runsQuery.data?.items ?? []
  /** 结果来源三态：本次执行 / 缓存结果 / 历史 run */
  const resultSource: RunSource = historyRunId ? 'history' : result?.from_cache ? 'cache' : 'live'
  /** 参数面板与当前展示 run 的差异（不平移结果，只提示） */
  const panelDiff = useMemo(
    () => diffPanelAgainstRun(selectedAlgo, params, result?.run, selectedDate, result?.trade_date),
    [params, result?.run, result?.trade_date, selectedAlgo, selectedDate],
  )

  /**
   * 导出参数：始终使用当前展示 run 的参数快照（含 limit），
   * 参数面板未提交的修改不会影响导出范围。
   */
  const exportParams = useMemo<AlgoParams>(() => {
    const snapshot = result?.run?.params
    if (!snapshot) return {}
    const converted: AlgoParams = {}
    Object.entries(snapshot).forEach(([key, value]) => {
      if (typeof value === 'number' || typeof value === 'string') converted[key] = value
      else if (typeof value === 'boolean') converted[key] = String(value)
    })
    return converted
  }, [result?.run])

  const displayedLimit = result?.run?.params?.limit

  /** 导出服务端 CSV */
  const handleExportCsv = useCallback(async () => {
    if (!selectedCode || !result) return
    setExporting(true)
    setExportError(null)
    try {
      await downloadAlgoCsv(
        selectedCode,
        exportParams,
        result?.trade_date,
        typeof displayedLimit === 'number' ? displayedLimit : null,
      )
    } catch (error) {
      setExportError(error instanceof ApiError ? `${error.code}: ${error.message}` : '导出失败，请稍后重试')
    } finally {
      setExporting(false)
    }
  }, [exportParams, result, selectedCode])


  /** Top10 排序指标对比图数据 */
  const chartData = useMemo(
    () => (result?.data ?? []).slice(0, 10).map((row) => ({ name: row.name, value: rowScore(row) })),
    [result],
  )

  const chartTitle = useMemo(() => {
    if (!result) return 'Top 10'
    if (result.data.some((row) => row.composite_score != null)) {
      return 'Top 10 综合评分（0–10）'
    }
    const labels: Record<string, string> = {
      a4: '股息率（%）',
      b1: 'ROC10（%）',
      b2: 'RSI6',
      b3: 'KDJ J 值',
      b4: '布林带宽 60 日分位（%）',
      b5: 'KDJ J 值',
    }
    return `Top 10 ${labels[result.algo] ?? '排序指标'}`
  }, [result])

  // ---- 算法清单加载 / 错误 ----
  if (algoListQuery.isLoading) {
    return (
      <div className="max-w-7xl mx-auto px-4 py-6">
        <LoadingState text="正在加载算法列表…" />
      </div>
    )
  }

  if (algoListQuery.isError) {
    return (
      <div className="max-w-7xl mx-auto px-4 py-6">
        <ErrorState title="算法列表加载失败" error={algoListQuery.error} onRetry={() => algoListQuery.refetch()} />
      </div>
    )
  }

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3 }}
      className="max-w-7xl mx-auto px-4 py-6 space-y-6"
    >
      {/* ========== 刊头 ========== */}
      <PageHeader
        eyebrow={`Strategy Lab${result?.trade_date ? ` · ${result.trade_date}` : ''}`}
        title="量化选股"
        description="本地行情库实时计算 · 参数真实生效 · 结果可复现（run_id）· 支持回测"
        actions={
          selectedCode && (
            <div className="flex min-w-0 max-w-full flex-wrap items-center gap-2">
            {/* 交易日选择：自动 = 该策略所需数据最新就绪的交易日 */}
            <label className="flex min-w-0 items-center gap-2 rounded-lg border border-border/70 bg-card/50 px-2.5 py-1.5 transition-colors hover:border-brass/40">
              <CalendarDays className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span className="eyebrow shrink-0">Date</span>
              <select
                value={selectedDate}
                onChange={(event) => handleDateChange(event.target.value)}
                className="min-w-0 max-w-[13rem] cursor-pointer bg-transparent text-[12px] font-medium text-foreground focus:outline-none"
                title="自动：选择该策略所需数据（指标/资金流/估值）最新齐全的交易日"
              >
                <option value="">自动（最近数据就绪日）</option>
                {openDays.map((day) => (
                  <option key={day} value={day}>
                    {day}
                  </option>
                ))}
              </select>
            </label>

            {/* 历史 run 切换 */}
            <label className="flex min-w-0 items-center gap-2 rounded-lg border border-border/70 bg-card/50 px-2.5 py-1.5 transition-colors hover:border-brass/40">
              <History className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span className="eyebrow shrink-0">Run</span>
              <select
                value={historyRunId ?? ''}
                onChange={(event) => setHistoryRunId(event.target.value || null)}
                className="min-w-0 max-w-[14rem] cursor-pointer bg-transparent text-[12px] font-medium text-foreground focus:outline-none"
              >
                <option value="">当前运行结果</option>
                {runOptions.map((run) => (
                  <option key={run.run_id} value={run.run_id}>
                    {shortRunId(run.run_id)} · {run.trade_date ?? '—'} · {fmtInt(run.signal_count)} 信号
                  </option>
                ))}
              </select>
            </label>

            {/* CSV 导出（服务端） */}
            <button
              onClick={handleExportCsv}
              disabled={exporting || !result}
              className="btn btn-sm h-9"
              title="导出当前展示 run 的结果 CSV（使用该 run 的参数快照与 limit，后端生成）"
            >
              <Download className="h-3.5 w-3.5" />
              {exporting ? '导出中…' : '导出 CSV'}
            </button>

            {/* 历史 run 仅取最近 10 条：显式标注总量（C1 total） */}
            {runsQuery.data && (runsQuery.data.total ?? 0) > runOptions.length && (
              <span className="basis-full text-[12px] leading-5 text-muted-foreground">
                历史 run 已显示 {runOptions.length} / 共 {runsQuery.data.total} 条（最近优先）
              </span>
            )}
            </div>
          )
        }
      />

      {exportError && <p className="text-xs text-alert">CSV 导出失败：{exportError}</p>}

      {/* ========== 策略天梯（信号级前瞻收益） ========== */}
      <StrategyLadder onSelectAlgo={handleLadderSelect} selectedCode={selectedCode} />

      {/* ========== 算法卡片 ========== */}
      <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2 lg:grid-cols-4">
        {algoList.map((algo) => (
          <AlgoCard
            key={algo.algo_code}
            algo={algo}
            signalCount={
              result && result.algo === algo.algo_code
                ? (result.total ?? result.count)
                : null
            }
            isActive={algo.algo_code === selectedCode}
            onSelect={handleSelect}
          />
        ))}
      </div>

      {/* ========== 多策略共识榜（默认折叠） ========== */}
      <ConsensusBoard onOpenAlgo={handleLadderSelect} />

      {/* ========== 参数面板 + 结果区 ========== */}
      {selectedAlgo && (
        <div id="algo-detail" className="grid grid-cols-1 lg:grid-cols-3 gap-6 scroll-mt-20">
          {/* 左：参数面板 */}
          <div className="lg:col-span-1">
            <AlgoParamPanel
              algo={selectedAlgo}
              values={params}
              onChange={handleParamChange}
              onReset={handleReset}
              onExecute={() => handleExecute(false)}
              isRunning={runMutation.isPending}
              dirty={panelDiff.dirty}
              changedKeys={panelDiff.changedKeys}
              dateMismatch={panelDiff.dateMismatch}
            />
          </div>

          {/* 右：结果 */}
          <div className="lg:col-span-2 space-y-4">
            {resultLoading && !result ? (
              <LoadingState text={historyRunId ? '正在加载历史 run 明细…' : '正在执行算法…'} />
            ) : resultError ? (
              <ErrorState
                title={historyRunId ? '历史 run 明细加载失败' : '算法执行失败'}
                error={resultError}
                onRetry={() => (historyRunId ? void historyQuery.refetch() : handleExecute(false))}
              />
            ) : !result ? (
              <EmptyState
                title="尚未执行算法"
                description="调整左侧参数后点击「执行算法」。不同参数会产生不同 run_id 与不同结果，参数修改本身不会自动请求。"
              />
            ) : (
              <>
                {/* Run 元信息 */}
                {result.run && (
                  <RunMetaPanel
                    run={result.run}
                    algo={result.algo}
                    source={resultSource}
                    dirty={panelDiff.dirty}
                    tradeDate={result.trade_date}
                    refreshing={runMutation.isPending}
                    onForceRefresh={() => handleExecute(true)}
                  />
                )}

                {/* 结果表格：动态 metrics 列 */}
                <AlgoDataTable
                  data={result.data ?? []}
                  algo={result.algo}
                  fullDesc={
                    selectedAlgo?.algo_code === result.algo ? selectedAlgo.full_desc : undefined
                  }
                  params={result.run?.params}
                  signalSchema={result.run?.signal_schema}
                  marketGate={result.market_gate}
                  total={result.total ?? result.count}
                  rangeNote={
                    typeof displayedLimit === 'number'
                      ? `本 run 按返回数量上限 limit=${displayedLimit} 保存信号；导出 CSV 使用同一 run 参数`
                      : '导出 CSV 使用当前展示 run 的参数快照'
                  }
                  onRowClick={(row) => navigate(`/stocks/${row.code}`)}
                  csvFilename={`${result.algo}_${result.trade_date}_signals.csv`}
                />

                {/* Top10 排序指标对比 */}
                <AlgoBarChart title={chartTitle} data={chartData} height={260} />
              </>
            )}
          </div>
        </div>
      )}

      {/* ========== 回测面板 ========== */}
      {selectedAlgo && (
        <BacktestPanel
          key={selectedAlgo.algo_code}
          algoCode={selectedAlgo.algo_code}
          algoName={selectedAlgo.algo_name}
          strategyParams={params}
          panelDirty={panelDiff.dirty}
          changedKeys={panelDiff.changedKeys}
          resultRunId={result?.run?.run_id ?? null}
        />
      )}

      <Disclaimer extraOnly extra="选股结果由本地行情数据按策略规则计算，仅作客观量化信号展示；参数快照与 run_id 保证结果可复现。" />
    </motion.div>
  )
}
