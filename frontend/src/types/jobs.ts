/**
 * 任务/采集相关类型 —— 严格对齐 docs/V5-API-CONTRACT.md 第 1 节
 */

/** 任务状态（契约仅示例 success，其余为枚举推断） */
export type JobStatus = 'success' | 'failed' | 'running' | 'pending' | 'skipped' | string

/** 采集/策略任务（契约 1 · jobs 单项） */
export interface Job {
  job_id: string
  task: string
  dataset: string | null
  trade_date: string | null
  status: JobStatus
  total: number | null
  done: number | null
  failed: number | null
  rows_written: number | null
  started_at: string | null
  finished_at: string | null
  elapsed_ms: number | null
  error: string | null
}

/** GET /api/v1/meta/jobs 查询参数（C1：仅追加 offset，不分页参数改名） */
export interface JobQuery {
  limit?: number
  offset?: number
  task?: string
  status?: string
}
