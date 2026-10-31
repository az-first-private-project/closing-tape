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
import { EmptyState, ErrorState, LoadingState } from '../components/StateViews'

const DAYS = 10

export default function WatchlistPage() {
  const { watchlist, query } = useWatchBadge(DAYS)
  const data = query.data
  const items = data?.items ?? []

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
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-[28px] font-bold flex items-center gap-2">
            <Star className="w-6 h-6 text-warning" aria-hidden="true" />
            自选股
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            名单与已读水位同步到本机后端（localStorage 仅作缓存）；每 60 秒检查一次近 {DAYS} 个交易日的策略命中。
          </p>
        </div>
        <button
          type="button"
          onClick={() => query.refetch()}
          disabled={query.isFetching}
          className="inline-flex items-center gap-1.5 px-3 py-2 border border-border rounded-lg text-sm hover:bg-muted transition-colors disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <RefreshCw className={`w-4 h-4 ${query.isFetching ? 'animate-spin' : ''}`} />
          刷新命中
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
          <h2 id="watch-hits-title" className="text-base font-semibold">
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
                去 <Link to="/algo-center" className="text-primary hover:underline">选股中心</Link>{' '}
                或个股详情页点击星标加入自选，命中策略后会在这里和导航栏提醒。
              </span>
            }
          />
        ) : query.isLoading ? (
          <LoadingState text="正在检查自选股命中…" />
        ) : query.isError ? (
          <ErrorState title="自选股命中加载失败" error={query.error} onRetry={() => query.refetch()} />
        ) : items.length === 0 ? (
          <EmptyState
            title={`近 ${DAYS} 个交易日无命中`}
            description="自选股暂未命中当前启用的策略。策略 run 完成后会自动出现在这里。"
          />
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm">
              <thead>
                <tr className="text-[11px] text-muted-foreground border-b border-border">
                  <th className="text-left font-medium py-2 pl-3">日期</th>
                  <th className="text-left font-medium py-2 px-2">策略</th>
                  <th className="text-left font-medium py-2 px-2">代码 / 名称</th>
                  <th className="text-right font-medium py-2 px-2">排名</th>
                  <th className="text-right font-medium py-2 pr-3">操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map((hit) => (
                  <tr key={`${hit.hit_id}:${hit.code}`} className="border-b border-border/60 last:border-0">
                    <td className="py-2.5 pl-3 num text-xs whitespace-nowrap">{fmtDate(hit.signal_date)}</td>
                    <td className="py-2.5 px-2">
                      <span className="font-mono uppercase text-xs text-foreground">{hit.algo_code}</span>
                      <span className="ml-1.5 text-xs text-muted-foreground">{hit.algo_name}</span>
                    </td>
                    <td className="py-2.5 px-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <Link to={`/stocks/${hit.code}`} className="font-mono text-xs text-primary hover:underline">
                          {hit.code}
                        </Link>
                        <span className="truncate">{hit.name}</span>
                      </div>
                    </td>
                    <td className="py-2.5 px-2 text-right num">{hit.rank ?? '—'}</td>
                    <td className="py-2.5 pr-3 text-right">
                      <Link to={`/stocks/${hit.code}`} className="text-xs text-primary hover:underline">
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

      <p className="text-[11px] text-muted-foreground">
        提示：同一策略同一天只算一条提醒；已读水位同步到本机后端，localStorage 仅作缓存。
        命中仅为策略筛选结果，不构成投资建议。
      </p>
    </motion.div>
  )
}
