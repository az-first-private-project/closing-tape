/**
 * 任务监控页（契约第 1 节）
 * ==========================
 * - 顶部：数据集新鲜度总览（每张数据集状态 / 最后成功时间 / 覆盖率 / 异常数）；
 * - 任务表：task / dataset / status / 耗时 / 行数 / 失败数 / 错误摘要；
 * - 30 秒自动刷新，可暂停。
 */
import { useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { Database, Pause, Play, RefreshCw } from 'lucide-react'
import { ApiError } from '../api/client'
import { triggerCatchUp } from '../api/meta'
import { useFreshness } from '../hooks/useFreshness'
import { useJobs } from '../hooks/useJobs'
import FreshnessBadge from '../components/FreshnessBadge'
import DataTable, { type DataTableColumn } from '../components/DataTable'
import PaginationBar from '../components/PaginationBar'
import PageHeader from '../components/PageHeader'
import SectionHeader from '../components/SectionHeader'
import Disclaimer from '../components/Disclaimer'
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
      return 'border-brass/45 bg-brass/[0.08] text-brass-strong dark:text-brass'
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
    render: (row) => <span className="font-mono text-[12px] text-muted-foreground">{row.dataset ?? '—'}</span>,
  },
  {
    key: 'status',
    header: '状态',
    sortable: true,
    align: 'center',
    value: (row) => row.status,
    render: (row) => (
      <span className={`inline-block text-[12px] px-2 py-0.5 rounded-full border ${statusBadgeClass(row.status)}`}>
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

const JOB_PAGE_SIZE = 50

export default function JobsPage() {
  const [paused, setPaused] = useState(false)
  const [statusFilter, setStatusFilter] = useState('')
  const [page, setPage] = useState(1)
  const [catchUp, setCatchUp] = useState<{
    status: 'idle' | 'submitting' | 'accepted' | 'error'
    message: string
  }>({ status: 'idle', message: '' })
  const [adminKey, setAdminKey] = useState(() => sessionStorage.getItem('gfna-admin-key') ?? '')
  const [keyPanelOpen, setKeyPanelOpen] = useState(false)
  const [keyInput, setKeyInput] = useState('')

  const freshnessQuery = useFreshness()
  const jobsQuery = useJobs(
    {
      limit: JOB_PAGE_SIZE,
      offset: (page - 1) * JOB_PAGE_SIZE,
      status: statusFilter || undefined,
    },
    paused,
  )
  const showFreshnessSkeleton = useDelayedFlag(freshnessQuery.isLoading)
  const showJobsSkeleton = useDelayedFlag(jobsQuery.isLoading)

  const jobs = jobsQuery.data?.items ?? []
  const jobsTotal = jobsQuery.data?.total ?? jobs.length
  const totalPages = Math.max(1, Math.ceil(jobsTotal / JOB_PAGE_SIZE))
  const currentPage = Math.min(page, totalPages)
  const failedCount = useMemo(() => jobs.filter((job) => job.status === 'failed').length, [jobs])

  const submitCatchUp = async (key: string) => {
    setCatchUp({ status: 'submitting', message: '' })
    try {
      await triggerCatchUp(key || undefined)
      setKeyPanelOpen(false)
      setCatchUp({
        status: 'accepted',
        message: '已提交后台补数，通常需要 10–30 分钟；完成后任务列表会出现记录',
      })
      void freshnessQuery.refetch()
      void jobsQuery.refetch()
    } catch (error) {
      if (error instanceof ApiError && error.status === 403) {
        // 密钥失效/未配置：清掉旧值，改用页面内输入条，不使用被浏览器禁用的 prompt()
        sessionStorage.removeItem('gfna-admin-key')
        setAdminKey('')
        setKeyPanelOpen(true)
        setCatchUp({
          status: 'error',
          message: '管理密钥无效，请重新输入',
        })
        return
      }
      setCatchUp({
        status: 'error',
        message: error instanceof Error ? error.message : '提交补数失败',
      })
    }
  }

  const handleCatchUp = () => {
    if (!adminKey) {
      setKeyPanelOpen(true)
      setCatchUp({ status: 'idle', message: '请输入管理密钥后继续' })
      return
    }
    void submitCatchUp(adminKey)
  }

  const handleKeySubmit = () => {
    const key = keyInput.trim()
    if (!key) {
      setCatchUp({ status: 'error', message: '请输入管理密钥' })
      return
    }
    sessionStorage.setItem('gfna-admin-key', key)
    setAdminKey(key)
    setKeyInput('')
    void submitCatchUp(key)
  }

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3 }}
      className="max-w-7xl mx-auto px-4 py-6 space-y-6"
    >
      {/* ========== 标题栏 ========== */}
      <PageHeader
        eyebrow="Pipeline Monitor"
        title="任务监控"
        description={
          <span className="flex flex-wrap items-center gap-x-2">
            <span>第 {currentPage} / {totalPages} 页</span>
            {failedCount > 0 && <span className="text-alert">本页失败 {failedCount} 条</span>}
            <span aria-hidden="true">·</span>
            <span>{paused ? '自动刷新已暂停' : '每 30 秒自动刷新'}</span>
          </span>
        }
        actions={
          <div className="flex flex-wrap items-center gap-2">
          <select
            value={statusFilter}
            onChange={(event) => {
              setStatusFilter(event.target.value)
              setPage(1)
            }}
            className="field !h-9 !w-auto cursor-pointer !py-0 pr-2 text-[12px]"
            aria-label="按状态筛选任务"
          >
            <option value="">全部状态</option>
            <option value="success">成功</option>
            <option value="failed">失败</option>
            <option value="running">运行中</option>
            <option value="pending">等待中</option>
          </select>
          <button
            onClick={() => setPaused((previous) => !previous)}
            className="btn btn-sm h-9"
          >
            {paused ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
            {paused ? '恢复自动刷新' : '暂停自动刷新'}
          </button>
          <button
            onClick={() => void handleCatchUp()}
            disabled={catchUp.status === 'submitting'}
            className="btn btn-sm h-9"
            title="检查最近缺失交易日并在后台补跑（复用启动补缺逻辑）"
          >
            <Database className={`h-3.5 w-3.5 ${catchUp.status === 'submitting' ? 'animate-pulse' : ''}`} />
            {catchUp.status === 'submitting' ? '提交中…' : '一键补数'}
          </button>
          <button
            onClick={() => {
              void freshnessQuery.refetch()
              void jobsQuery.refetch()
            }}
            disabled={jobsQuery.isFetching}
            className="btn btn-sm h-9"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${jobsQuery.isFetching ? 'animate-spin' : ''}`} />
            刷新{jobsQuery.isFetching ? '中…' : ''}
          </button>
          </div>
        }
      />

      <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground">
        <span>每日 15:36 自动更新；后端启动后会自动补最近缺失交易日。</span>
        {catchUp.message && (
          <span className={catchUp.status === 'error' ? 'text-alert' : 'text-foreground'}>
            {catchUp.message}
          </span>
        )}
      </div>

      {keyPanelOpen && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
          <span className="text-[11px] text-muted-foreground">
            管理密钥（<span className="font-mono">backend/.env</span> 的 ADMIN_API_KEY）
          </span>
          <input
            type="password"
            value={keyInput}
            onChange={(event) => setKeyInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault()
                handleKeySubmit()
              }
            }}
            placeholder="X-Admin-Key"
            autoComplete="off"
            aria-label="管理密钥"
            className="field !h-8 w-44 !py-0 text-[12px]"
          />
          <button
            type="button"
            onClick={handleKeySubmit}
            disabled={catchUp.status === 'submitting'}
            className="btn btn-sm h-8"
          >
            {catchUp.status === 'submitting' ? '提交中…' : '确认并补数'}
          </button>
          <button
            type="button"
            onClick={() => setKeyPanelOpen(false)}
            className="btn btn-sm h-8"
          >
            取消
          </button>
        </div>
      )}

      {/* ========== 数据集新鲜度总览 ========== */}
      <div className="space-y-2">
        <SectionHeader index="01" title="数据新鲜度总览" hint="每个数据集最后成功时间 / 覆盖率 / 异常数" />
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
        <SectionHeader index="02" title="采集 / 策略任务" hint="按开始时间倒序；导出仅当前页" />
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
          <>
            <DataTable
              columns={JOB_COLUMNS}
              rows={jobs}
              rowKey={(row) => row.job_id}
              initialSort={{ key: 'started_at', direction: 'desc' }}
              csvFilename="gfna_jobs.csv"
              maxHeight="640px"
              total={jobsTotal}
              rangeNote={`第 ${currentPage} / ${totalPages} 页`}
              footer={
                <span className="flex flex-wrap items-center gap-x-2">
                  <span>导出 CSV 仅包含第 {currentPage} 页这 {jobs.length} 条</span>
                  {jobsQuery.isFetching && <span>· 正在刷新…</span>}
                </span>
              }
            />
            {totalPages > 1 && (
              <PaginationBar
                page={currentPage}
                totalPages={totalPages}
                total={jobsTotal}
                onPageChange={setPage}
                compact
              />
            )}
          </>
        )}
      </div>

      <Disclaimer extraOnly extra="任务数据来自后端 meta_jobs / 采集编排记录；若某个数据集长时间未成功，请检查数据源可用性与网络环境。" />
    </motion.div>
  )
}
