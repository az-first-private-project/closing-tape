/**
 * 自选股面板（V6 T8）
 * ====================
 * 展示名单、最近一次命中摘要、跳转与移除；名单只存在本机 localStorage。
 * `compact` 用于盘后分析页的快捷区块（只显示前 5 只）。
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, Star, Trash2 } from 'lucide-react'
import { ApiError } from '../api/client'
import type { WatchHit } from '../types/algo'
import { useWatchSyncStatus } from '../hooks/useWatchSignals'
import { MAX_WATCHLIST, toggleWatch } from '../lib/watchlist'
import { fmtDate } from '../lib/format'

interface Props {
  watchlist: string[]
  hits: WatchHit[]
  days?: number
  latestSignalDate?: string | null
  loading?: boolean
  error?: unknown
  onRetry?: () => void
  compact?: boolean
  showAllLink?: boolean
}

function errorText(error: unknown): string {
  if (error instanceof ApiError) return `${error.code}：${error.message}`
  if (error instanceof Error) return error.message
  return '未知错误'
}

export default function WatchlistPanel({
  watchlist,
  hits,
  days = 10,
  latestSignalDate = null,
  loading = false,
  error,
  onRetry,
  compact = false,
  showAllLink = false,
}: Props) {
  const [hint, setHint] = useState<string | null>(null)
  const syncStatus = useWatchSyncStatus()
  const visible = compact ? watchlist.slice(0, 5) : watchlist

  const handleRemove = (code: string) => {
    const result = toggleWatch(code)
    if (result.reason === 'storage') {
      setHint('本浏览器缓存不可写，改动将同步到服务器')
    } else {
      setHint(null)
    }
  }

  return (
    <section className="card p-4 sm:p-5" aria-labelledby="watchlist-panel-title">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 id="watchlist-panel-title" className="text-base font-semibold flex items-center gap-2">
            <Star className="w-4 h-4 text-warning" aria-hidden="true" />
            自选股
            <span className="text-[11px] font-normal px-2 py-0.5 rounded-full border border-border bg-muted text-muted-foreground num">
              {watchlist.length}/{MAX_WATCHLIST}
            </span>
          </h2>
          <p className="text-xs text-muted-foreground mt-1">
            近 {days} 个交易日的策略命中；名单保存在本机缓存并同步到服务器。
          </p>
        </div>
        {(showAllLink || compact) && (
          <Link
            to="/watchlist"
            className="inline-flex items-center gap-1 text-xs text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
          >
            查看全部提醒
            <ArrowRight className="w-3.5 h-3.5" />
          </Link>
        )}
      </div>

      {error ? (
        <div className="mt-3 rounded-lg border border-alert/40 bg-alert/10 px-3 py-2 text-xs text-alert">
          <p>命中检查失败：{errorText(error)}</p>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="mt-1 underline hover:no-underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
            >
              重试
            </button>
          )}
        </div>
      ) : watchlist.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">
          还没有自选股。可在个股详情页、共识榜或盘后分析的龙头股旁点击星标加入。
        </p>
      ) : (
        <ul className="mt-3 space-y-2">
          {visible.map((code) => {
            const codeHits = hits.filter((hit) => hit.code === code)
            const latest = codeHits[0]
            return (
              <li
                key={code}
                className="flex items-start justify-between gap-3 rounded-lg border border-border bg-background/60 p-2.5"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <Link
                      to={`/stocks/${code}`}
                      className="font-mono text-xs text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
                    >
                      {code}
                    </Link>
                    <span className="text-sm font-medium truncate">{latest?.name ?? '—'}</span>
                  </div>
                  <p className="text-[11px] text-muted-foreground mt-0.5">
                    {latest ? (
                      <>
                        <span className="font-mono uppercase text-foreground">{latest.algo_code}</span>
                        {' · '}
                        {latest.algo_name}
                        {' · '}
                        {fmtDate(latest.signal_date)}
                        {latest.rank !== null && ` · 排名 ${latest.rank}`}
                        {codeHits.length > 1 && ` · 近 ${days} 日 ${codeHits.length} 条`}
                      </>
                    ) : loading ? (
                      '正在检查命中…'
                    ) : (
                      `近 ${days} 个交易日无命中`
                    )}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => handleRemove(code)}
                  aria-label={`从自选中移除 ${latest?.name ?? code}`}
                  className={`shrink-0 rounded-md border border-border p-1.5 text-muted-foreground transition-colors hover:border-alert/50 hover:bg-alert/10 hover:text-alert focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                    compact ? '' : 'inline-flex items-center gap-1 px-2 text-[11px]'
                  }`}
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  {!compact && '移除'}
                </button>
              </li>
            )
          })}
        </ul>
      )}

      {compact && watchlist.length > visible.length && (
        <p className="mt-2 text-[11px] text-muted-foreground">
          还有 {watchlist.length - visible.length} 只，进入自选页查看全部。
        </p>
      )}

      {hint && (
        <p role="status" className="mt-2 text-[11px] text-warning">
          {hint}
        </p>
      )}

      {syncStatus === 'offline' ? (
        <p role="status" className="mt-2 text-[11px] text-warning">
          服务器同步失败，改动已保存在本机，恢复后会自动重试。
        </p>
      ) : syncStatus === 'syncing' ? (
        <p className="mt-2 text-[11px] text-muted-foreground">正在同步到服务器…</p>
      ) : syncStatus === 'synced' ? (
        <p className="mt-2 text-[11px] text-muted-foreground">已同步到服务器</p>
      ) : null}

      {latestSignalDate && !error && (
        <p className="mt-3 text-[11px] text-muted-foreground">
          最近命中日 <span className="num text-foreground">{latestSignalDate}</span>
        </p>
      )}
    </section>
  )
}
