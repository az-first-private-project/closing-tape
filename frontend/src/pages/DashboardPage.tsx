/**
 * 盘后分析页（原 DashboardV3 并入，契约第 3 节）
 * ================================================
 * 页面结构：
 *  1. 顶部：标题 + 交易日选择（可回看历史）+ 刷新 + 数据新鲜度徽标；
 *  2. 指数卡片行：上证 / 深证 / 创业板 / 科创50 / 沪深300（美股若返回则展示）；
 *  3. 融资融券概览：北向资金为空时显示「暂停披露」；
 *  4. 板块热力图 + 真实综合评分榜；
 *  5. 领涨 / 领跌 Top5（含龙头与综合分）；
 *  6. AI 复盘报告（结构化渲染后端 Markdown）。
 */
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Activity, RefreshCw } from 'lucide-react'
import { useDashboard, useIndexSeries, useTradeCalendar } from '../hooks/useDashboard'
import { useWatchSignals, useWatchlist } from '../hooks/useWatchSignals'
import WatchButton from '../components/WatchButton'
import WatchlistPanel from '../components/WatchlistPanel'
import IndexCard, {
  IndexCompareHeader,
  computeIndexRanks,
  isIndexWarming,
  type IndexCardData,
} from '../components/IndexCard'
import SectorHeatmap from '../components/SectorHeatmap'
import ScoreRanking from '../components/ScoreRanking'
import AIReport from '../components/AIReport'
import MarketTemperature from '../components/MarketTemperature'
import FreshnessBadge from '../components/FreshnessBadge'
import {
  EmptyState,
  ErrorState,
  SkeletonChart,
  SkeletonKpiRow,
  SkeletonLine,
} from '../components/StateViews'
import { useDelayedFlag } from '../hooks/useDelayedFlag'
import type { IndexQuote, MarginData, SectorQuote, UsIndexQuote } from '../types/market'
import { fmtPct, fmtScore, fmtYi, pctColor } from '../lib/format'

/** 核心指数展示顺序：上证/深证/创业板/科创50/沪深300/中证500/中证1000，其余按后端顺序追加 */
const PREFERRED_INDEX_ORDER = [
  'sh000001',
  'sz399001',
  'sz399006',
  'sh000688',
  'sh000300',
  'sh000905',
  'sh000852',
]

/** 按优先顺序排列指数 */
function orderIndices(indices: IndexQuote[]): IndexQuote[] {
  return [...indices].sort((left, right) => {
    const leftIndex = PREFERRED_INDEX_ORDER.indexOf(left.index_code)
    const rightIndex = PREFERRED_INDEX_ORDER.indexOf(right.index_code)
    if (leftIndex === -1 && rightIndex === -1) return 0
    if (leftIndex === -1) return 1
    if (rightIndex === -1) return -1
    return leftIndex - rightIndex
  })
}

/** A 股指数 → 卡片数据 */
function toIndexCard(index: IndexQuote): IndexCardData {
  return {
    index_code: index.index_code,
    index_name: index.index_name,
    close: index.close,
    pct_chg: index.pct_chg,
    high: index.high,
    low: index.low,
    ret_1d: index.ret_1d,
    ret_5d: index.ret_5d,
    ret_20d: index.ret_20d,
    ret_60d: index.ret_60d,
    ret_ytd: index.ret_ytd,
    rel_300_20d: index.rel_300_20d,
    rel_300_ytd: index.rel_300_ytd,
  }
}

/** 美股指数 → 卡片数据（美股无当日高低，展示交易日；不参与相对300） */
function toUsIndexCard(index: UsIndexQuote): IndexCardData {
  return {
    index_code: index.index_code,
    index_name: index.index_name,
    close: index.close,
    pct_chg: index.pct_chg,
    ret_1d: index.ret_1d,
    ret_5d: index.ret_5d,
    ret_20d: index.ret_20d,
    ret_60d: index.ret_60d,
    ret_ytd: index.ret_ytd,
    subtitle: index.trade_date ? `美股 ${index.trade_date}` : null,
  }
}

/** 统计徽标 */
function StatBadge({
  label,
  value,
  sub,
  tone = 'neutral',
}: {
  label: string
  value: string
  sub?: string
  tone?: 'up' | 'down' | 'neutral'
}) {
  const color = tone === 'up' ? 'text-up' : tone === 'down' ? 'text-down' : 'text-foreground'
  return (
    <div className="card p-3 text-center" title={sub}>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={`text-sm font-semibold mt-1 num ${color}`}>{value}</p>
      {sub && <p className="text-[11px] text-muted-foreground mt-0.5 truncate">{sub}</p>}
    </div>
  )
}

