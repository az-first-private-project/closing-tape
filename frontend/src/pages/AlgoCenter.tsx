/**
 * 选股中心（契约第 5、6 节）
 * ===========================
 * - 算法卡片（/algo/list，参数 schema 驱动，A3/A4 + B1–B5）；
 * - 参数面板修改不自动请求，「执行算法」按钮触发 useMutation；
 * - 结果区：Run 元信息（run_id/耗时/universe/signal/data_version/参数快照/from_cache）、
 *   动态列结果表格、Top10 评分图、CSV 导出、历史 run 切换；
 * - 回测面板：POST /backtest/{algo_code}，绩效指标 + 净值曲线 + 成交明细 + 导出。
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { CalendarDays, Download, History } from 'lucide-react'
import { useAlgoList, useAlgoRun, useAlgoRuns, useAlgoRunDetail } from '../hooks/useAlgo'
import { useTradeCalendar } from '../hooks/useDashboard'
import { downloadAlgoCsv } from '../api/algo'
import { ApiError } from '../api/client'
import type { AlgoMeta, AlgoParams, AlgoResult, AlgoStockRow } from '../types/algo'
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
import type { DataTableSortState } from '../components/DataTable'
import { diffPanelAgainstRun, paramsFromSnapshot } from '../lib/algoParams'
import { loadSelectedAlgo, saveSelectedAlgo } from '../lib/backtestSession'
import {
  buildAlgoUrlSearch,
  loadAlgoContext,
  loadAlgoTableState,
  readAlgoUrlState,
  saveAlgoContext,
  saveAlgoTableState,
  type StoredAlgoTableState,
} from '../lib/algoSession'
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

/** 表格状态的默认值（无历史记录时） */
const DEFAULT_TABLE_STATE: Omit<StoredAlgoTableState, 'savedAt'> = {
  sort: { key: 'rank', direction: 'asc' },
  expandedKey: null,
  activeKey: null,
  scrollTop: 0,
  pageScrollY: 0,
}

/** 启动状态：URL 优先，缺失项回落到会话缓存 */
interface BootState {
  /** URL 中的 ?algo=（用于校验策略是否仍存在） */
  urlAlgo: string | null
  algoCode: string
  /** null = URL 与会话都没有参数，等 run 快照或 schema 默认值 */
  params: AlgoParams | null
  selectedDate: string
  runId: string | null
  tableState: StoredAlgoTableState | null
}

function resolveBootState(locationSearch: string): BootState {
  const url = readAlgoUrlState(new URLSearchParams(locationSearch))
  const lastAlgo = loadSelectedAlgo() ?? ''
  const algoCode = url.algoCode ?? lastAlgo
  const stored = algoCode ? loadAlgoContext(algoCode) : null
  const runId = url.runId ?? stored?.runId ?? null
  return {
    urlAlgo: url.algoCode,
    algoCode,
    params: url.params ?? stored?.params ?? null,
    selectedDate: url.selectedDate !== null ? url.selectedDate : (stored?.selectedDate ?? ''),
    runId,
    tableState: runId ? loadAlgoTableState(runId) : null,
  }
}

