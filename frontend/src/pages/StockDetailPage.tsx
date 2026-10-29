/**
 * 个股详情页（契约第 4 节）
 * ==========================
 * - K 线：lightweight-charts 蜡烛图 + MA/BOLL + 成交量 + 回测买卖标记；
 * - 指标卡：RSI/MACD/ATR/波动率/量比/动量等；
 * - 因子雷达：6 维 0–10 分；
 * - 所属板块、最近信号（该股是否出现在各算法最近一次 run 中）。
 */
import { lazy, Suspense, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ArrowLeft, CalendarDays, Radio, RefreshCw } from 'lucide-react'
import { useKline, useStock, useStockSignals } from '../hooks/useStock'
import { useBacktestTrades } from '../hooks/useBacktest'
import { useTradeCalendar } from '../hooks/useDashboard'
import { rowScore } from '../types/algo'
import type { BacktestTrade } from '../types/backtest'
import type { StockFeatures, StockLatest } from '../types/stock'
import type { Freshness } from '../types/common'
import AlgoRadarChart from '../components/AlgoRadarChart'
import FreshnessBadge from '../components/FreshnessBadge'
import { ErrorState, LoadingState, EmptyState } from '../components/StateViews'
import {
  fmtAmount,
  fmtDate,
  fmtNumber,
  fmtPct,
  fmtScore,
  fmtVolume,
  trendGlyph,
  priceColorClass,
} from '../lib/format'

/** 图表库单独分包：只有个股详情页需要 lightweight-charts，避免拖累首屏 */
const KlineChart = lazy(() => import('../components/KlineChart'))

/** K 线骨架：T13 统一骨架屏前的过渡实现 */
function KlineSkeleton() {
  return (
    <div className="card p-4 space-y-3">
      <div className="skeleton h-5 w-48" />
      <div className="skeleton h-[360px] w-full" />
      <p className="text-[11px] text-muted-foreground">正在加载 K 线…</p>
    </div>
  )
}

/** 小型信息项 */
function InfoItem({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2 text-xs">
      <span className="text-muted-foreground shrink-0">{label}</span>
      <span className={`num font-medium ${color ?? ''}`}>{value}</span>
    </div>
  )
}

/** 指标卡 */
function IndicatorCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="card p-3">
      <p className="text-[11px] text-muted-foreground">{label}</p>
      <p className="mt-1 text-sm font-semibold num">{value}</p>
      {hint && <p className="text-[11px] text-muted-foreground mt-0.5">{hint}</p>}
    </div>
  )
}

/** 行情明细（latest 为所选交易日或最近可用交易日） */
function LatestQuote({ latest }: { latest: StockLatest }) {
  return (
    <div className="card p-4">
      <h3 className="font-semibold text-sm mb-3">行情快照（{latest.trade_date}）</h3>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-x-6 gap-y-2">
        <InfoItem label="开盘" value={fmtNumber(latest.open, 2)} />
        <InfoItem label="最高" value={fmtNumber(latest.high, 2)} />
        <InfoItem label="最低" value={fmtNumber(latest.low, 2)} />
        <InfoItem label="昨收" value={fmtNumber(latest.pre_close, 2)} />
        <InfoItem label="成交量" value={fmtVolume(latest.volume)} />
        <InfoItem label="成交额" value={fmtAmount(latest.amount)} />
        <InfoItem label="换手率" value={latest.turnover_rate === null ? '—' : `${latest.turnover_rate.toFixed(2)}%`} />
        <InfoItem label="总市值" value={fmtAmount(latest.market_cap)} />
        <InfoItem label="PE(TTM)" value={latest.pe_ttm === null ? '—' : latest.pe_ttm < 0 ? '亏损' : latest.pe_ttm.toFixed(2)} />
        <InfoItem label="PB" value={fmtNumber(latest.pb, 2)} />
        <InfoItem label="PS(TTM)" value={fmtNumber(latest.ps_ttm, 2)} />
        <InfoItem label="股息率" value={latest.dv_ttm === null ? '—' : `${latest.dv_ttm.toFixed(2)}%`} />
      </div>
    </div>
  )
}

