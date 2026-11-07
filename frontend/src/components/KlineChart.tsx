/**
 * 个股 K 线（lightweight-charts v5）
 * ===================================
 * - 蜡烛图 + 成交量副图（同一图表，量能压在下 20%）；
 * - MA5/MA20/MA60 与 BOLL 叠加，勾选状态存 localStorage；
 * - 价格轴口径：后端 `adjust_basis="raw"`，K 线与 MA/BOLL 均为未复权价；
 *   回测 `trades[].price` 也是未复权成交价，因此标记与图表同口径，不做二次换算。
 * - 回测标记按交易日聚合：买=向上三角、卖=向下三角、shares=0 的被拒单=灰色圆点。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type LineData,
  type SeriesMarker,
  type Time,
  type WhitespaceData,
} from 'lightweight-charts'
import type { BacktestTrade } from '../types/backtest'
import type { KlineItem } from '../types/stock'
import { fmtAmount, fmtInt, fmtNumber, fmtPct, fmtVolume, priceColorClass, trendGlyph } from '../lib/format'
import { useTheme } from './ThemeProvider'
import { EmptyState } from './StateViews'

export type KlineAdjustBasis = 'raw' | 'hfq'

interface KlineChartProps {
  code: string
  items: KlineItem[]
  adjustBasis?: KlineAdjustBasis
  trades?: BacktestTrade[]
  btId?: string
}

type OverlayKey = 'ma5' | 'ma20' | 'ma60' | 'boll'
type Overlays = Record<OverlayKey, boolean>

const OVERLAY_STORAGE_KEY = 'gfna-kline-overlays'
const OVERLAY_KEYS: OverlayKey[] = ['ma5', 'ma20', 'ma60', 'boll']
const DEFAULT_OVERLAYS: Overlays = { ma5: true, ma20: true, ma60: true, boll: false }
const RANGES = [60, 120, 250] as const
const CHART_HEIGHT = 400

const MA_KEYS = ['ma5', 'ma20', 'ma60'] as const
const MA_COLORS: Record<(typeof MA_KEYS)[number], string> = {
  ma5: '#d3a55f',
  ma20: '#6fbfab',
  ma60: '#83aacb',
}
const BOLL_COLOR = '#948f84'

interface DayTrades {
  buys: BacktestTrade[]
  sells: BacktestTrade[]
  rejected: BacktestTrade[]
}

interface HoverInfo {
  x: number
  y: number
  item: KlineItem
}

interface ChartColors {
  up: string
  down: string
  muted: string
  border: string
}

function cssColor(name: string, fallback = '#64748b'): string {
  if (typeof window === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

function readColors(): ChartColors {
  return {
    up: cssColor('--color-up'),
    down: cssColor('--color-down'),
    muted: cssColor('--color-muted-foreground'),
    border: cssColor('--color-border'),
  }
}

/** 坐标轴/十字光标数字使用设计系统等宽字体 */
function readMonoFont(): string {
  if (typeof window === 'undefined') return 'monospace'
  const mono = getComputedStyle(document.documentElement).getPropertyValue('--font-mono').trim()
  return mono || getComputedStyle(document.body).fontFamily
}

function withAlpha(color: string, alpha: number): string {
  const hex = color.trim()
  if (/^#[0-9a-f]{6}$/i.test(hex)) {
    return `${hex}${Math.round(alpha * 255).toString(16).padStart(2, '0')}`
  }
  return hex
}

function loadOverlays(): Overlays {
  const result: Overlays = { ...DEFAULT_OVERLAYS }
  try {
    const raw = localStorage.getItem(OVERLAY_STORAGE_KEY)
    if (!raw) return result
    const parsed = JSON.parse(raw) as Partial<Record<OverlayKey, unknown>>
    for (const key of OVERLAY_KEYS) {
      const value = parsed[key]
      if (typeof value === 'boolean') result[key] = value
    }
  } catch {
    // 隐私模式 / 非法 JSON：退回默认值，不影响图表
  }
  return result
}