/** 融资融券概览：北向资金为空时显示「暂停披露」 */
function MarginOverview({ margin }: { margin: MarginData | null }) {
  if (!margin) {
    return (
      <div className="card p-4 text-sm text-muted-foreground">
        融资融券数据暂无（采集任务可能尚未运行，见「任务监控」）。
      </div>
    )
  }

  const change = margin.margin_change ?? null
  const northward = margin.northward_net ?? null

  // 后端无两融数据时返回 {}，这里按字段判断而不是按对象真值判断
  const hasAnyValue = change !== null || northward !== null ||
    margin.margin_balance !== null || margin.short_balance !== null
  if (!hasAnyValue) {
    return (
      <div className="card p-4 text-sm text-muted-foreground">
        暂无融资融券数据（该交易日采集任务可能未成功，见「任务监控」）。
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-sm">融资融券概览</h3>
        {margin.trade_date && <span className="text-[11px] text-muted-foreground">数据日 {margin.trade_date}</span>}
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatBadge label="融资余额" value={fmtYi(margin.margin_balance)} />
        <StatBadge label="融券余额" value={fmtYi(margin.short_balance)} />
        <StatBadge
          label="融资变化"
          value={change === null ? '—' : `${change >= 0 ? '+' : ''}${fmtYi(change)}`}
          tone={change === null ? 'neutral' : change >= 0 ? 'up' : 'down'}
        />
        {northward === null ? (
          <StatBadge label="北向资金" value="暂停披露" sub="北向资金已停止实时披露" />
        ) : (
          <StatBadge
            label="北向资金"
            value={`${northward >= 0 ? '净流入 ' : '净流出 '}${fmtYi(Math.abs(northward))}`}
            tone={northward >= 0 ? 'up' : 'down'}
          />
        )}
      </div>
    </div>
  )
}

/** 领涨 / 领跌板块 Top5（含龙头、综合分） */
function SectorRankList({ title, sectors }: { title: string; sectors: SectorQuote[] }) {
  const list = sectors.slice(0, 5)
  if (list.length === 0) return null

  return (
    <div className="card p-4">
      <h3 className="font-semibold mb-3">{title}</h3>
      <div className="space-y-2">
        {list.map((sector, index) => {
          const strengthLeader = sector.strength_leaders?.[0]
          const sentimentLeader = sector.sentiment_leaders?.[0]
          return (
            <div key={sector.sector_code} className="p-2 rounded-lg hover:bg-muted transition-colors">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-xs text-muted-foreground w-4 shrink-0">{index + 1}</span>
                  <span className="text-sm font-medium truncate">{sector.sector_name}</span>
                  {sector.leading_stock && (
                    <span className="text-[11px] text-muted-foreground truncate">
                      龙头 {sector.leading_stock}
                      {sector.leading_stock_pct !== null && ` ${fmtPct(sector.leading_stock_pct)}`}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-3 shrink-0">
                  <span className="text-xs text-muted-foreground num">
                    {sector.composite_score === null ? '未评分' : `${fmtScore(sector.composite_score)}/10`}
                  </span>
                  <span className={`text-sm font-semibold num ${pctColor(sector.pct_chg)}`}>
                    {fmtPct(sector.pct_chg)}
                  </span>
                </div>
              </div>

              {/* 实力龙头 / 情绪龙头 */}
              {(strengthLeader || sentimentLeader) && (
                <div className="flex flex-wrap gap-1.5 mt-1.5 ml-6">
                  {strengthLeader && (
                    <span
                      className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border border-border bg-muted/60"
                      title={strengthLeader.rank_text ?? undefined}
                    >
                      实力龙头 {strengthLeader.name}
                      <span className={`num ${pctColor(strengthLeader.change_pct)}`}>{fmtPct(strengthLeader.change_pct)}</span>
                      <WatchButton code={strengthLeader.code} name={strengthLeader.name} />
                    </span>
                  )}
                  {sentimentLeader && (
                    <span
                      className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border border-border bg-muted/60"
                      title={sentimentLeader.rank_text ?? undefined}
                    >
                      情绪龙头 {sentimentLeader.name}
                      <span className={`num ${pctColor(sentimentLeader.change_pct)}`}>{fmtPct(sentimentLeader.change_pct)}</span>
                      <WatchButton code={sentimentLeader.code} name={sentimentLeader.name} />
                    </span>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default function DashboardPage() {
  /** 空字符串 = 最新交易日；否则为指定交易日（历史回看） */
  const [selectedDate, setSelectedDate] = useState('')
  const dashboard = useDashboard(selectedDate || undefined)
  const calendar = useTradeCalendar(720)
  const watchlist = useWatchlist()
  const watchHits = useWatchSignals(watchlist)

  const data = dashboard.data
  const showLoadingSkeleton = useDelayedFlag(dashboard.isLoading && !data)
  const indexSeries = useIndexSeries(60, selectedDate || undefined, !!data)

  /** 交易日历：只保留开市日，倒序（最新在前） */
  const openDays = useMemo(() => {
    const days = calendar.data?.days ?? []
    return days
      .filter((day) => day.is_open)
      .map((day) => day.cal_date)
      .sort()
      .reverse()
  }, [calendar.data])

  // ---------- 加载 / 错误 ----------
  if (dashboard.isLoading && !data) {
    return (
      <div
        className="max-w-7xl mx-auto px-4 py-6 space-y-6"
        aria-busy="true"
        style={{ visibility: showLoadingSkeleton ? 'visible' : 'hidden' }}
      >
        <div className="space-y-2">
          <SkeletonLine className="h-7 w-56" />
          <SkeletonLine className="h-3 w-80 max-w-full" />
        </div>
        <SkeletonKpiRow count={4} />
        <SkeletonChart height={260} />
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <SkeletonChart height={280} />
          <SkeletonChart height={280} />
        </div>
      </div>
    )
  }

  if (dashboard.isError && !data) {
    return (
      <div className="max-w-7xl mx-auto px-4 py-6">
        <ErrorState title="盘后分析数据加载失败" error={dashboard.error} onRetry={() => dashboard.refetch()} />
      </div>
    )
  }

  if (!data) {
    return (
      <div className="max-w-7xl mx-auto px-4 py-6">
        <EmptyState title="暂无盘后分析数据" description="请到「任务监控」查看采集任务状态。" />
      </div>
    )
  }

  // 契约规定这些字段必返；前端仍做一次防御性归一化，避免个别字段缺失导致整页白屏
  const indices = data.indices ?? []
  const usIndices = data.us_indices ?? []
  const sectors = data.sectors ?? []
  const topSectors = data.top_sectors ?? []
  const bottomSectors = data.bottom_sectors ?? []
  const freshness = data.freshness ?? []

  const hasMarketData = indices.length > 0 || sectors.length > 0
  const scoredCount = data.stats?.scored_sectors ?? sectors.filter((sector) => sector.composite_score !== null).length
  const totalSectors = data.stats?.total_sectors ?? sectors.length

  const seriesMap = indexSeries.data?.series ?? {}
  const sparkFor = (code: string) =>
    seriesMap[code]?.map((point) => point.close).slice(-30)

  const indexCards = orderIndices(indices).map((index) => ({
    ...toIndexCard(index),
    spark: sparkFor(index.index_code),
  }))
  const usCards = usIndices.map((index) => ({
    ...toUsIndexCard(index),
    spark: sparkFor(index.index_code),
  }))
  const indexRanks = computeIndexRanks(indexCards)
  const hasIndexWarming = indexCards.some(isIndexWarming)

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3 }}
      className="max-w-7xl mx-auto px-4 py-6 space-y-6"
    >
      {/* ========== 标题栏 + 日期选择 ========== */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-[28px] font-bold">📊 GFNA 盘后分析</h1>
          <p className="text-sm text-muted-foreground mt-1">
            交易日 {data.trade_date} · {indices.length} 指数 · {sectors.length} 板块 · 评分覆盖{' '}
            {scoredCount}/{totalSectors}
            {(data.stats?.signal_count ?? 0) > 0 && ` · 当日信号 ${data.stats.signal_count} 条`}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {/* 日期选择器：可回看历史交易日 */}
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            交易日
            <select
              value={selectedDate}
              onChange={(event) => setSelectedDate(event.target.value)}
              className="px-2.5 py-2 rounded-lg border border-border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
            >
              <option value="">最新交易日</option>
              {openDays.map((day) => (
                <option key={day} value={day}>
                  {day}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={() => dashboard.refetch()}
            disabled={dashboard.isFetching}
            className="inline-flex items-center gap-1.5 px-3 py-2 border border-border rounded-lg text-sm hover:bg-muted transition-colors disabled:opacity-50"
            title="重新加载当前交易日数据"
          >
            <RefreshCw className="w-4 h-4" />
            刷新{dashboard.isFetching ? '中…' : ''}
          </button>
        </div>
      </div>

      {/* 刷新失败但仍展示旧数据 */}
      {dashboard.isError && (
        <ErrorState
          title="刷新失败（以下为上次成功数据）"
          error={dashboard.error}
          onRetry={() => dashboard.refetch()}
          className="py-4"
        />
      )}

      {/* ========== 数据新鲜度 ========== */}
      <FreshnessBadge items={freshness} title="数据新鲜度" />

      {/* ========== 无数据引导 ========== */}
      {!hasMarketData ? (
        <EmptyState
          title="该交易日暂无可展示的行情数据"
          description={
            <span>
              {data.empty_reason ? `${data.empty_reason}。` : ''}
              可能是采集任务尚未完成或该日不是交易日。可到{' '}
              <Link to="/jobs" className="text-primary hover:underline">
                任务监控
              </Link>{' '}
              查看采集状态，或在上方切换到其他交易日回看。
            </span>
          }
        />
      ) : (
        <>
          {/* ========== 市场温度计（V6 T3） ========== */}
          <MarketTemperature tradeDate={selectedDate || undefined} />

          {/* ========== 自选股快捷区（V6 T8） ========== */}
          {watchlist.length > 0 && (
            <WatchlistPanel
              watchlist={watchlist}
              hits={watchHits.data?.items ?? []}
              latestSignalDate={watchHits.data?.latest_signal_date ?? null}
              loading={watchHits.isLoading}
              error={watchHits.isError ? watchHits.error : undefined}
              onRetry={() => watchHits.refetch()}
              compact
            />
          )}

          {/* ========== 指数多周期对比条 ========== */}
          {indexCards.length > 0 && (
            <div className="card overflow-hidden">
              <div className="overflow-x-auto scrollbar-thin">
                <div className="min-w-[860px]">
                  <IndexCompareHeader />
                  {indexCards.map((index, position) => (
                    <IndexCard
                      key={index.index_code}
                      index={{ ...index, ranks: indexRanks[index.index_code] }}
                      order={position}
                    />
                  ))}
                </div>
              </div>
              <p className="px-3 py-2 text-[11px] text-muted-foreground border-t border-border/60">
                周期按交易日计算；小字为同周期 A 股指数排名；相对300 单位为百分点。
                {hasIndexWarming && (
                  <span className="ml-2 text-warning">
                    部分新纳入指数（中证500 / 中证1000）历史回补中，缺失周期显示 “—”。
                  </span>
                )}
              </p>
            </div>
          )}

          {/* 美股指数（若返回则展示） */}
          {usCards.length > 0 && (
            <div>
              <p className="text-xs text-muted-foreground mb-2">美股指数（T-1 收盘）</p>
              <div className="card overflow-hidden">
                <div className="overflow-x-auto scrollbar-thin">
                  <div className="min-w-[860px]">
                    <IndexCompareHeader showRelative={false} />
                    {usCards.map((index, position) => (
                      <IndexCard
                        key={index.index_code}
                        index={index}
                        showRelative={false}
                        order={position}
                      />
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* ========== 融资融券概览 ========== */}
          <MarginOverview margin={data.margin_data} />

          {/* ========== 热力图 + 评分榜 ========== */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <SectorHeatmap tradeDate={data.trade_date} />
            <ScoreRanking sectors={sectors} stats={data.stats} />
          </div>

          {/* ========== 领涨 / 领跌 Top5 ========== */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <SectorRankList title="🚀 领涨板块 Top 5" sectors={topSectors} />
            <SectorRankList title="📉 领跌板块 Top 5" sectors={bottomSectors} />
          </div>

          {/* ========== AI 复盘报告 ========== */}
          <AIReport report={data.report} />
        </>
      )}

      {/* 免责声明 */}
      <div className="card p-4 text-xs text-muted-foreground space-y-1">
        <p className="flex items-center gap-1.5">
          <Activity className="w-3.5 h-3.5" />
          数据来自本地行情库，页面展示以「数据新鲜度」标注的采集时间为准。
        </p>
        <p className="flex items-center gap-1.5">
          <Activity className="w-3.5 h-3.5" />
          颜色遵循 A 股习惯：红涨绿跌。量化评分与 AI 报告不构成投资建议。
        </p>
      </div>
    </motion.div>
  )
}
