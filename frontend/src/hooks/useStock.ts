/**
 * 个股 Hook（契约第 4 节）
 * =========================
 * useStock / useKline / useStockSignals（最近一次 run 中该股是否被选中）。
 */
import { useQueries, useQuery } from '@tanstack/react-query'
import { fetchKline, fetchSparklineBatch, fetchStock } from '../api/stock'
import { fetchAlgoRuns, fetchAlgoRunDetail } from '../api/algo'
import { useAlgoList } from './useAlgo'
import type { AlgoStockRow } from '../types/algo'

/** 个股概览（tradeDate 为空 = 最新交易日） */
export function useStock(code: string, tradeDate?: string) {
  return useQuery({
    queryKey: ['stock', code, tradeDate || 'latest'],
    queryFn: () => fetchStock(code, tradeDate),
    enabled: !!code,
    staleTime: 5 * 60 * 1000,
    placeholderData: (previousData) => previousData,
  })
}

/** 个股 K 线（默认截至最新交易日的 250 个交易日） */
export function useKline(code: string, limit = 250, end?: string) {
  return useQuery({
    queryKey: ['kline', code, limit, end || 'latest'],
    queryFn: () => fetchKline(code, { limit, end }),
    enabled: !!code,
    staleTime: 5 * 60 * 1000,
    placeholderData: (previousData) => previousData,
  })
}

/** 批量近 N 日收盘（P2-5）：整页 sparkline 一次请求，按代码集合缓存 5 分钟 */
export function useSparklineBatch(
  codes: string[],
  days = 30,
  end?: string,
  enabled = true,
) {
  const unique = [...new Set(codes.filter(Boolean))].sort()
  return useQuery({
    queryKey: ['sparkline-batch', unique, days, end || 'latest'],
    queryFn: () => fetchSparklineBatch(unique, { days, end }),
    enabled: enabled && unique.length > 0,
    staleTime: 5 * 60 * 1000,
    placeholderData: (previousData) => previousData,
  })
}

/** 该股在某个算法最近一次 run 中的信号 */
export interface StockSignal {
  algoCode: string
  algoName: string
  runId: string
  tradeDate: string | null
  row: AlgoStockRow
}

/**
 * 最近信号：对每个启用中的算法取最近 1 次 run 明细，筛选包含该代码的行。
 * 契约未提供「按股票查信号」接口，这里复用 /algo/{code}/runs + /algo/runs/{run_id}。
 */
export function useStockSignals(code: string) {
  const { data: algos } = useAlgoList()
  const activeAlgos = (algos ?? []).filter((algo) => algo.is_active !== false)

  const latestRunQueries = useQueries({
    queries: activeAlgos.map((algo) => ({
      queryKey: ['algo-runs', algo.algo_code, 1],
      queryFn: () => fetchAlgoRuns(algo.algo_code, 1),
      enabled: !!code,
      staleTime: 5 * 60 * 1000,
    })),
  })

  const detailQueries = useQueries({
    queries: latestRunQueries.map((runQuery) => {
      const summary = runQuery.data?.[0]
      return {
        queryKey: ['algo-run', summary?.run_id ?? null],
        queryFn: () => fetchAlgoRunDetail(summary?.run_id as string),
        enabled: !!summary?.run_id,
        staleTime: 5 * 60 * 1000,
      }
    }),
  })

  const signals: StockSignal[] = []
  detailQueries.forEach((detailQuery, index) => {
    const algo = activeAlgos[index]
    const summary = latestRunQueries[index]?.data?.[0]
    const detail = detailQuery.data
    if (!algo || !summary || !detail || !Array.isArray(detail.data)) return
    const row = detail.data.find((item) => item.code === code)
    if (!row) return
    signals.push({
      algoCode: algo.algo_code,
      algoName: detail.algo_name || algo.algo_name,
      runId: summary.run_id,
      tradeDate: summary.trade_date ?? detail.trade_date ?? null,
      row,
    })
  })

  return {
    signals,
    isLoading:
      latestRunQueries.some((query) => query.isLoading) || detailQueries.some((query) => query.isLoading),
  }
}