/** 技术指标卡组 */
function FeaturesPanel({ features, latest }: { features: StockFeatures; latest: StockLatest | null }) {
  return (
    <div className="card p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-sm">技术指标</h3>
        <div className="flex items-center gap-1.5">
          {features.ma_bullish !== null && (
            <span
              className={`text-[11px] px-2 py-0.5 rounded-full border ${
                features.ma_bullish
                  ? 'border-up/40 bg-up/10 text-up-strong'
                  : 'border-border bg-muted text-muted-foreground'
              }`}
            >
              {features.ma_bullish ? '均线多头' : '均线非多头'}
            </span>
          )}
          {features.macd_golden_cross !== null && (
            <span
              className={`text-[11px] px-2 py-0.5 rounded-full border ${
                features.macd_golden_cross
                  ? 'border-up/40 bg-up/10 text-up-strong'
                  : 'border-border bg-muted text-muted-foreground'
              }`}
            >
              {features.macd_golden_cross ? 'MACD 金叉' : 'MACD 无金叉'}
            </span>
          )}
        </div>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <IndicatorCard label="RSI14" value={fmtNumber(features.rsi14, 2)} hint={latest ? `收盘 ${fmtNumber(latest.close, 2)}` : undefined} />
        <IndicatorCard
          label="MACD"
          value={features.macd_dif === null ? '—' : `DIF ${features.macd_dif.toFixed(3)}`}
          hint={`DEA ${fmtNumber(features.macd_dea, 3)} · 柱 ${fmtNumber(features.macd_hist, 3)}`}
        />
        <IndicatorCard label="ATR14" value={fmtNumber(features.atr14, 3)} hint="平均真实波幅" />
        <IndicatorCard
          label="20日波动率"
          value={features.volatility_20 === null ? '—' : `${features.volatility_20.toFixed(2)}%`}
        />
        <IndicatorCard label="MA5" value={fmtNumber(features.ma5, 2)} />
        <IndicatorCard label="MA20" value={fmtNumber(features.ma20, 2)} />
        <IndicatorCard label="20日动量" value={fmtPct(features.momentum_20)} />
        <IndicatorCard label="量比" value={fmtNumber(features.volume_ratio, 2)} />
      </div>
    </div>
  )
}