export default function AlgoCenter() {
  const navigate = useNavigate()
  const location = useLocation()

  // ---- 算法清单 ----
  const algoListQuery = useAlgoList()
  const algoList = algoListQuery.data ?? []

  // ---- 页面状态 ----
  /** 启动时一次性解析 URL / 会话：URL 优先，缺失项回落到持久化缓存 */
  const bootRef = useRef<BootState | null>(null)
  if (bootRef.current === null) bootRef.current = resolveBootState(location.search)
  const boot = bootRef.current

  /** 本地记住当前策略：离开页面再回来仍能看到该策略的后台回测 */
  const [selectedCode, setSelectedCode] = useState(boot.algoCode)
  const [params, setParams] = useState<AlgoParams>(boot.params ?? {})
  /** 已初始化过参数的策略，避免列表刷新覆盖用户改过的参数 */
  const seededParamsFor = useRef<string | null>(boot.params ? boot.algoCode : null)
  /** 启动自 run 但 URL/会话都没有参数：等 run 明细返回后用快照回填 */
  const snapshotSeedPending = useRef(!!boot.runId && !boot.params)
  /** 空字符串 = 自动（该策略最近数据就绪日）；否则为指定交易日 */
  const [selectedDate, setSelectedDate] = useState(boot.selectedDate)
  /** 非空表示正在查看某次 run 的明细（含启动时从 URL/会话恢复的 run） */
  const [historyRunId, setHistoryRunId] = useState<string | null>(boot.runId)
  /** 启动恢复的 run：用于来源标记与 404 清理 */
  const [restoredRunId, setRestoredRunId] = useState<string | null>(boot.runId)
  /** 恢复的 run 已不存在时的说明 */
  const [restoreNotice, setRestoreNotice] = useState<string | null>(null)
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

  // 列表加载后：校验启动时恢复的策略；失效时回落到 URL 指定或第一个启用中的算法
  useEffect(() => {
    if (algoList.length === 0) return
    const current = algoList.find((algo) => algo.algo_code === selectedCode) ?? null
    if (current) {
      // 只补一次 schema 默认值；run 快照稍后到达时会覆盖
      if (seededParamsFor.current !== selectedCode) {
        seededParamsFor.current = selectedCode
        setParams(buildDefaultParams(current))
      }
      return
    }
    const requested = boot.urlAlgo
      ? (algoList.find((algo) => algo.algo_code === boot.urlAlgo) ?? null)
      : null
    const fallback = requested ?? algoList.find((algo) => algo.is_active !== false) ?? algoList[0]
    seededParamsFor.current = fallback.algo_code
    setSelectedCode(fallback.algo_code)
    setParams(buildDefaultParams(fallback))
    setHistoryRunId(null)
    setRestoredRunId(null)
    setExportError(null)
  }, [algoList, selectedCode, boot.urlAlgo])

  // 记住当前策略：导航离开/返回、F5 后仍停在离开前的策略上
  useEffect(() => {
    if (selectedCode) saveSelectedAlgo(selectedCode)
  }, [selectedCode])

  /** 启动恢复的 run：策略/参数冲突时 run 优先（URL/algo 与 run 不一致） */
  const bootAligned = useRef(false)
  useEffect(() => {
    const data = historyQuery.data
    if (!data || !historyRunId || bootAligned.current) return
    if (historyRunId !== boot.runId || algoList.length === 0) return
    bootAligned.current = true
    if (data.algo !== selectedCode) {
      if (!algoList.some((algo) => algo.algo_code === data.algo)) return
      seededParamsFor.current = data.algo
      snapshotSeedPending.current = false
      setSelectedCode(data.algo)
      setParams(paramsFromSnapshot(data.run.params))
      return
    }
    if (snapshotSeedPending.current) {
      seededParamsFor.current = selectedCode
      snapshotSeedPending.current = false
      setParams(paramsFromSnapshot(data.run.params))
    }
  }, [historyQuery.data, historyRunId, boot.runId, selectedCode, algoList])

  /** 恢复的 run 已不存在：清掉失效恢复点，回到可重新执行的空态 */
  useEffect(() => {
    if (!historyRunId || !historyQuery.isError || historyRunId !== restoredRunId) return
    const error = historyQuery.error
    if (!(error instanceof ApiError) || error.status !== 404) return
    setHistoryRunId(null)
    setRestoredRunId(null)
    setRestoreNotice(`上次恢复的 run ${shortRunId(historyRunId)} 已不存在，请重新执行算法。`)
    if (selectedCode) {
      const context = loadAlgoContext(selectedCode)
      if (context) saveAlgoContext({ ...context, runId: null })
    }
  }, [historyQuery.isError, historyQuery.error, historyRunId, restoredRunId, selectedCode])

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
      seededParamsFor.current = code
      snapshotSeedPending.current = false
      setSelectedCode(code)
      setParams(buildDefaultParams(algo))
      setHistoryRunId(null)
      setRestoredRunId(null)
      setRestoreNotice(null)
      setExportError(null)
      runMutation.reset()
    },
    [algoList, runMutation],
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
      setRestoredRunId(null)
      setRestoreNotice(null)
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
      setRestoredRunId(null)
      setRestoreNotice(null)
      setExportError(null)
      runMutation.reset()
    },
    [runMutation],
  )

  /** 手动切换历史 run：不改参数面板，只切换展示的 run 快照 */
  const handleHistoryChange = useCallback((value: string) => {
    setHistoryRunId(value || null)
    setRestoredRunId(null)
    setRestoreNotice(null)
    setExportError(null)
  }, [])

  // ---- 结果来源：历史 run 明细 或 本次执行结果 ----
  const result: AlgoResult | null = historyRunId ? (historyQuery.data ?? null) : (runMutation.data ?? null)
  const resultLoading = historyRunId ? historyQuery.isLoading : runMutation.isPending
  const resultError = historyRunId ? historyQuery.error : runMutation.error
  const runOptions = runsQuery.data?.items ?? []
  const displayedRunId = result?.run?.run_id ?? null
  /** 结果来源：本次执行 / 缓存结果 / 历史 run / 会话恢复 */
  const resultSource: RunSource = historyRunId
    ? restoredRunId === historyRunId
      ? 'restored'
      : 'history'
    : result?.from_cache
      ? 'cache'
      : 'live'
  /** run 明细尚未就绪（加载/失败）时不改写 URL 与上下文，避免丢掉恢复点 */
  const runStateSettled = !historyRunId || historyQuery.isSuccess
  /** 参数面板与当前展示 run 的差异（不平移结果，只提示） */
  const panelDiff = useMemo(
    () => diffPanelAgainstRun(selectedAlgo, params, result?.run, selectedDate, result?.trade_date),
    [params, result?.run, result?.trade_date, selectedAlgo, selectedDate],
  )

  /**
   * 导出参数：始终使用当前展示 run 的参数快照（含 limit），
   * 参数面板未提交的修改不会影响导出范围。
   */
  const exportParams = useMemo<AlgoParams>(
    () => paramsFromSnapshot(result?.run?.params),
    [result?.run],
  )

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

  /**
   * URL 同步：algo / date / run / p.* 全量写入当前历史条目（replace），
   * 刷新与返回时优先按 URL 还原。直接改地址栏而不触发路由重渲染，避免循环。
   */
  const syncUrl = useCallback(() => {
    if (!selectedCode || !selectedAlgo || !runStateSettled) return
    const next = buildAlgoUrlSearch({
      algoCode: selectedCode,
      selectedDate,
      runId: displayedRunId,
      params,
      algo: selectedAlgo,
    })
    const query = next.toString()
    const pathname = window.location.pathname
    const hash = window.location.hash
    const target = `${pathname}${query ? `?${query}` : ''}${hash}`
    const current = `${pathname}${window.location.search}${hash}`
    if (target !== current) window.history.replaceState(window.history.state, '', target)
  }, [displayedRunId, params, runStateSettled, selectedAlgo, selectedCode, selectedDate])

  useEffect(() => {
    syncUrl()
  }, [syncUrl])

  /** 会话上下文（按策略）：参数、交易日、当前展示的 run */
  useEffect(() => {
    if (!selectedCode || !selectedAlgo || !runStateSettled) return
    saveAlgoContext({
      algoCode: selectedCode,
      params,
      selectedDate,
      runId: displayedRunId,
    })
  }, [displayedRunId, params, runStateSettled, selectedAlgo, selectedCode, selectedDate])

  /** 表格状态（按 run）：排序 / 展开 / 选中行 / 内部滚动 / 页面滚动 */
  const tableInitial = useMemo(
    () => (displayedRunId ? loadAlgoTableState(displayedRunId) : null),
    [displayedRunId],
  )
  const tableStateRef = useRef<StoredAlgoTableState | null>(null)
  const tableRunIdRef = useRef<string | null>(null)
  const tableSaveTimer = useRef<number | null>(null)
  const displayedRunIdRef = useRef<string | null>(null)
  displayedRunIdRef.current = displayedRunId

  useEffect(() => {
    if (tableRunIdRef.current === displayedRunId) return
    tableRunIdRef.current = displayedRunId
    tableStateRef.current = tableInitial
  }, [displayedRunId, tableInitial])

  const tableBaseFor = useCallback((runId: string): StoredAlgoTableState => {
    if (tableStateRef.current && tableRunIdRef.current === runId) return tableStateRef.current
    return loadAlgoTableState(runId) ?? { ...DEFAULT_TABLE_STATE, savedAt: 0 }
  }, [])

  const writeTableState = useCallback(
    (patch: Partial<StoredAlgoTableState>) => {
      const runId = displayedRunIdRef.current
      if (!runId) return
      tableRunIdRef.current = runId
      tableStateRef.current = { ...tableBaseFor(runId), ...patch, savedAt: Date.now() }
      if (tableSaveTimer.current !== null) window.clearTimeout(tableSaveTimer.current)
      tableSaveTimer.current = window.setTimeout(() => {
        tableSaveTimer.current = null
        const currentRunId = displayedRunIdRef.current
        if (currentRunId && tableStateRef.current) {
          saveAlgoTableState(currentRunId, tableStateRef.current)
        }
      }, 200)
    },
    [tableBaseFor],
  )

  const flushTableState = useCallback(
    (patch: Partial<StoredAlgoTableState> = {}) => {
      if (tableSaveTimer.current !== null) {
        window.clearTimeout(tableSaveTimer.current)
        tableSaveTimer.current = null
      }
      const runId = displayedRunIdRef.current
      if (!runId) return
      tableRunIdRef.current = runId
      tableStateRef.current = { ...tableBaseFor(runId), ...patch, savedAt: Date.now() }
      saveAlgoTableState(runId, tableStateRef.current)
    },
    [tableBaseFor],
  )

  useEffect(
    () => () => {
      if (tableSaveTimer.current !== null) window.clearTimeout(tableSaveTimer.current)
    },
    [],
  )

  /** 跟踪页面滚动（同步更新 ref，落盘由 writeTableState 防抖） */
  useEffect(() => {
    let lastY = window.scrollY
    const onScroll = () => {
      const current = window.scrollY
      if (current === lastY) return
      lastY = current
      writeTableState({ pageScrollY: current })
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [writeTableState])

  /** 返回时恢复页面滚动：等结果渲染后执行一次，跳过全局 smooth 动画 */
  const pageScrollRestored = useRef(false)
  useLayoutEffect(() => {
    if (pageScrollRestored.current || !result) return
    pageScrollRestored.current = true
    if (boot.runId && result.run?.run_id === boot.runId) {
      const target = boot.tableState?.pageScrollY ?? 0
      if (target > 0) window.scrollTo({ top: target, behavior: 'instant' as ScrollBehavior })
    }
  }, [result, boot.runId, boot.tableState])

  const handleTableSortChange = useCallback(
    (sort: DataTableSortState | null) => {
      writeTableState({ sort })
    },
    [writeTableState],
  )

  const handleTableExpandedChange = useCallback(
    (expandedKey: string | null) => {
      writeTableState({ expandedKey })
    },
    [writeTableState],
  )

  const handleTableScrollTop = useCallback(
    (scrollTop: number) => {
      writeTableState({ scrollTop })
    },
    [writeTableState],
  )

  /** 结果行 → 个股详情：先落表格/滚动状态，再 SPA 跳转 */
  const handleRowClick = useCallback(
    (row: AlgoStockRow, rowKey: string) => {
      flushTableState({ activeKey: rowKey, pageScrollY: window.scrollY })
      syncUrl()
      navigate(`/stocks/${row.code}`, { state: { fromAlgoCenter: true } })
    },
    [flushTableState, navigate, syncUrl],
  )

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
                onChange={(event) => handleHistoryChange(event.target.value)}
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
                title={restoreNotice ? '上次的结果已失效' : '尚未执行算法'}
                description={
                  restoreNotice ??
                  '调整左侧参数后点击「执行算法」。不同参数会产生不同 run_id 与不同结果，参数修改本身不会自动请求。'
                }
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
                  key={result.run?.run_id ?? `${result.algo}_${result.trade_date}`}
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
                  initialSort={tableInitial ? tableInitial.sort : undefined}
                  initialExpandedKey={tableInitial?.expandedKey ?? null}
                  initialActiveKey={tableInitial?.activeKey ?? null}
                  initialScrollTop={tableInitial?.scrollTop ?? 0}
                  onSortChange={handleTableSortChange}
                  onExpandedChange={handleTableExpandedChange}
                  onScrollTopChange={handleTableScrollTop}
                  onRowClick={handleRowClick}
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
