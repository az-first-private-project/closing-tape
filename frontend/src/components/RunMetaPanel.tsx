/**
 * Run 元信息面板（契约 5.2）
 * ===========================
 * 展示 run_id / 耗时 / universe_count / signal_count / data_version /
 * 参数快照 / from_cache，并提供「强制刷新」（force_refresh=true）入口。
 */
import { useState } from 'react'
import {
  Check,
  Copy,
  Cpu,
  Database,
  GitBranch,
  History,
  RefreshCw,
  RotateCcw,
  Timer,
  TrendingUp,
  Zap,
} from 'lucide-react'
import { isLegacySignalSnapshot, type AlgoParamValue, type AlgoRun } from '../types/algo'
import type { Freshness } from '../types/common'
import { fmtDateTime, fmtDuration, fmtInt } from '../lib/format'
import FreshnessBadge from './FreshnessBadge'

/** 结果来源：本次执行 / 缓存结果 / 历史 run / 会话恢复（U01 显式区分） */
export type RunSource = 'live' | 'cache' | 'history' | 'restored'

interface RunMetaPanelProps {
  run: AlgoRun
  /** 策略代码：判断该 run 是否本应有结构化信号依据 */
  algo?: string
  source: RunSource
  tradeDate: string | null
  /** 参数面板与当前 run 快照是否不一致 */
  dirty?: boolean
  /** 数据新鲜度（可选，后端若在结果里附带则一并展示） */
  freshness?: Freshness[]
  /** 强制刷新回调：显式传 force_refresh=true 重跑 */
  onForceRefresh?: () => void
  refreshing?: boolean
}

const SOURCE_META: Record<
  RunSource,
  { label: string; hint: string; icon: typeof Zap; className: string }
> = {
  live: {
    label: '本次执行',
    hint: '刚由当前参数计算得出',
    icon: Zap,
    className: 'border-primary/40 bg-primary/10 text-primary',
  },
  cache: {
    label: '缓存结果',
    hint: '命中相同参数与数据版本的已有 run',
    icon: Database,
    className: 'border-success/40 bg-success/10 text-success',
  },
  history: {
    label: '历史 run',
    hint: '从历史 run 列表载入，不是本次执行',
    icon: History,
    className: 'border-brass/45 bg-brass/10 text-brass-strong dark:text-brass',
  },
  restored: {
    label: '已恢复',
    hint: '按 URL / 会话记录恢复的上一次结果，不是本次执行',
    icon: RotateCcw,
    className: 'border-brass/45 bg-brass/10 text-brass-strong dark:text-brass',
  },
}

/** 参数快照值 → 展示文本 */
function formatParamValue(value: AlgoParamValue): string {
  if (value === null) return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}

/** 单个统计项 */
function MetaItem({
  icon: Icon,
  label,
  value,
  title,
}: {
  icon: typeof Cpu
  label: string
  value: string
  title?: string
}) {
  return (
    <div className="flex items-start gap-2" title={title}>
      <Icon className="w-3.5 h-3.5 mt-0.5 text-muted-foreground shrink-0" />
      <div className="min-w-0">
        <p className="text-[12px] text-muted-foreground">{label}</p>
        <p className="text-[12px] font-medium num truncate">{value}</p>
      </div>
    </div>
  )
}

const SHORT_RUN_ID_LENGTH = 12