export default function StockDetailPage() {
  const { code = '' } = useParams<{ code: string }>()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  /** 空字符串 = 最新交易日；否则为历史回看日期 */
  const [selectedDate, setSelectedDate] = useState('')
  /** ?bt=<bt_id>：从回测面板跳转过来时在 K 线上叠加买卖标记 */
  const btId = searchParams.get('bt') || ''

  const stockQuery = useStock(code, selectedDate || undefined)
  const klineQuery = useKline(code, 250, selectedDate || undefined)
  const signalsQuery = useStockSignals(code)
  const tradesQuery = useBacktestTrades(btId, code)
  const calendar = useTradeCalendar(1100)

  /** 交易日历：只保留开市日，倒序（最新在前） */
  const openDays = useMemo(() => {
    const days = calendar.data?.days ?? []
    return days
      .filter((day) => day.is_open)
      .map((day) => day.cal_date)
      .sort()
      .reverse()
  }, [calendar.data])

  const tradeItems: BacktestTrade[] = tradesQuery.data?.items ?? []
  const klineDates = useMemo(
    () => new Set((klineQuery.data?.items ?? []).map((item) => item.trade_date)),
    [klineQuery.data],
  )
  const tradesOutsideRange = useMemo(
    () => tradeItems.filter((trade) => !klineDates.has(trade.date)).length,
    [tradeItems, klineDates],
  )

  const clearBacktestMarkers = () => {
    const next = new URLSearchParams(searchParams)
    next.delete('bt')
    setSearchParams(next, { replace: true })
  }

  const stock = stockQuery.data

  if (stockQuery.isLoading) {
    return (
      <div className="max-w-7xl mx-auto px-4 py-6">
        <LoadingState text="正在加载个股信息…" />
      </div>
    )
  }

  if (stockQuery.isError || !stock) {
    return (
      <div className="max-w-7xl mx-auto px-4 py-6 space-y-4">
        <ErrorState title="个股信息加载失败" error={stockQuery.error} onRetry={() => stockQuery.refetch()} />
        <button onClick={() => navigate(-1)} className="text-sm text-primary hover:underline">
          ← 返回上一页
        </button>
      </div>
    )
  }

  const latest = stock.latest
  const features = stock.features
  const scores = stock.scores
  // 防御性归一化：契约必返，但字段缺失时不应白屏
  const stockSectors = stock.sectors ?? []
  // 后端 freshness 返回数组、契约写单个对象，两种都兼容
  const stockFreshness: Freshness[] = Array.isArray(stock.freshness)
    ? stock.freshness
    : stock.freshness
      ? [stock.freshness]
      : []

  /** 因子雷达 6 维（0–10） */
  const radarDimensions = scores
    ? [
        { dimension: '价值', value: scores.value },
        { dimension: '质量', value: scores.quality },
        { dimension: '动量', value: scores.momentum },
        { dimension: '波动', value: scores.volatility },
        { dimension: '流动性', value: scores.liquidity },
        { dimension: '情绪', value: scores.sentiment },
      ]
    : []

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3 }}
      className="max-w-7xl mx-auto px-4 py-6 space-y-6"
    >
      {/* 返回 + 历史日期选择 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Link to="/algo-center" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition-colors">
          <ArrowLeft className="w-4 h-4" />
          返回选股中心
        </Link>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <CalendarDays className="w-3.5 h-3.5" />
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
          {selectedDate && (
            <button
              onClick={() => setSelectedDate('')}
              className="inline-flex items-center gap-1.5 px-3 py-2 border border-border rounded-lg text-sm hover:bg-muted transition-colors"
            >
              <RefreshCw className="w-3.5 h-3.5" />
              回到最新
            </button>
          )}
          {stockQuery.isFetching && <span className="text-[11px] text-muted-foreground">加载中…</span>}
        </div>
      </div>

      {/* ========== 标题 + 价格 ========== */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-[28px] font-bold">{stock.name}</h1>
            <span className="font-mono text-sm text-muted-foreground">{stock.code}</span>
            {stock.is_st && (
              <span className="text-[11px] px-2 py-0.5 rounded-full bg-alert/10 text-alert">
                ST
              </span>
            )}
            {latest?.is_suspended && (
              <span className="text-[11px] px-2 py-0.5 rounded-full bg-warning/10 text-warning">
                停牌
              </span>
            )}
            {latest?.is_limit_up && (
              <span className="text-[11px] px-2 py-0.5 rounded-full bg-up/10 text-up-strong">
                涨停
              </span>
            )}
            {latest?.is_limit_down && (
              <span className="text-[11px] px-2 py-0.5 rounded-full bg-down/10 text-down-strong">
                跌停
              </span>
            )}
          </div>
          <p className="text-xs text-muted-foreground mt-1">
            {[stock.exchange, stock.board, stock.industry_l1, stock.industry_l2, stock.ownership]
              .filter(Boolean)
              .join(' · ')}
            {stock.list_date && ` · 上市 ${stock.list_date}`}
          </p>
        </div>

        {latest && (
          <div className="text-right">
            <p className={`text-[28px] font-bold num ${priceColorClass(latest.pct_chg)}`}>
              {fmtNumber(latest.close, 2)}
            </p>
            <p className={`text-sm font-semibold num ${priceColorClass(latest.pct_chg)}`}>
              {trendGlyph(latest.pct_chg)} {fmtPct(latest.pct_chg)}
            </p>
          </div>
        )}
      </div>

      {/* 新鲜度 */}
      <FreshnessBadge items={stockFreshness} title="数据新鲜度" />

      {/* ========== 行情快照 ========== */}
      {latest ? (
        <LatestQuote latest={latest} />
      ) : (
        <EmptyState
          title="该交易日无行情数据"
          description={
            selectedDate
              ? `所选交易日 ${selectedDate} 没有该股的行情记录（可能是停牌、未上市或数据未覆盖）。可在上方切换到其他交易日。`
              : '数据库中暂无该股的行情数据。'
          }
        />
      )}
      {selectedDate && stock.trade_date && stock.trade_date !== selectedDate && (
        <p className="text-xs text-warning">
          所选交易日 {selectedDate} 无该股行情，已显示之前最近的交易日 {stock.trade_date}。
        </p>
      )}

      {/* ========== 技术指标 ========== */}
      {features && <FeaturesPanel features={features} latest={latest} />}

      {/* ========== K 线 ========== */}
      {btId && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2 text-xs">
          <span className="text-muted-foreground">
            回测标记 <span className="font-mono text-foreground">{btId.slice(0, 12)}</span>
            {tradesQuery.isLoading
              ? ' · 正在加载成交…'
              : tradesQuery.isError
                ? ' · 成交加载失败'
                : ` · ${(tradesQuery.data?.algo_code ?? '').toUpperCase()} · 本股成交 ${tradesQuery.data?.count ?? 0} 条`}
            {tradesOutsideRange > 0 && ` · ${tradesOutsideRange} 条不在当前 K 线区间`}
          </span>
          <div className="flex items-center gap-3">
            {tradesQuery.isError && (
              <button onClick={() => tradesQuery.refetch()} className="text-primary hover:underline">
                重试
              </button>
            )}
            <button onClick={clearBacktestMarkers} className="text-muted-foreground hover:text-foreground">
              清除标记
            </button>
          </div>
        </div>
      )}

      {klineQuery.isLoading ? (
        <KlineSkeleton />
      ) : klineQuery.isError ? (
        <ErrorState title="K 线加载失败" error={klineQuery.error} onRetry={() => klineQuery.refetch()} />
      ) : (
        <Suspense fallback={<KlineSkeleton />}>
          <KlineChart
            code={code}
            items={klineQuery.data?.items ?? []}
            adjustBasis={klineQuery.data?.adjust_basis ?? 'raw'}
            trades={tradeItems}
            btId={btId || undefined}
          />
        </Suspense>
      )}

      {/* ========== 因子雷达 + 所属板块 ========== */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {scores && radarDimensions.length > 0 && (
          <div className="space-y-2">
            <AlgoRadarChart title="🎯 因子雷达（0–10）" data={radarDimensions} />
            <p className="text-xs text-muted-foreground text-right">
              综合得分 <span className="font-semibold text-foreground num">{fmtScore(scores.composite)}</span>/10
            </p>
          </div>
        )}

        <div className="card p-4">
          <h3 className="font-semibold text-sm mb-3">所属板块</h3>
          {stockSectors.length === 0 ? (
            <p className="text-sm text-muted-foreground">暂无板块归属数据</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {stockSectors.map((sector) => (
                <span key={sector.sector_code} className="text-xs px-2.5 py-1 rounded-full border border-border bg-muted/50">
                  {sector.sector_name}
                  <span className="ml-1 text-muted-foreground font-mono text-[11px]">{sector.sector_code}</span>
                </span>
              ))}
            </div>
          )}

          {/* 最近信号 */}
          <h3 className="font-semibold text-sm mt-5 mb-3 flex items-center gap-1.5">
            <Radio className="w-4 h-4 text-primary" />
            最近信号
          </h3>
          {signalsQuery.isLoading ? (
            <p className="text-sm text-muted-foreground">正在检查各算法最近一次 run…</p>
          ) : signalsQuery.signals.length === 0 ? (
            <p className="text-sm text-muted-foreground">无（该股未出现在各算法最近一次 run 的选股结果中）</p>
          ) : (
            <ul className="space-y-2">
              {signalsQuery.signals.map((signal) => (
                <li key={signal.algoCode} className="flex items-center justify-between gap-2 text-sm p-2 rounded-lg bg-muted/40">
                  <div className="min-w-0">
                    <p className="truncate font-medium">{signal.algoName}</p>
                    <p className="text-[11px] text-muted-foreground">
                      run {signal.tradeDate ?? '—'} · 排名 {signal.row.rank ?? '—'}
                    </p>
                  </div>
                  <span className="text-sm font-semibold num shrink-0">
                    {rowScore(signal.row) === null ? '未评分' : fmtScore(rowScore(signal.row))}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-3 text-[11px] text-muted-foreground">
            信号时间 {fmtDate(latest?.trade_date)} · 若要看完整选股结果请到「选股中心」按算法执行。
          </p>
        </div>
      </div>

      {/* 免责声明 */}
      <div className="card p-4 text-xs text-muted-foreground">
        本页数据来自本地行情库；指标与因子评分仅作量化研究参考，不构成投资建议。最近信号为各算法最近一次运行的静态快照。
      </div>
    </motion.div>
  )
}
