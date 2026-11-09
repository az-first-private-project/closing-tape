/**
 * 回测 Hook（契约第 6 节）
 */
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError } from '../api/client'
import {
  createBacktestGrid,
  fetchBacktest,
  fetchBacktestGrid,
  fetchBacktestStatus,
  fetchBacktestTrades,
  retryBacktestGrid,
  runBacktest,
} from '../api/backtest'
import {
  clearActiveBacktest,
  getActiveBacktest,
  getBacktestSessionRevision,
  loadGridState,
  saveActiveBacktest,
  saveGridState,
  subscribeBacktestSession,
  type StoredBacktestTask,
} from '../lib/backtestSession'
import type { BacktestGridRequest, BacktestOptions, BacktestResult } from '../types/backtest'

export interface BacktestVariables {
  algoCode: string
  options: BacktestOptions
}

export interface BacktestGridVariables {
  algoCode: string
  request: BacktestGridRequest
}

/** 轮询间隔与前端等待上限；后端任务不受 30s HTTP 超时约束 */
const POLL_INTERVAL_MS = 1500
const POLL_TIMEOUT_MS = 10 * 60 * 1000

export interface BacktestController {
  mutate: (variables: BacktestVariables) => void
  /** 成功后由后端按 bt_id 恢复的完整结果 */
  data: BacktestResult | null
  /** 提交中或后台任务尚未终态 */
  isPending: boolean
  /** 按钮禁用：运行中/提交中/结果加载中 */
  isBusy: boolean
  /** 视图加载中：提交中/状态首载/成功后结果加载 */
  isLoading: boolean
  isError: boolean
  error: unknown | null
  /** 仅瞬时错误与超时提供就地重试；业务失败交给面板重新发起 */
  retry: (() => void) | null
  submitting: boolean
  status: string | null
  startedAt: number | null
  /** 当前 algo 的持久化任务引用（含 POST 晚到时补全的输入） */
  task: StoredBacktestTask | null
}

/**
 * 单次回测：POST 只负责创建任务，状态与结果由全局 Query 按 bt_id 订阅。
 * bt_id 写入 sessionStorage，路由卸载/刷新后可重新接上同一任务。
 */
export function useBacktest(algoCode: string): BacktestController {
  const revision = useSyncExternalStore(
    subscribeBacktestSession,
    getBacktestSessionRevision,
    () => 0,
  )
  const task = useMemo(() => getActiveBacktest(algoCode), [algoCode, revision])
  const btId = task?.btId ?? null

  const submit = useMutation({
    mutationFn: async (variables: BacktestVariables) => {
      const job = await runBacktest(variables.algoCode, variables.options)
      // 写在 mutationFn 内：组件卸载后 POST 仍会完成，任务引用不能丢
      saveActiveBacktest({
        algoCode: variables.algoCode,
        btId: job.bt_id,
        startedAt: Date.now(),
        start: variables.options.start,
        rebalanceDays: variables.options.rebalance_days,
        maxPositions: variables.options.max_positions,
      })
      return job
    },
  })

  const [watchNonce, setWatchNonce] = useState(0)
  const [timedOut, setTimedOut] = useState(false)

  const statusQuery = useQuery({
    queryKey: ['backtest-status', btId],
    queryFn: () => fetchBacktestStatus(btId as string),
    enabled: !!btId,
    // 状态不是可长期缓存的数据：重新进入页面必须向后端确认
    staleTime: 0,
    refetchInterval: (query) => {
      if (timedOut || query.state.error) return false
      const status = query.state.data?.status
      if (status === 'success' || status === 'failed') return false
      return POLL_INTERVAL_MS
    },
    // 与旧版后台循环一致：切到其他标签页也继续观察，回来即是最新状态
    refetchIntervalInBackground: true,
  })

  const status = statusQuery.data?.status ?? null
  const terminal = status === 'success' || status === 'failed'

  // 任务切换或点「继续等待」后，重新开始一个观察窗口
  useEffect(() => {
    setTimedOut(false)
  }, [btId, watchNonce])

  useEffect(() => {
    if (!btId || terminal || timedOut) return
    const timer = window.setTimeout(() => setTimedOut(true), POLL_TIMEOUT_MS)
    return () => window.clearTimeout(timer)
  }, [btId, terminal, timedOut, watchNonce])

  // 任务已被清理/过期：去掉本地引用，让面板回到可运行状态
  useEffect(() => {
    const error = statusQuery.error
    if (btId && error instanceof ApiError && error.status === 404) {
      clearActiveBacktest(algoCode, btId)
    }
  }, [algoCode, btId, statusQuery.error])

  const refetchStatus = statusQuery.refetch
  const resume = useCallback(() => {
    setTimedOut(false)
    setWatchNonce((value) => value + 1)
    void refetchStatus()
  }, [refetchStatus])

  const resultQuery = useBacktestDetail(status === 'success' ? btId : null)
  const result = status === 'success' ? (resultQuery.data ?? null) : null

  const notFound = statusQuery.error instanceof ApiError && statusQuery.error.status === 404
  const statusError = notFound ? null : statusQuery.error
  const failedMessage = statusQuery.data?.error

  const failedError = useMemo(() => {
    if (status !== 'failed') return null
    return new ApiError({
      code: 'BACKTEST_FAILED',
      status: 200,
      message: failedMessage || '回测执行失败',
    })
  }, [status, failedMessage])

  const timeoutError = useMemo(() => {
    if (!timedOut || terminal) return null
    return new ApiError({
      code: 'BACKTEST_TIMEOUT',
      status: 0,
      message: '前端等待回测超时（10 分钟），后台任务可能仍在运行',
    })
  }, [timedOut, terminal])

  const error = submit.error ?? timeoutError ?? failedError ?? statusError ?? resultQuery.error ?? null

  const retry = timeoutError
    ? resume
    : statusError || resultQuery.error
      ? () => {
          if (statusError) void statusQuery.refetch()
          if (resultQuery.error) void resultQuery.refetch()
        }
      : null

  const running = submit.isPending || (!!btId && !terminal)
  const isLoading =
    submit.isPending ||
    (!!btId && statusQuery.isPending) ||
    (status === 'success' && resultQuery.isPending)

  return {
    mutate: (variables) => submit.mutate(variables),
    data: result,
    isPending: running,
    isBusy: running || isLoading,
    isLoading,
    isError: error !== null,
    error,
    retry,
    submitting: submit.isPending,
    status,
    startedAt: task?.startedAt ?? null,
    task,
  }
}

