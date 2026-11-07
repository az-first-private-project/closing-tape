/**
 * 多策略共识榜（V6 T6）
 * =====================
 * 同一只票被多个策略同时命中时按命中策略数排序。
 * - 默认折叠在策略卡片之下（策略天梯 T5 才是首要信息），展开后才请求；
 * - 命中数徽章 ●●●○○ 的填充比例 = 命中数 / 当日已评估策略数，满命中视觉最重；
 * - 命中策略可展开查看该票在各策略里的名次、排序值与 metrics（每策略前 3 项）；
 * - **不展示跨策略平均分**：各策略 score 量纲不同，混算会误导。
 */
import { Fragment, useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronDown, Info, Star } from 'lucide-react'
import { useConsensus } from '../hooks/useAlgo'
import { useKline } from '../hooks/useStock'
import type { ConsensusAlgoHit, ConsensusItem } from '../types/algo'
import { getWatchlist, toggleWatch, MAX_WATCHLIST } from '../lib/watchlist'
import { fmtMetricValue, fmtNumber, fmtPct, fmtScore, pctColor } from '../lib/format'
import { EmptyState, ErrorState } from './StateViews'
import FreshnessBadge from './FreshnessBadge'
import Sparkline from './Sparkline'

/** 命中数徽章：圆点 + 比例色阶（满命中用实心 primary） */
function HitBadge({ hits, total }: { hits: number; total: number }) {
  const safeTotal = Math.max(total, hits)
  const ratio = safeTotal > 0 ? hits / safeTotal : 0
  const tone =
    ratio >= 1
      ? 'bg-brass text-[#17140d] border-brass'
      : ratio >= 0.6
        ? 'bg-brass/20 text-brass-strong border-brass/45 dark:text-brass'
        : ratio >= 0.35
          ? 'bg-brass/10 text-brass-strong border-brass/30 dark:text-brass'
          : 'bg-muted text-muted-foreground border-border'
  const dots = '●'.repeat(hits) + '○'.repeat(Math.max(0, safeTotal - hits))
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 ${tone}`}
      aria-label={`命中 ${hits} / ${safeTotal} 个策略`}
    >
      <span aria-hidden="true" className="text-[10px] tracking-[0.08em] num">
        {dots}
      </span>
      <span className="text-[11px] font-bold num">
        {hits}/{safeTotal}
      </span>
    </span>
  )
}

/** 个股 30 日收盘走势：按 code 走 react-query 缓存，展开榜单才请求 */
function StockSparkline({ code, tradeDate }: { code: string; tradeDate?: string }) {
  const query = useKline(code, 30, tradeDate)
  if (query.isLoading) {
    return <span className="inline-block h-[24px] w-[84px]" aria-hidden="true" />
  }
  if (query.isError || !query.data || query.data.items.length === 0) {
    return <span className="text-[11px] text-muted-foreground">—</span>
  }
  return (
    <Sparkline
      values={query.data.items.map((item) => item.close)}
      days={30}
      width={84}
      height={24}
      label={`${code} 近 30 日收盘走势`}
      formatValue={(value) => fmtNumber(value, 2)}
      className="ml-auto w-[84px]"
    />
  )
}

/** 展开后的逐策略指标卡 */
function HitDetails({
  item,
  onOpenAlgo,
}: {
  item: ConsensusItem
  onOpenAlgo?: (algoCode: string) => void
}) {
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {item.algos.map((hit: ConsensusAlgoHit) => (
          <div key={hit.algo_code} className="rounded-lg border border-border bg-background/60 p-3 space-y-2">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="text-sm font-medium truncate">{hit.algo_name}</p>
                <p className="text-[11px] text-muted-foreground num">
                  {hit.algo_code.toUpperCase()} · 名次 {hit.rank ?? '—'} · 排序值{' '}
                  {hit.score === null ? '—' : hit.score.toFixed(2)}
                </p>
              </div>
              {onOpenAlgo && (
                <button
                  type="button"
                  onClick={() => onOpenAlgo(hit.algo_code)}
                  className="shrink-0 text-[11px] px-2 py-1 rounded-md border border-border text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
                >
                  选中策略
                </button>
              )}
            </div>
            {Object.keys(hit.metrics).length === 0 ? (
              <p className="text-[11px] text-muted-foreground">该策略未附带指标</p>
            ) : (
              <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
                {Object.entries(hit.metrics).map(([key, value]) => (
                  <div key={key} className="flex justify-between gap-2 min-w-0">
                    <dt className="text-muted-foreground truncate" title={key}>
                      {key}
                    </dt>
                    <dd className="num text-right shrink-0">
                      {typeof value === 'object' ? '—' : fmtMetricValue(key, value)}
                    </dd>
                  </div>
                ))}
              </dl>
            )}
          </div>
        ))}
      </div>
      <p className="text-[11px] text-muted-foreground">
        仅展示每个命中策略的前 3 个指标键值；各策略评分量纲不同，未做加权或平均。
      </p>
    </div>
  )
}

export default function ConsensusBoard({
  onOpenAlgo,
}: {
  onOpenAlgo?: (algoCode: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [minCount, setMinCount] = useState(2)
  const [expandedCode, setExpandedCode] = useState<string | null>(null)
  const [watchlist, setWatchlist] = useState<string[]>(() => getWatchlist())
  const [watchHint, setWatchHint] = useState<string | null>(null)

  const query = useConsensus(minCount, { enabled: open })
  const data = query.data
  const items = data?.items ?? []
  const totalAlgos = data?.total_algos_evaluated ?? 0

  const handleWatch = (code: string) => {
    const result = toggleWatch(code)
    setWatchlist(getWatchlist())
    if (result.reason === 'limit') {
      setWatchHint(`自选上限 ${MAX_WATCHLIST} 只，请先在自选列表中移除`)
    } else if (result.reason === 'storage') {
      setWatchHint('本浏览器不保存自选，刷新后会丢失')
    } else {
      setWatchHint(null)
    }
  }

  return (
    <section className="card p-4 sm:p-5" aria-labelledby="consensus-title">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="consensus-title" className="flex items-center gap-2 text-[14px] font-semibold">
            多策略共识榜
            <span className="rounded-full border border-border/70 bg-muted/50 px-2 py-0.5 text-[10px] font-normal text-muted-foreground">
              按命中策略数排序
            </span>
          </h2>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            同一只票被多个策略同时命中时，按命中策略数排序；命中策略可展开查看该票在各策略里的名次与指标。
          </p>
        </div>

        <div className="flex items-center gap-2">
          {open && data?.freshness && <FreshnessBadge items={[data.freshness]} title="选股任务" />}
          <button
            type="button"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            aria-controls="consensus-body"
            className="btn btn-sm h-9"
          >
            {open ? '收起共识榜' : '展开共识榜'}
            <ChevronDown className={`w-4 h-4 transition-transform ${open ? 'rotate-180' : ''}`} />
          </button>
        </div>
      </div>

      {open && (
        <div id="consensus-body" className="mt-4 space-y-4">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              最少命中策略数
              <input
                type="range"
                min={2}
                max={5}
                step={1}
                value={minCount}
                onChange={(event) => {
                  setMinCount(Number(event.target.value))
                  setExpandedCode(null)
                }}
                className="w-28 accent-primary"
                aria-label="最少命中策略数"
              />
              <span className="num font-semibold text-foreground w-5 text-center">{minCount}</span>
            </label>
            {data && (
              <span className="text-[11px] text-muted-foreground">
                数据截至 <span className="num text-foreground">{data.trade_date}</span>
                {' · '}已评估 <span className="num text-foreground">{totalAlgos}</span> 个策略
              </span>
            )}
            <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
              <Info className="w-3 h-3" />
              同一策略当日多次参数 run 命中同一票只算一次
            </span>
          </div>

          {query.isLoading && !data ? (
            <p className="text-sm text-muted-foreground py-8 text-center">正在计算共识榜…</p>
          ) : query.isError ? (
            <ErrorState title="共识榜加载失败" error={query.error} onRetry={() => query.refetch()} />
          ) : items.length === 0 ? (
            <EmptyState
              title="暂无共识标的"
              description={`当前最少命中 ${minCount} 个策略时没有标的。可降低阈值，或等待当日选股 run 完成后再看。`}
            />
          ) : (
            <div className="overflow-x-auto -mx-1">
              <table className="w-full min-w-[960px] text-[12px]">
                <thead>
                  <tr className="border-b border-border/70 bg-muted/25 font-mono text-[10px] tracking-[0.05em] text-muted-foreground">
                    <th className="text-left font-medium py-2 pl-1 min-w-[10rem]">代码 / 名称</th>
                    <th className="text-left font-medium py-2 px-2 min-w-[8.5rem]">命中数</th>
                    <th className="text-left font-medium py-2 px-2 min-w-[6rem]">行业</th>
                    <th className="text-right font-medium py-2 px-2">当日涨跌</th>
                    <th className="text-right font-medium py-2 px-2 min-w-[6.5rem]">30 日走势</th>
                    <th className="text-right font-medium py-2 px-2">综合分</th>
                    <th className="text-right font-medium py-2 px-2 min-w-[10rem]">命中策略</th>
                    <th className="text-right font-medium py-2 pr-1 min-w-[5.5rem]">自选</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => {
                    const isExpanded = expandedCode === item.code
                    const watched = watchlist.includes(item.code)
                    return (
                      <Fragment key={item.code}>
                        <tr
                          className={`table-row border-b border-border/50 ${isExpanded ? 'bg-brass/[0.05]' : ''}`}
                        >
                          <td className="py-2.5 pl-1">
                            <div className="flex items-center gap-2 min-w-0">
                              <Link
                                to={`/stocks/${item.code}`}
                                className="font-mono text-[10px] text-brass-strong hover:underline dark:text-brass"
                              >
                                {item.code}
                              </Link>
                              <span className="font-medium truncate">{item.name}</span>
                            </div>
                            <div className="text-[11px] text-muted-foreground">
                              {item.board ?? '—'} · {item.ownership ?? '—'}
                            </div>
                          </td>
                          <td className="py-2.5 px-2">
                            <HitBadge hits={item.hit_count} total={totalAlgos} />
                          </td>
                          <td className="py-2.5 px-2 text-xs">{item.industry_l1 ?? '—'}</td>
                          <td className={`py-2.5 px-2 text-right num ${pctColor(item.pct_chg)}`}>
                            {fmtPct(item.pct_chg)}
                          </td>
                          <td className="py-2.5 px-2 text-right">
                            <StockSparkline code={item.code} tradeDate={data?.trade_date} />
                          </td>
                          <td className="py-2.5 px-2 text-right num">{fmtScore(item.composite_score)}</td>
                          <td className="py-2.5 px-2 text-right">
                            <button
                              type="button"
                              onClick={() => setExpandedCode(isExpanded ? null : item.code)}
                              aria-expanded={isExpanded}
                              className="inline-flex flex-col items-end gap-1 group"
                            >
                              <span className="flex flex-wrap justify-end gap-1">
                                {item.algos.map((hit) => (
                                  <span
                                    key={hit.algo_code}
                                    className="text-[10px] font-mono uppercase px-1.5 py-0.5 rounded border border-border bg-background text-foreground"
                                    title={`${hit.algo_name} · 名次 ${hit.rank ?? '—'}`}
                                  >
                                    {hit.algo_code}
                                  </span>
                                ))}
                              </span>
                              <span className="text-[11px] text-brass-strong group-hover:underline dark:text-brass">
                                {isExpanded ? '收起指标' : '查看指标'}
                              </span>
                            </button>
                          </td>
                          <td className="py-2.5 pr-1 text-right">
                            <button
                              type="button"
                              onClick={() => handleWatch(item.code)}
                              aria-pressed={watched}
                              aria-label={watched ? `取消自选 ${item.name}` : `加入自选 ${item.name}`}
                              className={`inline-flex items-center gap-1 px-2 py-1 rounded-md border text-[11px] transition-colors ${
                                watched
                                  ? 'border-warning/50 bg-warning/10 text-warning'
                                  : 'border-border text-muted-foreground hover:bg-muted hover:text-foreground'
                              }`}
                            >
                              <Star className={`w-3.5 h-3.5 ${watched ? 'fill-current' : ''}`} />
                              {watched ? '已自选' : '自选'}
                            </button>
                          </td>
                        </tr>
                        {isExpanded && (
                          <tr className="border-b border-border/60 bg-muted/20">
                            <td colSpan={8} className="p-3">
                              <HitDetails item={item} onOpenAlgo={onOpenAlgo} />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}

          {watchHint && <p className="text-[11px] text-warning">⚠ {watchHint}</p>}

          <p className="border-t border-border/60 pt-3 text-[10px] leading-relaxed text-muted-foreground">
            各策略评分量纲不同，榜单按命中策略数排序，不做跨策略分数平均。
            共识只说明多策略同时选中，不代表更强收益。30 日走势为未复权收盘价，按个股缓存 5 分钟。
          </p>
        </div>
      )}

      {!open && (
        <p className="text-[11px] text-muted-foreground mt-3">
          各策略评分量纲不同，榜单按命中策略数排序，不做跨策略分数平均。
        </p>
      )}
    </section>
  )
}