function timeToDateString(time: Time | undefined): string | null {
  if (time === undefined || time === null) return null
  if (typeof time === 'string') return time
  if (typeof time === 'number') return new Date(time * 1000).toISOString().slice(0, 10)
  return `${time.year}-${String(time.month).padStart(2, '0')}-${String(time.day).padStart(2, '0')}`
}

function groupTradesByDate(trades: BacktestTrade[]): Map<string, DayTrades> {
  const grouped = new Map<string, DayTrades>()
  for (const trade of trades) {
    const date = trade.date
    if (!date) continue
    let day = grouped.get(date)
    if (!day) {
      day = { buys: [], sells: [], rejected: [] }
      grouped.set(date, day)
    }
    if (!trade.shares || trade.shares <= 0) day.rejected.push(trade)
    else if (trade.side === 'buy') day.buys.push(trade)
    else if (trade.side === 'sell') day.sells.push(trade)
  }
  return grouped
}

const REASON_LABELS: Record<string, string> = {
  rebalance: '调仓成交',
  limit_up: '涨停无法买入',
  limit_down: '跌停无法卖出',
  suspended: '停牌无法成交',
  no_data: '无行情数据',
  below_one_lot: '不足一手',
  insufficient_cash: '可用资金不足',
}

function reasonLabel(reason: string | null | undefined): string {
  if (!reason) return '—'
  return REASON_LABELS[reason] ?? reason
}

function buildLineData(
  items: KlineItem[],
  key: keyof KlineItem,
): (LineData<Time> | WhitespaceData<Time>)[] {
  return items.map((item) => {
    const value = item[key]
    return typeof value === 'number' && Number.isFinite(value)
      ? { time: item.trade_date, value }
      : { time: item.trade_date }
  })
}