/** 回测成交明细（6.3）；btId 为空时不发请求 */
export function useBacktestTrades(btId: string, code?: string) {
  return useQuery({
    queryKey: ['backtest-trades', btId, code || 'all'],
    queryFn: () => fetchBacktestTrades(btId, code),
    enabled: !!btId,
    staleTime: 5 * 60 * 1000,
  })
}

/** 已落库回测的完整结果（矩阵格点击复用 T9 面板） */
export function useBacktestDetail(btId: string | null) {
  return useQuery({
    queryKey: ['backtest', btId],
    queryFn: () => fetchBacktest(btId as string),
    enabled: !!btId,
    staleTime: 5 * 60 * 1000,
  })
}

/** 建立参数敏感性矩阵（T10）：用户显式触发 */
export function useBacktestGrid() {
  return useMutation({
    mutationFn: async (variables: BacktestGridVariables) => {
      const data = await createBacktestGrid(variables.algoCode, variables.request)
      // 与单回测同理：POST 返回即落存储，避免创建后离开页面丢失 grid_id
      const previous = loadGridState(variables.algoCode)
      saveGridState(variables.algoCode, {
        gridId: data.grid_id,
        start: variables.request.start_date,
        endOverride: previous?.endOverride ?? '',
        rebalanceDays: variables.request.rebalance_days,
        maxPositions: variables.request.max_positions,
        metric: previous?.metric ?? 'excess',
        selectedBtId: null,
      })
      return data
    },
  })
}

/** 重试矩阵失败格（P1-4）：成功格复用，仅 failed 重新入队；完成后立即刷新矩阵 */
export function useBacktestGridRetry(gridId: string | null) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => retryBacktestGrid(gridId as string),
    onSuccess: () => {
      if (gridId) {
        void queryClient.invalidateQueries({ queryKey: ['backtest-grid', gridId] })
      }
    },
  })
}

/** 矩阵轮询（T10）：3s 一次，页面不可见、标签页隐藏或全部完成时自动停止 */
export function useBacktestGridDetail(gridId: string | null, active = true) {
  return useQuery({
    queryKey: ['backtest-grid', gridId],
    queryFn: () => fetchBacktestGrid(gridId as string),
    enabled: !!gridId && active,
    // 返回页面时必须先向后台确认进度，不能沿用离开前的缓存
    staleTime: 0,
    refetchInterval: (query) => {
      const data = query.state.data
      if (data && data.progress.done >= data.progress.total) return false
      return 3000
    },
    refetchIntervalInBackground: false,
  })
}
