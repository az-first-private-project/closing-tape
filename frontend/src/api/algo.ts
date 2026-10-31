/**
 * 选股 API（契约第 5 节）
 */
import { downloadCsv, extractList, request } from './client'
import type {
  AlgoMeta,
  AlgoParams,
  AlgoResult,
  AlgoRunDetail,
  AlgoRunSummary,
  ConsensusResponse,
  LadderResponse,
  WatchHitsResponse,
} from '../types/algo'

/** 算法清单（5.1） */
export function fetchAlgoList(): Promise<AlgoMeta[]> {
  return request<AlgoMeta[]>('/algo/list')
}

/**
 * 执行算法（5.2）
 * @param params 策略参数（来自 params_schema），会平铺进 query string
 * @param options.tradeDate 指定交易日
 * @param options.forceRefresh 忽略缓存强制重算
 */
export function fetchAlgoResult(
  algoCode: string,
  params: AlgoParams = {},
  options: { tradeDate?: string; forceRefresh?: boolean } = {},
): Promise<AlgoResult> {
  return request<AlgoResult>(`/algo/${encodeURIComponent(algoCode)}`, {
    query: {
      ...params,
      trade_date: options.tradeDate || undefined,
      // 只有显式强制刷新才传，避免 false 也进 query 影响缓存键
      force_refresh: options.forceRefresh ? true : undefined,
    },
  })
}

/** 忽略缓存重跑（5.3 POST /algo/{algo_code}/refresh） */
export function refreshAlgoResult(
  algoCode: string,
  params: AlgoParams = {},
  tradeDate?: string,
): Promise<AlgoResult> {
  return request<AlgoResult>(`/algo/${encodeURIComponent(algoCode)}/refresh`, {
    method: 'POST',
    query: { ...params, trade_date: tradeDate || undefined },
  })
}

/**
 * 历史 run 列表（5.3，不含明细）。
 * 契约未固定包裹形状，兼容裸数组与 {items:[]}/{runs:[]}。
 */
export async function fetchAlgoRuns(algoCode: string, limit = 10): Promise<AlgoRunSummary[]> {
  const payload = await request<unknown>(`/algo/${encodeURIComponent(algoCode)}/runs`, {
    query: { limit },
  })
  return extractList<AlgoRunSummary>(payload)
}

/**
 * 单次 run 明细（5.3）。
 * 契约未定义响应结构，后端按与 5.2 相同的结构返回（已在交付说明中标注）。
 */
export function fetchAlgoRunDetail(runId: string): Promise<AlgoRunDetail> {
  return request<AlgoRunDetail>(`/algo/runs/${encodeURIComponent(runId)}`)
}

/** 导出选股结果 CSV（5.3；后端直接返回 text/csv，文件名如 b1_2026-10-21.csv） */
export function downloadAlgoCsv(
  algoCode: string,
  params: AlgoParams = {},
  tradeDate?: string,
): Promise<void> {
  return downloadCsv(
    `/algo/${encodeURIComponent(algoCode)}/export.csv`,
    { ...params, trade_date: tradeDate || undefined },
    `${algoCode}_export.csv`,
  )
}

/**
 * 策略天梯（5.4）
 * @param window 回看交易日数：20/60/120
 * @param horizon 收益口径：5/10/20
 */
export function fetchLadder(
  window: number,
  horizon: number,
  tradeDate?: string,
): Promise<LadderResponse> {
  return request<LadderResponse>('/algo/ladder', {
    query: { window, horizon, trade_date: tradeDate || undefined },
  })
}

/**
 * 多策略共识榜（5.5）
 * @param minCount 最少命中策略数（后端 2–7）
 */
export function fetchConsensus(
  minCount: number,
  tradeDate?: string,
  limit = 100,
): Promise<ConsensusResponse> {
  return request<ConsensusResponse>('/algo/consensus', {
    query: { min_count: minCount, limit, trade_date: tradeDate || undefined },
  })
}

/**
 * 自选股命中（5.6）
 * 只读查询，POST 仅因为 codes 列表可能很长；重试沿用 client.ts 的 GET 语义
 * （5xx / 网络错误最多 1 次，4xx 不重试）。
 */
export function fetchWatchHits(codes: string[], days = 10): Promise<WatchHitsResponse> {
  return request<WatchHitsResponse>('/algo/hits', {
    method: 'POST',
    body: { codes, days },
    retry: 1,
  })
}
