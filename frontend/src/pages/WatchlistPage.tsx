/**
 * 自选股与站内信号提醒（V6 T8）
 * ==============================
 * - 名单只存本机 localStorage；
 * - 进入本页即把当前命中的 hit_id 写入已读水位，导航栏红点随之清零；
 * - 命中查询每 60 秒轮询，标签页切到后台时自动停止。
 */
import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { RefreshCw, Star } from 'lucide-react'
import { useWatchBadge } from '../hooks/useWatchSignals'
import { markHitsSeen } from '../lib/watchlist'
import { fmtDate } from '../lib/format'
import WatchlistPanel from '../components/WatchlistPanel'
import FreshnessBadge from '../components/FreshnessBadge'
import { EmptyState, ErrorState, SkeletonTable } from '../components/StateViews'
import { useDelayedFlag } from '../hooks/useDelayedFlag'

const DAYS = 10

export default function WatchlistPage() {
  const { watchlist, query } = useWatchBadge(DAYS)
  const data = query.data
  const items = data?.items ?? []
  const showSkeleton = useDelayedFlag(query.isLoading)

  useEffect(() => {
    if (!data) return
    markHitsSeen(data.items.map((item) => item.hit_id))
  }, [data])

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3 }}
      className="max-w-5xl mx-auto px-4 py-6 space-y-6"
    >
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="rise">
          <div className="flex items-center gap-2.5">
            <span aria-hidden="true" className="h-px w-6 bg-brass" />
            <span className="eyebrow">Watchlist</span>
          </div>
          <h1 className="page-title mt-2.5 flex items-center gap-2">
            自选股
            <Star className="h-4 w-4 text-brass" aria-hidden="true" />
          </h1>
          <p className="mt-2 max-w-2xl text-[12px] leading-relaxed text-muted-foreground">
            名单与已读水位同步到本机后端（localStorage 仅作缓存）；每 60 秒检查一次近 {DAYS} 个交易日的策略命中。
          </p>
        </div>
        <button
          type="button"
          onClick={() => query.refetch()}
          disabled={query.isFetching}
          className="btn btn-sm h-9"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${query.isFetching ? 'animate-spin' : ''}`} />
          刷新命中{query.isFetching ? '中…' : ''}
        </button>
      </div>

      {data?.freshness && <FreshnessBadge items={[data.freshness]} title="选股任务" />}

      <WatchlistPanel
        watchlist={watchlist}
        hits={items}
        days={DAYS}
        latestSignalDate={data?.latest_signal_date ?? null}
        loading={query.isLoading}
        error={query.isError ? query.error : undefined}
        onRetry={() => query.refetch()}
      />

      <section className="space-y-3" aria-labelledby="watch-hits-title">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="watch-hits-title" className="text-[13px] font-semibold">
            近期命中明细
          </h2>
          {data && (
            <span className="text-[11px] text-muted-foreground">
              共 <span className="num text-foreground">{items.length}</span> 条 · 最近信号日{' '}
              <span className="num text-foreground">{data.latest_signal_date ?? '—'}</span>
            </span>
          )}
        </div>

        {watchlist.length === 0 ? (
          <EmptyState
            title="暂无自选股"
            description={
              <span>
                去 <Link to="/algo-center" className="text-brass-strong hover:underline dark:text-brass">选股中心</Link>{' '}
                或个股详情页点击星标加入自选，命中策略后会在这里和导航栏提醒。
              </span>
            }
          />
        ) : query.isLoading ? (
          <div
            aria-busy="true"
            style={{ visibility: showSkeleton ? 'visible' : 'hidden' }}
          >
            <SkeletonTable rows={5} cols={5} />
          </div>
        ) : query.isError ? (
          <ErrorState title="自选股命中加载失败" error={query.error} onRetry={() => query.refetch()} />
        ) : items.length === 0 ? (
          <EmptyState
            title={`近 ${DAYS} 个交易日无命中`}
            description="自选股暂未命中当前启用的策略。策略 run 完成后会自动出现在这里。"
          />
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full min-w-[640px] text-[12px]">
              <thead>
                <tr className="border-b border-border/70 bg-muted/30 font-mono text-[10px] tracking-[0.06em] text-muted-foreground">
                  <th className="py-2.5 pl-4 text-left font-medium">日期</th>
                  <th className="px-2 py-2.5 text-left font-medium">策略</th>
                  <th className="px-2 py-2.5 text-left font-medium">代码 / 名称</th>
                  <th className="px-2 py-2.5 text-right font-medium">排名</th>
                  <th className="py-2.5 pr-4 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map((hit) => (
                  <tr key={`${hit.hit_id}:${hit.code}`} className="table-row border-b border-border/50 last:border-0">
                    <td className="num whitespace-nowrap py-2.5 pl-4 text-[11px]">{fmtDate(hit.signal_date)}</td>
                    <td className="py-2.5 px-2">
                      <span className="font-mono uppercase text-xs text-foreground">{hit.algo_code}</span>
                      <span className="ml-1.5 text-xs text-muted-foreground">{hit.algo_name}</span>
                    </td>
                    <td className="py-2.5 px-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <Link to={`/stocks/${hit.code}`} className="font-mono text-[10px] text-brass-strong hover:underline dark:text-brass">
                          {hit.code}
                        </Link>
                        <span className="truncate">{hit.name}</span>
                      </div>
                    </td>
                    <td className="py-2.5 px-2 text-right num">{hit.rank ?? '—'}</td>
                    <td className="py-2.5 pr-4 text-right">
                      <Link to={`/stocks/${hit.code}`} className="text-[10px] text-brass-strong hover:underline dark:text-brass">
                        查看个股
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <p className="border-t border-border/60 pt-4 text-[10px] leading-relaxed text-muted-foreground">
        提示：同一策略同一天只算一条提醒；已读水位同步到本机后端，localStorage 仅作缓存。
        命中仅为策略筛选结果，不构成投资建议。
      </p>
    </motion.div>
  )
}