export default function KlineChart({
  code,
  items,
  adjustBasis = 'raw',
  trades = [],
  btId,
}: KlineChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const { theme } = useTheme()
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const maRefs = useRef<Partial<Record<(typeof MA_KEYS)[number], ISeriesApi<'Line'>>>>({})
  const bollRefs = useRef<Partial<Record<'mid' | 'upper' | 'lower', ISeriesApi<'Line'>>>>({})
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const itemsMapRef = useRef<Map<string, KlineItem>>(new Map())
  const tradesMapRef = useRef<Map<string, DayTrades>>(new Map())

  const [range, setRange] = useState<(typeof RANGES)[number]>(250)
  const [overlays, setOverlays] = useState<Overlays>(loadOverlays)
  const [hover, setHover] = useState<HoverInfo | null>(null)
  const [pinnedDate, setPinnedDate] = useState<string | null>(null)
  const [containerWidth, setContainerWidth] = useState(0)
  const [colorEpoch, setColorEpoch] = useState(0)

  // 主题切换后显式触发重绘（不能只依赖 CSS：canvas 需要重新读取变量值）
  useEffect(() => {
    setColorEpoch((value) => value + 1)
  }, [theme])

  const visibleItems = useMemo(() => items.slice(-range), [items, range])
  const tradesByDate = useMemo(() => groupTradesByDate(trades), [trades])
  const pinnedDay = pinnedDate ? tradesByDate.get(pinnedDate) ?? null : null
  const pinnedItem = pinnedDate ? visibleItems.find((item) => item.trade_date === pinnedDate) ?? null : null
  const hoverDay = hover ? tradesByDate.get(hover.item.trade_date) ?? null : null

  // ---------------------------------------------------------------
  // 图表生命周期：只创建一次；StrictMode 双挂载靠 remove() + innerHTML 兜底
  // ---------------------------------------------------------------
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const colors = readColors()
    container.innerHTML = ''

    const chart = createChart(container, {
      width: Math.max(280, container.clientWidth || 0),
      height: CHART_HEIGHT,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: colors.muted,
        fontSize: 11,
        fontFamily: readMonoFont(),
      },
      grid: {
        vertLines: { color: colors.border, style: LineStyle.Dotted },
        horzLines: { color: colors.border },
      },
      rightPriceScale: {
        borderColor: colors.border,
        scaleMargins: { top: 0.05, bottom: 0.25 },
      },
      timeScale: {
        borderColor: colors.border,
        rightOffset: 4,
        barSpacing: 6,
        minBarSpacing: 0.5,
        timeVisible: false,
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: colors.muted,
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: colors.muted,
        },
        horzLine: {
          color: colors.muted,
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: colors.muted,
        },
      },
      localization: {
        locale: 'zh-CN',
        priceFormatter: (price: number) => price.toFixed(2),
      },
      handleScroll: { mouseWheel: true, pressedMouseMove: true },
      handleScale: { mouseWheel: true, pinch: true },
    })

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: colors.up,
      downColor: colors.down,
      borderUpColor: colors.up,
      borderDownColor: colors.down,
      wickUpColor: colors.up,
      wickDownColor: colors.down,
      priceLineVisible: false,
      lastValueVisible: true,
    })

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
      priceLineVisible: false,
      lastValueVisible: false,
    })
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
      visible: false,
    })

    maRefs.current = {
      ma5: chart.addSeries(LineSeries, {
        color: MA_COLORS.ma5,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        visible: false,
      }),
      ma20: chart.addSeries(LineSeries, {
        color: MA_COLORS.ma20,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        visible: false,
      }),
      ma60: chart.addSeries(LineSeries, {
        color: MA_COLORS.ma60,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        visible: false,
      }),
    }
    bollRefs.current = {
      mid: chart.addSeries(LineSeries, {
        color: BOLL_COLOR,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        visible: false,
      }),
      upper: chart.addSeries(LineSeries, {
        color: BOLL_COLOR,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        visible: false,
      }),
      lower: chart.addSeries(LineSeries, {
        color: BOLL_COLOR,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        visible: false,
      }),
    }

    markersRef.current = createSeriesMarkers(candleSeries, [])

    chart.subscribeCrosshairMove((param) => {
      if (!param.point || !param.time) {
        setHover(null)
        return
      }
      const date = timeToDateString(param.time)
      const item = date ? itemsMapRef.current.get(date) : undefined
      if (!item) {
        setHover(null)
        return
      }
      setHover({ x: param.point.x, y: param.point.y, item })
    })

    chart.subscribeClick((param) => {
      if (!param.time) return
      const date = timeToDateString(param.time)
      if (!date) return
      const day = tradesMapRef.current.get(date)
      if (!day) {
        setPinnedDate(null)
        chart.clearCrosshairPosition()
        return
      }
      setPinnedDate(date)
      const item = itemsMapRef.current.get(date)
      if (item?.close != null) {
        chart.setCrosshairPosition(item.close, param.time, candleSeries)
      }
    })

    const handleDblClick = () => chart.timeScale().fitContent()
    container.addEventListener('dblclick', handleDblClick)

    const resizeObserver = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0
      if (width <= 0) return
      setContainerWidth(width)
      chart.applyOptions({ width: Math.max(280, width), height: CHART_HEIGHT })
    })
    resizeObserver.observe(container)

    // ThemeProvider 只切 .dark class，图表颜色必须在 class 变化后重新读取 CSS 变量
    const themeObserver = new MutationObserver(() => setColorEpoch((value) => value + 1))
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })

    chartRef.current = chart
    candleRef.current = candleSeries
    volumeRef.current = volumeSeries

    return () => {
      resizeObserver.disconnect()
      themeObserver.disconnect()
      container.removeEventListener('dblclick', handleDblClick)
      chart.remove()
      chartRef.current = null
      candleRef.current = null
      volumeRef.current = null
      markersRef.current = null
      maRefs.current = {}
      bollRefs.current = {}
    }
  }, [])

  // 颜色随主题重绘（K 线/量能/文字/网格）
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const colors = readColors()
    chart.applyOptions({
      layout: { textColor: colors.muted },
      grid: {
        vertLines: { color: colors.border, style: LineStyle.Dotted },
        horzLines: { color: colors.border },
      },
      rightPriceScale: { borderColor: colors.border },
      timeScale: { borderColor: colors.border },
      crosshair: {
        vertLine: { color: colors.muted, labelBackgroundColor: colors.muted },
        horzLine: { color: colors.muted, labelBackgroundColor: colors.muted },
      },
    })
    candleRef.current?.applyOptions({
      upColor: colors.up,
      downColor: colors.down,
      borderUpColor: colors.up,
      borderDownColor: colors.down,
      wickUpColor: colors.up,
      wickDownColor: colors.down,
    })
    bollRefs.current.mid?.applyOptions({ color: colors.muted })
    bollRefs.current.upper?.applyOptions({ color: colors.muted })
    bollRefs.current.lower?.applyOptions({ color: colors.muted })
  }, [colorEpoch])

  // 数据：蜡烛 + 量能 + MA/BOLL
  useEffect(() => {
    const chart = chartRef.current
    const candle = candleRef.current
    if (!chart || !candle) return
    const colors = readColors()
    const map = new Map<string, KlineItem>()
    const candles: CandlestickData<Time>[] = []
    const volumes: HistogramData<Time>[] = []
    const volumeAlpha = withAlpha(colors.up, 0.35)
    const volumeDownAlpha = withAlpha(colors.down, 0.35)

    for (const item of visibleItems) {
      map.set(item.trade_date, item)
      if (item.open == null || item.high == null || item.low == null || item.close == null) continue
      candles.push({
        time: item.trade_date,
        open: item.open,
        high: item.high,
        low: item.low,
        close: item.close,
      })
      if (item.volume != null && item.volume > 0) {
        volumes.push({
          time: item.trade_date,
          value: item.volume,
          color: item.close >= item.open ? volumeAlpha : volumeDownAlpha,
        })
      }
    }

    itemsMapRef.current = map
    candle.setData(candles)
    volumeRef.current?.setData(volumes)
    for (const key of MA_KEYS) {
      maRefs.current[key]?.setData(buildLineData(visibleItems, key))
    }
    bollRefs.current.mid?.setData(buildLineData(visibleItems, 'boll_mid'))
    bollRefs.current.upper?.setData(buildLineData(visibleItems, 'boll_upper'))
    bollRefs.current.lower?.setData(buildLineData(visibleItems, 'boll_lower'))
    chart.timeScale().fitContent()
  }, [visibleItems, colorEpoch])

  // 叠加开关
  useEffect(() => {
    for (const key of MA_KEYS) {
      maRefs.current[key]?.applyOptions({ visible: overlays[key] })
    }
    bollRefs.current.mid?.applyOptions({ visible: overlays.boll })
    bollRefs.current.upper?.applyOptions({ visible: overlays.boll })
    bollRefs.current.lower?.applyOptions({ visible: overlays.boll })
    try {
      localStorage.setItem(OVERLAY_STORAGE_KEY, JSON.stringify(overlays))
    } catch {
      // 隐私模式下不持久化，开关仍在当前会话生效
    }
  }, [overlays])

  // 回测标记：按交易日聚合，拒单单独灰点
  useEffect(() => {
    const markers = markersRef.current
    if (!markers) return
    const colors = readColors()
    tradesMapRef.current = tradesByDate
    const visibleDates = new Set(visibleItems.map((item) => item.trade_date))
    const list: SeriesMarker<Time>[] = []
    const dates = [...tradesByDate.keys()].filter((date) => visibleDates.has(date)).sort()

    for (const date of dates) {
      const day = tradesByDate.get(date)
      if (!day) continue
      const isPinned = pinnedDate === date
      if (day.buys.length > 0) {
        list.push({
          time: date,
          position: 'belowBar',
          shape: 'arrowUp',
          color: colors.up,
          text: day.buys.length > 1 ? `买${day.buys.length}` : '买',
          size: isPinned ? 2 : 1,
          id: `buy-${date}`,
        })
      }
      if (day.sells.length > 0) {
        list.push({
          time: date,
          position: 'aboveBar',
          shape: 'arrowDown',
          color: colors.down,
          text: day.sells.length > 1 ? `卖${day.sells.length}` : '卖',
          size: isPinned ? 2 : 1,
          id: `sell-${date}`,
        })
      }
      if (day.rejected.length > 0) {
        list.push({
          time: date,
          position: 'aboveBar',
          shape: 'circle',
          color: colors.muted,
          text: day.rejected.length > 1 ? `拒${day.rejected.length}` : '拒',
          size: isPinned ? 2 : 1,
          id: `rejected-${date}`,
        })
      }
    }
    markers.setMarkers(list)
  }, [tradesByDate, visibleItems, pinnedDate, colorEpoch])

  const toggleOverlay = (key: OverlayKey) => {
    setOverlays((current) => ({ ...current, [key]: !current[key] }))
  }

  const hasAnyData = visibleItems.some((item) => item.close != null)
  const tooltipLeft = Math.max(
    4,
    Math.min(hover ? hover.x + 12 : 4, Math.max(280, containerWidth) - 224),
  )

  return (
    <div className="card p-4">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="eyebrow">Daily Candle</span>
            <span className="rounded border border-border/70 bg-muted/40 px-1.5 py-0.5 text-[9px] text-muted-foreground">
              {adjustBasis === 'hfq' ? '后复权' : '未复权'}
            </span>
            {btId && (
              <span className="rounded border border-brass/40 bg-brass/[0.08] px-1.5 py-0.5 text-[9px] text-brass-strong dark:text-brass">
                回测标记
              </span>
            )}
          </div>
          <h3 className="mt-1.5 text-[14px] font-semibold">日线 K 线</h3>
          <p className="mt-1 font-mono text-[10px] text-muted-foreground">
            {visibleItems.length > 0
              ? `${visibleItems[0].trade_date} ~ ${visibleItems[visibleItems.length - 1].trade_date} · ${visibleItems.length} 个交易日`
              : '暂无数据'}
            {' · '}滚轮缩放 / 拖拽平移 / 双击复位
          </p>
        </div>
        <div className="flex items-center gap-0.5 rounded-lg border border-border/70 bg-muted/25 p-0.5">
          {RANGES.map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => setRange(value)}
              className={`rounded-[6px] px-2.5 py-1 text-[10px] font-medium transition-all duration-300 ${
                range === value
                  ? 'bg-card text-foreground shadow-[0_1px_2px_rgba(0,0,0,0.08)] ring-1 ring-border/70'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              {value} 根
            </button>
          ))}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 mb-2 text-[11px]">
        {(
          [
            ['ma5', 'MA5', MA_COLORS.ma5],
            ['ma20', 'MA20', MA_COLORS.ma20],
            ['ma60', 'MA60', MA_COLORS.ma60],
            ['boll', 'BOLL(20,2)', BOLL_COLOR],
          ] as const
        ).map(([key, label, color]) => (
          <label key={key} className="inline-flex items-center gap-1.5 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={overlays[key]}
              onChange={() => toggleOverlay(key)}
              className="h-3 w-3 accent-[var(--color-brass)]"
            />
            <span className="inline-block h-0.5 w-4 rounded" style={{ backgroundColor: color }} />
            <span className={overlays[key] ? 'text-foreground' : 'text-muted-foreground'}>{label}</span>
          </label>
        ))}
        <span className="text-muted-foreground/80">
          买卖点来自回测成交；灰色圆点为「信号命中但无法成交」
        </span>
      </div>

      <div ref={containerRef} className="relative w-full" style={{ height: CHART_HEIGHT }}>
        {!hasAnyData && (
          <div className="absolute inset-0 z-10 flex items-center justify-center">
            <EmptyState
              title="暂无 K 线数据"
              description="该股票在所选区间内没有有效 OHLC 行情。"
              className="border-0 bg-card/80"
            />
          </div>
        )}

        {hover && (
          <div
            className="pointer-events-none absolute z-20 w-[212px] rounded-lg border border-border bg-card p-2.5 text-[11px] shadow-lg"
            style={{ left: tooltipLeft, top: 8 }}
          >
            <div className="flex items-center justify-between gap-2 mb-1">
              <span className="font-mono text-muted-foreground">{hover.item.trade_date}</span>
              <span className={`font-semibold num ${priceColorClass(hover.item.pct_chg)}`}>
                {trendGlyph(hover.item.pct_chg)} {fmtPct(hover.item.pct_chg)}
              </span>
            </div>
            <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 num">
              <span className="text-muted-foreground">开</span>
              <span className="text-right">{fmtNumber(hover.item.open, 2)}</span>
              <span className="text-muted-foreground">高</span>
              <span className="text-right">{fmtNumber(hover.item.high, 2)}</span>
              <span className="text-muted-foreground">低</span>
              <span className="text-right">{fmtNumber(hover.item.low, 2)}</span>
              <span className="text-muted-foreground">收</span>
              <span className="text-right">{fmtNumber(hover.item.close, 2)}</span>
              <span className="text-muted-foreground">成交量</span>
              <span className="text-right">{fmtVolume(hover.item.volume)}</span>
              <span className="text-muted-foreground">成交额</span>
              <span className="text-right">{fmtAmount(hover.item.amount)}</span>
            </div>
            {hoverDay && (
              <p className="mt-1.5 pt-1.5 border-t border-border/60 text-muted-foreground">
                回测：买 {hoverDay.buys.length} · 卖 {hoverDay.sells.length} · 拒{' '}
                {hoverDay.rejected.length}
              </p>
            )}
          </div>
        )}
      </div>

      {pinnedDate && pinnedDay && pinnedItem && (
        <div className="mt-3 rounded-lg border border-border bg-muted/30 p-3 text-[11px]">
          <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
            <p className="font-medium text-xs">
              回测成交 · <span className="font-mono">{pinnedDate}</span> · {code}
            </p>
            <button
              type="button"
              onClick={() => setPinnedDate(null)}
              className="text-muted-foreground hover:text-foreground"
            >
              收起
            </button>
          </div>
          <div className="space-y-1.5">
            {[...pinnedDay.buys, ...pinnedDay.sells, ...pinnedDay.rejected].map((trade, index) => {
              const rejected = !trade.shares || trade.shares <= 0
              const sideLabel = rejected ? '拒单' : trade.side === 'buy' ? '买入' : '卖出'
              const sideClass = rejected
                ? 'text-muted-foreground'
                : trade.side === 'buy'
                  ? 'text-up'
                  : 'text-down'
              return (
                <div
                  key={`${trade.date}-${trade.code}-${trade.side}-${index}`}
                  className="flex flex-wrap items-center gap-x-3 gap-y-1 num"
                >
                  <span className={`font-semibold ${sideClass}`}>{sideLabel}</span>
                  <span className="text-muted-foreground">
                    价 {fmtNumber(trade.price, 2)}
                  </span>
                  <span>{fmtInt(trade.shares)} 股</span>
                  <span>金额 {fmtAmount(trade.amount)}</span>
                  <span>费用 {fmtNumber(trade.fee, 2)}</span>
                  <span className="text-muted-foreground">{reasonLabel(trade.reason)}</span>
                </div>
              )
            })}
          </div>
          <p className="mt-2 text-[11px] text-muted-foreground">
            当日 OHLC：开 {fmtNumber(pinnedItem.open, 2)} / 高 {fmtNumber(pinnedItem.high, 2)} / 低{' '}
            {fmtNumber(pinnedItem.low, 2)} / 收 {fmtNumber(pinnedItem.close, 2)}。成交价为未复权口径
            （含滑点），与图表一致。
          </p>
        </div>
      )}
    </div>
  )
}
