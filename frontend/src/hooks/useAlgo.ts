/**
 * 选股中心 Hook
 * ==============
 * - useAlgoList：算法清单（含 params_schema）；
 * - useAlgoRun：useMutation 执行算法（滑杆变化不再自动请求）；
 * - useAlgoRuns：历史 run 列表；
 * - useAlgoRunDetail：历史 run 明细。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  fetchAlgoList,
  fetchAlgoResult,
  fetchAlgoRuns,
  fetchAlgoRunDetail,
} from '../api/algo'
import type { AlgoParams } from '../types/algo'

/** 算法清单（5 分钟缓存） */
export function useAlgoList() {
  return useQuery({
    queryKey: ['algo-list'],
    queryFn: fetchAlgoList,
    staleTime: 5 * 60 * 1000,
  })
}

/** 执行算法的 mutation 入参 */
export interface AlgoRunVariables {
  algoCode: string
  params: AlgoParams
  forceRefresh?: boolean
  tradeDate?: string
}

/**
 * 执行算法（点击「执行算法」按钮时手动触发）。
 * 成功后让历史 run 列表失效，保证下拉框能立刻看到本次 run。
 */
export function useAlgoRun() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (variables: AlgoRunVariables) =>
      fetchAlgoResult(variables.algoCode, variables.params, {
        forceRefresh: variables.forceRefresh,
        tradeDate: variables.tradeDate,
      }),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['algo-runs', data.algo] })
    },
  })
}

/** 历史 run 列表（不含明细） */
export function useAlgoRuns(algoCode: string | null, limit = 10) {
  return useQuery({
    queryKey: ['algo-runs', algoCode, limit],
    queryFn: () => fetchAlgoRuns(algoCode as string, limit),
    enabled: !!algoCode,
    staleTime: 60 * 1000,
  })
}

/** 单次 run 明细 */
export function useAlgoRunDetail(runId: string | null) {
  return useQuery({
    queryKey: ['algo-run', runId],
    queryFn: () => fetchAlgoRunDetail(runId as string),
    enabled: !!runId,
    staleTime: 5 * 60 * 1000,
  })
}
