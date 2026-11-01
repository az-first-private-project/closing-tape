/**
 * 任务监控页（契约第 1 节）
 * ==========================
 * - 顶部：数据集新鲜度总览（每张数据集状态 / 最后成功时间 / 覆盖率 / 异常数）；
 * - 任务表：task / dataset / status / 耗时 / 行数 / 失败数 / 错误摘要；
 * - 30 秒自动刷新，可暂停。
 */
import { useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { Pause, Play, RefreshCw } from 'lucide-react'
import { useFreshness } from '../hooks/useFreshness'
import { useJobs } from '../hooks/useJobs'
import FreshnessBadge from '../components/FreshnessBadge'
import DataTable, { type DataTableColumn } from '../components/DataTable'
import {
  EmptyState,
  ErrorState,
  SkeletonKpiRow,
  SkeletonTable,
} from '../components/StateViews'
import { useDelayedFlag } from '../hooks/useDelayedFlag'
import type { Job } from '../types/jobs'
import { fmtDateTime, fmtDuration, fmtInt } from '../lib/format'

/** 任务状态 → 徽标样式 */
function statusBadgeClass(status: string): string {
  switch (status) {
    case 'success':
      return 'border-success/40 bg-success/10 text-success'
    case 'failed':
      return 'border-alert/40 bg-alert/10 text-alert'
    case 'running':
      return 'border-primary/40 bg-primary/10 text-primary'
    case 'pending':
      return 'border-warning/40 bg-warning/10 text-warning'
    default:
      return 'border-border bg-muted text-muted-foreground'
  }
}

/** 任务状态 → 中文 */
function statusLabel(status: string): string {
  const labelMap: Record<string, string> = {
    success: '成功',
    failed: '失败',
    running: '运行中',
    pending: '等待中',
    skipped: '跳过',
  }
  return labelMap[status] ?? status
}

/** 任务表列定义 */
const JOB_COLUMNS: DataTableColumn<Job>[] = [
  { key: 'task', header: '任务', sortable: true, value: (row) => row.task, render: (row) => <span className="font-medium">{row.task}</span> },
  {
    key: 'dataset',
    header: '数据集',
    sortable: true,
    value: (row) => row.dataset,
    render: (row) => <span className="font-mono text-[11px] text-muted-foreground">{row.dataset ?? '—'}</span>,
  },
  {
    key: 'status',
    header: '状态',
    sortable: true,
    align: 'center',
    value: (row) => row.status,
    render: (row) => (
      <span className={`inline-block text-[11px] px-2 py-0.5 rounded-full border ${statusBadgeClass(row.status)}`}>
        {statusLabel(row.status)}
      </span>
    ),
  },
  {
    key: 'trade_date',
    header: '交易日',
    sortable: true,
    value: (row) => row.trade_date,
    render: (row) => row.trade_date ?? '—',
  },
  {
    key: 'elapsed_ms',
    header: '耗时',
    align: 'right',
    sortable: true,
    value: (row) => row.elapsed_ms,
    render: (row) => fmtDuration(row.elapsed_ms),
  },
  {
    key: 'rows_written',
    header: '行数',
    align: 'right',
    sortable: true,
    value: (row) => row.rows_written,
    render: (row) => fmtInt(row.rows_written),
  },
  {
    key: 'failed',
    header: '失败数',
    align: 'right',
    sortable: true,
    value: (row) => row.failed,
    render: (row) =>
      row.failed !== null && row.failed > 0 ? (
        <span className="text-alert font-medium num">{fmtInt(row.failed)}</span>
      ) : (
        fmtInt(row.failed)
      ),
  },
  {
    key: 'error',
    header: '错误摘要',
    value: (row) => row.error,
    render: (row) => (
      <span className="text-alert line-clamp-1 break-all" title={row.error ?? undefined}>
        {row.error ?? '—'}
      </span>
    ),
  },
  {
    key: 'started_at',
    header: '开始时间',
    sortable: true,
    value: (row) => row.started_at,
    render: (row) => <span className="num">{fmtDateTime(row.started_at)}</span>,
  },
]

export default function JobsPage() {
  const [paused, setPaused] = useState(false)
  const [statusFilter, setStatusFilter] = useState('')

  const freshnessQuery = useFreshness()
  const jobsQuery = useJobs(
    {
      limit: 50,
      status: statusFilter || undefined,
    },
    paused,
  )
  const showFreshnessSkeleton = useDelayedFlag(freshnessQuery.isLoading)
  const showJobsSkeleton = useDelayedFlag(jobsQuery.isLoading)

  const jobs = jobsQuery.data ?? []
  const failedCount = useMemo(() => jobs.filter((job) => job.status === 'failed').length, [jobs])

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3 }}
      className="max-w-7xl mx-auto px-4 py-6 space-y-6"
    >
      {/* ========== 标题栏 ========== */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-[28px] font-bold">🛠️ 任务监控</h1>
          <p className="text-sm text-muted-foreground mt-1">
            最近 {jobs.length} 条采集/策略任务
            {failedCount > 0 && <span className="text-alert"> · 其中失败 {failedCount} 条</span>}
            {' · '}
            {paused ? '自动刷新已暂停' : '每 30 秒自动刷新'}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
            className="px-2.5 py-2 rounded-lg border border-border bg-background text-sm focus:outline-none focus:ring-2 focus:ring-primary"
          >
            <option value="">全部状态</option>
            <option value="success">成功</option>
            <option value="failed">失败</option>
            <option value="running">运行中</option>
            <option value="pending">等待中</option>
          </select>
          <button
            onClick={() => setPaused((previous) => !previous)}
            className="inline-flex items-center gap-1.5 px-3 py-2 border border-border rounded-lg text-sm hover:bg-muted transition-colors"
          >
            {paused ? <Play className="w-4 h-4" /> : <Pause className="w-4 h-4" />}
            {paused ? '恢复自动刷新' : '暂停自动刷新'}
          </button>
          <button
            onClick={() => {
              void freshnessQuery.refetch()
              void jobsQuery.refetch()
            }}
            disabled={jobsQuery.isFetching}
            className="inline-flex items-center gap-1.5 px-3 py-2 border border-border rounded-lg text-sm hover:bg-muted transition-colors disabled:opacity-50"
          >
            <RefreshCw className="w-4 h-4" />
            刷新{jobsQuery.isFetching ? '中…' : ''}
          </button>
        </div>
      </div>

      {/* ========== 数据集新鲜度总览 ========== */}
      <div className="space-y-2">
        <h2 className="font-semibold text-sm">数据新鲜度总览</h2>
        {freshnessQuery.isLoading ? (
          <div
            aria-busy="true"
            style={{ visibility: showFreshnessSkeleton ? 'visible' : 'hidden' }}
          >
            <SkeletonKpiRow count={6} />
          </div>
        ) : freshnessQuery.isError ? (
          <ErrorState title="数据新鲜度加载失败" error={freshnessQuery.error} onRetry={() => freshnessQuery.refetch()} />
        ) : (
          <FreshnessBadge items={freshnessQuery.data ?? []} variant="cards" />
        )}
      </div>

      {/* ========== 任务表 ========== */}
      <div className="space-y-2">
        <h2 className="font-semibold text-sm">采集 / 策略任务</h2>
        {jobsQuery.isLoading ? (
          <div
            aria-busy="true"
            style={{ visibility: showJobsSkeleton ? 'visible' : 'hidden' }}
          >
            <SkeletonTable rows={8} cols={6} />
          </div>
        ) : jobsQuery.isError ? (
          <ErrorState title="任务列表加载失败" error={jobsQuery.error} onRetry={() => jobsQuery.refetch()} />
        ) : jobs.length === 0 ? (
          <EmptyState
            title="暂无任务记录"
            description="采集任务运行后，这里会展示每次任务的耗时、行数、失败数与错误摘要。"
          />
        ) : (
          <DataTable
            columns={JOB_COLUMNS}
            rows={jobs}
            rowKey={(row) => row.job_id}
            initialSort={{ key: 'started_at', direction: 'desc' }}
            csvFilename="gfna_jobs.csv"
            maxHeight="640px"
            footer={
              <span>
                共 {jobs.length} 条{jobsQuery.isFetching ? ' · 正在刷新…' : ''}
              </span>
            }
          />
        )}
      </div>

      <div className="card p-4 text-xs text-muted-foreground">
        任务数据来自后端 `meta_jobs` / 采集编排记录；若某个数据集长时间未成功，请检查数据源可用性与网络环境。
      </div>
    </motion.div>
  )
}