export default function RunMetaPanel({
  run,
  algo,
  source,
  tradeDate,
  dirty = false,
  freshness,
  onForceRefresh,
  refreshing = false,
}: RunMetaPanelProps) {
  const [copied, setCopied] = useState(false)
  const sourceMeta = SOURCE_META[source]
  const SourceIcon = sourceMeta.icon
  /** 有信号但快照版本落后：明确提示依据不可展开，而不是让用户以为功能缺失 */
  const legacySnapshot =
    isLegacySignalSnapshot(algo, run.signal_schema) && (run.signal_count ?? 0) > 0

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(run.run_id)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // 剪贴板不可用时静默失败，不影响页面功能
    }
  }

  const paramEntries = Object.entries(run.params ?? {})

  return (
    <div className="card p-4 space-y-3">
      {/* 头部：来源标记 + 强制刷新 */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={`inline-flex items-center gap-1 text-[12px] px-2 py-0.5 rounded-full border ${sourceMeta.className}`}
            title={sourceMeta.hint}
          >
            <SourceIcon className="w-3 h-3" />
            {sourceMeta.label}
          </span>
          <span className="text-[12px] text-muted-foreground">交易日 {tradeDate ?? '—'}</span>
          {dirty && (
            <span
              className="inline-flex items-center gap-1 text-[12px] px-2 py-0.5 rounded-full border border-warning/45 bg-warning/10 text-warning"
              title="左侧参数面板已修改；下方结果、导出与信号依据仍对应当前展示的 run 快照"
            >
              参数面板已修改
            </span>
          )}
          {run.status && run.status !== 'success' && (
            <span
              className={`inline-flex items-center gap-1 text-[12px] px-2 py-0.5 rounded-full border ${
                run.status === 'failed'
                  ? 'border-alert/40 bg-alert/10 text-alert'
                  : 'border-warning/40 bg-warning/10 text-warning'
              }`}
              title={run.error ?? undefined}
            >
              run {run.status}
            </span>
          )}
          {legacySnapshot && (
            <span
              className="inline-flex items-center gap-1 text-[12px] px-2 py-0.5 rounded-full border border-border bg-muted/40 text-muted-foreground"
              title={`信号快照 schema ${run.signal_schema ?? 1}：该 run 产生于结构化信号依据上线前，无法展开逐条依据/评分拆解；如需最新依据请重新执行。`}
            >
              <History className="w-3 h-3" />
              旧版本快照
            </span>
          )}
        </div>
        {onForceRefresh && (
          <button
            onClick={onForceRefresh}
            disabled={refreshing}
            className="btn btn-sm"
            title="忽略缓存重新计算（force_refresh=true）"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            {refreshing ? '重算中…' : '强制刷新'}
          </button>
        )}
      </div>

      <p className="text-[12px] leading-relaxed text-muted-foreground">
        {sourceMeta.hint}。结果表、导出 CSV 与信号依据均使用该 run 的参数快照，不使用左侧面板当前值。
      </p>

      {/* run_id */}
      <div className="flex items-center gap-2">
        <GitBranch className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
        <span className="text-[12px] text-muted-foreground shrink-0">run_id</span>
        <code className="truncate rounded border border-border/60 bg-muted/50 px-2 py-1 font-mono text-[12px]" title={run.run_id}>
          {run.run_id.length > SHORT_RUN_ID_LENGTH ? `…${run.run_id.slice(-SHORT_RUN_ID_LENGTH)}` : run.run_id}
        </code>
        <button
          onClick={handleCopy}
          className="text-muted-foreground hover:text-foreground transition-colors"
          title="复制完整 run_id"
        >
          {copied ? <Check className="w-3.5 h-3.5 text-success" /> : <Copy className="w-3.5 h-3.5" />}
        </button>
        <span className="ml-auto text-[12px] text-muted-foreground num">{fmtDateTime(run.created_at)}</span>
      </div>

      {/* 关键指标 */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3 pt-1">
        <MetaItem icon={Timer} label="耗时" value={fmtDuration(run.elapsed_ms)} />
        <MetaItem icon={Database} label="universe_count" value={fmtInt(run.universe_count)} />
        <MetaItem icon={TrendingUp} label="signal_count" value={fmtInt(run.signal_count)} />
        <MetaItem icon={Cpu} label="data_version" value={run.data_version ?? '—'} title={run.data_version ?? undefined} />
        <MetaItem icon={GitBranch} label="参数快照" value={`${paramEntries.length} 项`} />
      </div>

      {/* 参数快照 */}
      {paramEntries.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[12px] text-muted-foreground">参数快照</span>
          {paramEntries.map(([key, value]) => (
            <span
              key={key}
              className="rounded border border-border/50 bg-muted/40 px-2 py-0.5 font-mono text-[12px] tracking-wide"
              title={`${key} = ${formatParamValue(value)}`}
            >
              {key}=<span className="text-foreground">{formatParamValue(value)}</span>
            </span>
          ))}
        </div>
      )}

      {/* 数据新鲜度（可选） */}
      {freshness && freshness.length > 0 && <FreshnessBadge items={freshness} />}
    </div>
  )
}
