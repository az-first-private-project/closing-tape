/**
 * 数据新鲜度徽标（契约 0.4）
 * ==========================
 * 两种形态：
 *  - compact：仪表盘顶部一行紧凑徽标，展示每个数据集状态；
 *  - cards：任务监控页的数据集总览卡片，额外展示行数 / 覆盖率 / 异常数 / 错误。
 * status ∈ ok | stale | quality_failed | error | unknown。
 */
import { AlertTriangle, CheckCircle2, CircleHelp, Clock, Database, XCircle } from 'lucide-react'
import type { Freshness, FreshnessStatus } from '../types/common'
import { fmtDateTime, fmtInt, fmtRatio } from '../lib/format'

interface StatusMeta {
  label: string
  icon: typeof CheckCircle2
  /** 紧凑徽标配色 */
  badge: string
  /** 图标配色 */
  iconClass: string
}

const STATUS_META: Record<FreshnessStatus, StatusMeta> = {
  ok: {
    label: '正常',
    icon: CheckCircle2,
    badge: 'border-success/40 bg-success/10 text-success',
    iconClass: 'text-success',
  },
  stale: {
    label: '滞后',
    icon: Clock,
    badge: 'border-brass/40 bg-brass/10 text-brass-strong dark:text-brass',
    iconClass: 'text-brass-strong dark:text-brass',
  },
  quality_failed: {
    label: '质量异常',
    icon: AlertTriangle,
    badge: 'border-warning/45 bg-warning/10 text-warning',
    iconClass: 'text-warning',
  },
  error: {
    label: '失败',
    icon: XCircle,
    badge: 'border-alert/40 bg-alert/10 text-alert',
    iconClass: 'text-alert',
  },
  unknown: {
    label: '未知',
    icon: CircleHelp,
    badge: 'border-border bg-muted text-muted-foreground',
    iconClass: 'text-muted-foreground',
  },
}

/** 生成 title 提示文本（完整信息，避免额外弹层组件） */
function buildTitle(item: Freshness): string {
  const parts = [
    `${item.display_name}（${item.dataset}）`,
    `状态：${STATUS_META[item.status]?.label ?? item.status}`,
    `最后成功：${fmtDateTime(item.last_success_at)}`,
    `最后交易日：${item.last_trade_date ?? '—'}`,
    `行数：${fmtInt(item.rows)}`,
    `覆盖率：${fmtRatio(item.coverage)}`,
    `异常数：${fmtInt(item.anomalies)}`,
  ]
  if (item.error) parts.push(`错误：${item.error}`)
  return parts.join('\n')
}

interface FreshnessBadgeProps {
  items: Freshness[]
  /** compact：紧凑徽标行；cards：数据集卡片总览 */
  variant?: 'compact' | 'cards'
  title?: string
  className?: string
}

export default function FreshnessBadge({
  items,
  variant = 'compact',
  title = '数据新鲜度',
  className = '',
}: FreshnessBadgeProps) {
  if (!items || items.length === 0) {
    return (
      <div className={`text-xs text-muted-foreground flex items-center gap-1.5 ${className}`}>
        <CircleHelp className="w-3.5 h-3.5" />
        {title}：暂无元信息
      </div>
    )
  }

  if (variant === 'cards') {
    return (
      <div className={`grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3 ${className}`}>
        {items.map((item) => {
          const meta = STATUS_META[item.status] ?? STATUS_META.unknown
          const Icon = meta.icon
          return (
            <div
              key={item.dataset}
              className="card group relative overflow-hidden p-4 pl-5"
              title={buildTitle(item)}
            >
              {/* 左侧状态色条：一眼分辨数据集健康度 */}
              <span
                aria-hidden="true"
                className={`absolute inset-y-0 left-0 w-[2px] ${meta.iconClass.replace('text-', 'bg-')} opacity-70`}
              />
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <Icon className={`w-3.5 h-3.5 shrink-0 ${meta.iconClass}`} />
                  <span className="text-[13px] font-medium truncate">{item.display_name}</span>
                </div>
                <span className={`shrink-0 text-[12px] px-2 py-0.5 rounded-full border ${meta.badge}`}>{meta.label}</span>
              </div>
              <p className="mt-1 text-[12px] text-muted-foreground/80 font-mono truncate tracking-wide">{item.dataset}</p>
              <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-[12px]">
                <div>
                  <dt className="text-muted-foreground">最后成功</dt>
                  <dd className="num mt-0.5 text-foreground">{fmtDateTime(item.last_success_at)}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">最后交易日</dt>
                  <dd className="num mt-0.5 text-foreground">{item.last_trade_date ?? '—'}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">行数</dt>
                  <dd className="num mt-0.5">{fmtInt(item.rows)}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">覆盖率</dt>
                  <dd className="num mt-0.5">{fmtRatio(item.coverage)}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">异常数</dt>
                  <dd className="num mt-0.5">{fmtInt(item.anomalies)}</dd>
                </div>
              </dl>
              {item.error && (
                <p className="mt-2 text-[12px] text-alert break-words line-clamp-3" title={item.error}>
                  {item.error}
                </p>
              )}
            </div>
          )
        })}
      </div>
    )
  }

  return (
    <div className={`flex flex-wrap items-center gap-2 ${className}`}>
      <span className="inline-flex items-center gap-1.5 font-mono text-[12px] tracking-[0.16em] text-muted-foreground/80 uppercase">
        <Database className="w-3 h-3" />
        {title}
      </span>
      {items.map((item) => {
        const meta = STATUS_META[item.status] ?? STATUS_META.unknown
        const Icon = meta.icon
        const lastSuccess = item.last_success_at
          ? `更新 ${fmtDateTime(item.last_success_at).slice(5)}`
          : '从未成功'
        return (
          <span
            key={item.dataset}
            title={buildTitle(item)}
            className={`inline-flex items-center gap-1.5 text-[12px] px-2 py-[3px] rounded-full border cursor-help
              transition-colors duration-300 ${meta.badge}`}
          >
            <Icon className="w-3 h-3" />
            <span className="font-medium">{item.display_name}</span>
            <span className="opacity-80">{meta.label}</span>
            {item.coverage !== null && (
              <span className="hidden opacity-70 num sm:inline">覆盖 {fmtRatio(item.coverage)}</span>
            )}
            <span className="hidden opacity-60 num sm:inline">{lastSuccess}</span>
          </span>
        )
      })}
    </div>
  )
}
