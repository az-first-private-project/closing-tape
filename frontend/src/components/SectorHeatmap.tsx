/**
 * 板块热力图（V6 T4 treemap）
 * ===========================
 * 面积 = 板块市值（可切等权），颜色 = 涨跌幅（±5% 饱和，红涨绿跌）。
 * - 布局由 lib/treemap.ts 的手写 squarified treemap 完成，不依赖 d3/echarts；
 * - 市值缺失/非正的板块剔除并计数，不用 0 伪造面积；
 * - 默认只渲染前 80 大，其余折叠为「其他」（点击展开），控制交互开销；
 * - 涨跌家数为 NULL（同花顺源）时显示「不适用」，绝不渲染成 0；
 * - 点击板块打开内嵌抽屉（复用 /market/sector/{code}），不新增路由；
 * - 仅容器层淡入，不做逐格 stagger；尊重 prefers-reduced-motion。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { ReactNode } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import { X } from 'lucide-react'
import { useSectorDetail, useSectors } from '../hooks/useSectors'
import type { LeaderStock, SectorQuote, SectorType } from '../types/market'
import { squarify } from '../lib/treemap'
import { trendHeat } from '../lib/color'
import { SkeletonChart, SkeletonLine } from './StateViews'
import {
  EMPTY_TEXT,
  fmtAmount,
  fmtNumber,
  fmtPct,
  pctColor,
  priceBgClass,
  trendGlyph,
} from '../lib/format'

const DEFAULT_TOP_N = 80
const OTHERS_ID = '__others__'
const LABEL_MIN_W = 56
const LABEL_MIN_H = 34
const TOOLTIP_W = 264
const TOOLTIP_H = 208

type AreaMode = 'cap' | 'equal'

interface HeatCell {
  id: string
  name: string
  value: number
  pct_chg: number | null
  sector: SectorQuote | null
  count?: number
}

function hasUsableCap(item: SectorQuote): item is SectorQuote & { market_cap: number } {
  return typeof item.market_cap === 'number' && Number.isFinite(item.market_cap) && item.market_cap > 0
}

/** 涨跌幅文本：null → 「—」；四舍五入后为 0 的显示 0.0%，避免「-0.0%」 */
function pctLabel(value: number | null, digits = 1): string {
  if (value === null || !Number.isFinite(value)) return EMPTY_TEXT
  if (Number(value.toFixed(digits)) === 0) return `${(0).toFixed(digits)}%`
  return `${trendGlyph(value)}${fmtPct(value, digits)}`
}

/** 涨跌家数：两端都缺（同花顺源）→「不适用」，单端缺失 →「—」 */
function countText(up: number | null, down: number | null): string {
  if (up === null && down === null) return '不适用'
  return `${up === null ? EMPTY_TEXT : up} / ${down === null ? EMPTY_TEXT : down} 家`
}

/** partial 名单提示：THS 只暴露前后各 5 页，名单只是样本 */
function partialLabel(count: number | null | undefined): string {
  return count != null ? `名单不完整 · 样本 ${count} 只` : '名单不完整 · 样本数未知'
}

/** 「其他」格子的加权涨跌幅：市值模式按市值加权，等权模式取简单平均 */
function aggregatePct(rest: SectorQuote[], byCap: boolean): number | null {
  let weighted = 0
  let weight = 0
  for (const item of rest) {
    if (item.pct_chg === null || !Number.isFinite(item.pct_chg)) continue
    const w = byCap && hasUsableCap(item) ? item.market_cap : 1
    weighted += item.pct_chg * w
    weight += w
  }
  return weight > 0 ? weighted / weight : null
}

/** 容器尺寸（treemap 需要像素级宽高；容器条件渲染，必须用 callback ref 重新观察） */
function useContainerSize() {
  const [node, setNode] = useState<HTMLDivElement | null>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })
  const ref = useCallback((element: HTMLDivElement | null) => setNode(element), [])
  useEffect(() => {
    if (!node) return
    const update = () => {
      const rect = node.getBoundingClientRect()
      setSize((prev) =>
        prev.width === rect.width && prev.height === rect.height
          ? prev
          : { width: rect.width, height: rect.height },
      )
    }
    update()
    const observer = new ResizeObserver(update)
    observer.observe(node)
    return () => observer.disconnect()
  }, [node])
  return { ref, node, width: size.width, height: size.height }
}

/** 分段切换按钮组 */
function Segment({
  value,
  options,
  onChange,
  label,
}: {
  value: string
  options: { value: string; label: string }[]
  onChange: (value: string) => void
  label: string
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className="inline-flex overflow-hidden rounded-lg border border-border/70 bg-muted/25 p-0.5"
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
          className={`rounded-[6px] px-2.5 py-1 text-[10px] font-medium transition-all duration-300 ${
            value === option.value
              ? 'bg-card text-brass-strong shadow-[0_1px_2px_rgba(0,0,0,0.08)] ring-1 ring-border/70 dark:text-brass'
              : 'text-muted-foreground hover:text-foreground'
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

function TooltipRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <span className="text-muted-foreground">{label}</span>
      <span className="num text-right">{children}</span>
    </>
  )
}

export default function SectorHeatmap({ tradeDate }: { tradeDate?: string }) {
  const [sectorType, setSectorType] = useState<SectorType>('industry')
  const [areaMode, setAreaMode] = useState<AreaMode>('cap')
  const [showAll, setShowAll] = useState(false)
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  const [pointer, setPointer] = useState({ x: 0, y: 0 })
  const [selected, setSelected] = useState<SectorQuote | null>(null)

  const sectorsQuery = useSectors(tradeDate, sectorType)
  const items = sectorsQuery.data?.items ?? []

  // 概念板块常缺市值：全缺时自动回退等权，避免整张图空白
  const hasCap = useMemo(() => items.some(hasUsableCap), [items])
  const autoFallback = areaMode === 'cap' && !hasCap
  const effectiveMode: AreaMode = autoFallback ? 'equal' : areaMode
  const capMode = effectiveMode === 'cap'

  const memberTotal = useMemo(
    () => items.reduce((sum, item) => sum + (item.member_count ?? 0), 0),
    [items],
  )
  const partialCount = useMemo(
    () => items.filter((item) => item.is_partial).length,
    [items],
  )

  const { cells, excluded, capCovered, folded } = useMemo(() => {
    const candidates: SectorQuote[] = capMode ? items.filter(hasUsableCap) : [...items]
    const excludedCount = capMode ? items.length - candidates.length : 0
    const sorted = [...candidates].sort((left, right) => {
      const capDiff = (right.market_cap ?? -1) - (left.market_cap ?? -1)
      return capDiff !== 0 ? capDiff : left.sector_name.localeCompare(right.sector_name, 'zh-CN')
    })
    const limit = showAll ? sorted.length : Math.min(DEFAULT_TOP_N, sorted.length)
    const visible = sorted.slice(0, limit)
    const rest = sorted.slice(limit)

    const list: HeatCell[] = visible.map((item) => ({
      id: item.sector_code,
      name: item.sector_name,
      value: capMode ? (item.market_cap ?? 0) : 1,
      pct_chg: item.pct_chg,
      sector: item,
    }))

    if (rest.length > 0) {
      const value = capMode
        ? rest.reduce((sum, item) => sum + (item.market_cap ?? 0), 0)
        : rest.length
      list.push({
        id: OTHERS_ID,
        name: `其他 ${rest.length} 个`,
        value,
        pct_chg: aggregatePct(rest, capMode),
        sector: null,
        count: rest.length,
      })
    }

    return {
      cells: list,
      excluded: excludedCount,
      capCovered: candidates.length,
      folded: rest.length,
    }
  }, [items, capMode, showAll])

  const { ref: containerRef, node: containerNode, width, height } = useContainerSize()
  const layout = useMemo(() => {
    if (!width || !height) return []
    return squarify(
      cells.map((cell) => ({ id: cell.id, value: cell.value })),
      { x: 0, y: 0, w: width, h: height },
    )
  }, [cells, width, height])
  const cellMap = useMemo(() => new Map(cells.map((cell) => [cell.id, cell])), [cells])
  const hoveredCell = hoveredId ? cellMap.get(hoveredId) ?? null : null
  const reducedMotion = useReducedMotion()

  // 指针位置按帧节流，避免 mousemove 触发整张图高频重渲染
  const pointerRef = useRef(pointer)
  const rafRef = useRef<number | null>(null)
  const updatePointer = (x: number, y: number) => {
    pointerRef.current = { x, y }
    if (rafRef.current !== null) return
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null
      setPointer(pointerRef.current)
    })
  }
  useEffect(
    () => () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
    },
    [],
  )

  useEffect(() => {
    setSelected(null)
    setHoveredId(null)
  }, [tradeDate, sectorType])

  const closeDrawer = useCallback(() => setSelected(null), [])

  const handlePointerMove = (event: React.MouseEvent<HTMLButtonElement>) => {
    if (!containerNode) return
    const rect = containerNode.getBoundingClientRect()
    updatePointer(event.clientX - rect.left, event.clientY - rect.top)
  }

  const handleTypeChange = (next: string) => {
    const nextType = next as SectorType
    if (nextType === sectorType) return
    setSectorType(nextType)
    setShowAll(false)
  }

  const typeLabel = sectorType === 'industry' ? '行业' : '概念'
  const tooltipLeft = Math.max(4, Math.min(pointer.x + 12, Math.max(4, width - TOOLTIP_W - 4)))
  const tooltipTop =
    pointer.y + 12 + TOOLTIP_H > height
      ? Math.max(4, pointer.y - TOOLTIP_H - 8)
      : pointer.y + 12

  return (
    <div className="card p-4">
      {/* ========== 标题栏 + 维度切换 ========== */}
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex items-baseline gap-2">
            <h3 className="text-[15px] font-semibold tracking-[-0.01em]">板块热力图</h3>
            <span className="font-mono text-[10px] tracking-[0.12em] text-muted-foreground">
              面积 = {capMode ? '市值' : '等权'} · ±5% 饱和
            </span>
          </div>
          <p className="mt-1 font-mono text-[10px] text-muted-foreground/85">
            {typeLabel} {sectorsQuery.data?.count ?? items.length} 个
            {capMode && ` · 市值覆盖 ${capCovered}/${items.length}`}
            {` · 成分合计 ${memberTotal} 家`}
            {partialCount > 0 && `（不含 ${partialCount} 个名单不完整板块）`}
            {folded > 0 && ` · 已展示前 ${DEFAULT_TOP_N} 大`}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Segment
            label="板块维度"
            value={sectorType}
            onChange={handleTypeChange}
            options={[
              { value: 'industry', label: '行业' },
              { value: 'concept', label: '概念' },
            ]}
          />
          <Segment
            label="面积维度"
            value={areaMode}
            onChange={(next) => setAreaMode(next as AreaMode)}
            options={[
              { value: 'cap', label: '市值' },
              { value: 'equal', label: '等权' },
            ]}
          />
          {folded > 0 || showAll ? (
            <button
              type="button"
              onClick={() => setShowAll((current) => !current)}
              className="btn btn-sm"
            >
              {showAll ? '只看前 80' : '显示全部'}
            </button>
          ) : null}
          {sectorsQuery.isFetching && (
            <span className="skeleton h-3.5 w-3.5 rounded-full" aria-label="加载中" />
          )}
        </div>
      </div>

      {autoFallback && (
        <p className="mb-2 rounded-md border border-warning/30 bg-warning/[0.07] px-2 py-1 text-[10px] text-warning">
          当前板块无市值数据，已自动回退等权面积（{items.length} 个板块）。
        </p>
      )}

      <motion.div
        initial={reducedMotion ? false : { opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.25 }}
        className="relative"
      >
        {sectorsQuery.isPending ? (
          <div className="skeleton h-[340px] rounded-lg sm:h-[400px]" />
        ) : sectorsQuery.isError ? (
          <div className="flex h-[340px] flex-col items-center justify-center gap-2 rounded-lg border border-border text-sm sm:h-[400px]">
            <p className="text-alert">板块数据加载失败</p>
            <button
              type="button"
              onClick={() => sectorsQuery.refetch()}
              className="rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted"
            >
              重试
            </button>
          </div>
        ) : items.length === 0 ? (
          <div className="flex h-[340px] items-center justify-center rounded-lg border border-border text-sm text-muted-foreground sm:h-[400px]">
            暂无板块行情数据
          </div>
        ) : (
          <div
            ref={containerRef}
            className="relative h-[340px] overflow-hidden rounded-lg sm:h-[400px]"
            onMouseLeave={() => setHoveredId(null)}
          >
            {layout.map((rect) => {
              const cell = cellMap.get(rect.id)
              if (!cell) return null
              const heat = trendHeat(cell.pct_chg)
              const isOthers = cell.id === OTHERS_ID
              const canLabel = rect.w >= LABEL_MIN_W && rect.h >= LABEL_MIN_H
              return (
                <button
                  key={cell.id}
                  type="button"
                  aria-label={
                    isOthers
                      ? `${cell.name}，点击显示全部`
                      : `${cell.name} ${pctLabel(cell.pct_chg, 2)}${
                          cell.sector?.is_partial ? '，名单不完整' : ''
                        }`
                  }
                  className="absolute overflow-hidden rounded-[3px] border border-card/80 text-left transition-shadow duration-200 hover:z-10 hover:ring-1 hover:ring-brass/90 focus-visible:z-10 focus-visible:ring-2 focus-visible:ring-brass"
                  style={{
                    left: rect.x,
                    top: rect.y,
                    width: rect.w,
                    height: rect.h,
                    backgroundColor: heat.background,
                    color: heat.color,
                  }}
                  onMouseEnter={() => setHoveredId(cell.id)}
                  onMouseMove={handlePointerMove}
                  onFocus={() => {
                    setHoveredId(cell.id)
                    setPointer({ x: rect.x + rect.w / 2, y: rect.y })
                  }}
                  onBlur={() => setHoveredId((current) => (current === cell.id ? null : current))}
                  onClick={() => {
                    if (isOthers) {
                      setShowAll(true)
                      setHoveredId(null)
                    } else if (cell.sector) {
                      setSelected(cell.sector)
                    }
                  }}
                >
                  {canLabel && (
                    <span className="flex h-full flex-col justify-between gap-0.5 p-1">
                      <span className="truncate text-[10.5px] leading-tight opacity-90">{cell.name}</span>
                      <span className="num text-[11px] font-semibold leading-tight">
                        {pctLabel(cell.pct_chg)}
                      </span>
                    </span>
                  )}
                </button>
              )
            })}

            {hoveredCell && (
              <div
                className="pointer-events-none absolute z-20 w-[264px] rounded-xl border border-border/80 bg-card/95 p-3 text-xs shadow-[var(--shadow-3)] backdrop-blur-md"
                style={{ left: tooltipLeft, top: tooltipTop }}
              >
                {hoveredCell.sector ? (
                  <SectorTooltip sector={hoveredCell.sector} />
                ) : (
                  <div className="space-y-1">
                    <p className="text-sm font-medium">{hoveredCell.name}</p>
                    <p className="text-muted-foreground">
                      合计涨跌幅 <span className={`num ${pctColor(hoveredCell.pct_chg)}`}>{pctLabel(hoveredCell.pct_chg, 2)}</span>
                    </p>
                    <p className="text-muted-foreground">点击展开全部板块</p>
                  </div>
                )}
              </div>
            )}

            {excluded > 0 && (
              <span className="absolute bottom-1.5 right-1.5 z-10 rounded border border-border bg-card/90 px-1.5 py-0.5 text-[10px] text-muted-foreground">
                已剔除 {excluded} 个（数据不足）
              </span>
            )}
          </div>
        )}
      </motion.div>

      {/* ========== 图例 ========== */}
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-border/60 pt-2.5 text-[10px] text-muted-foreground">
        <div className="flex items-center gap-2">
          <span className="text-down">跌</span>
          <span
            className="h-1.5 w-24 rounded-full ring-1 ring-border/60"
            style={{
              background: `linear-gradient(90deg, ${trendHeat(-5).background}, ${trendHeat(-2.5).background}, ${trendHeat(0).background}, ${trendHeat(2.5).background}, ${trendHeat(5).background})`,
            }}
          />
          <span className="text-up">涨</span>
          <span className="text-muted-foreground/70">色阶 ±5% 饱和</span>
        </div>
        <span className="text-muted-foreground/70">
          面积 = {capMode ? '成分股市值合计' : '等权（每板块同面积）'} · 点击板块看详情
        </span>
      </div>

      {selected &&
        createPortal(
          <SectorDrawer sector={selected} tradeDate={tradeDate} onClose={closeDrawer} />,
          document.body,
        )}
    </div>
  )
}

/** 单个板块的悬浮提示 */
function SectorTooltip({ sector }: { sector: SectorQuote }) {
  const scored = [sector.technical_score, sector.sentiment_score, sector.news_score].some(
    (score) => score !== null,
  )
  const scoreText = scored
    ? `技 ${sector.technical_score === null ? '—' : sector.technical_score.toFixed(1)} · 情 ${
        sector.sentiment_score === null ? '—' : sector.sentiment_score.toFixed(1)
      } · 消 ${sector.news_score === null ? '—' : sector.news_score.toFixed(1)}`
    : '未评分'

  return (
    <>
      <p className="text-sm font-medium">
        {sector.sector_name}
        <span className="ml-1.5 text-[11px] font-normal text-muted-foreground">
          {sector.sector_code}
        </span>
      </p>
      {sector.is_partial && (
        <p className="mt-1.5 rounded border border-warning/30 bg-warning/[0.07] px-1.5 py-1 text-[10px] leading-snug text-warning">
          {partialLabel(sector.partial_member_count)}，涨跌家数与市值不可用
        </p>
      )}
      <div className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-2 gap-y-1">
        <TooltipRow label="涨跌幅">
          <span className={pctColor(sector.pct_chg)}>{pctLabel(sector.pct_chg, 2)}</span>
        </TooltipRow>
        <TooltipRow label="主力净流入">{fmtAmount(sector.main_net_inflow)}</TooltipRow>
        <TooltipRow label="20日相对300">{fmtPct(sector.rs_20d)}</TooltipRow>
        <TooltipRow label="涨跌家数">{countText(sector.up_count, sector.down_count)}</TooltipRow>
        <TooltipRow label="龙头">
          {sector.leading_stock
            ? `${sector.leading_stock} ${fmtPct(sector.leading_stock_pct, 1)}`
            : EMPTY_TEXT}
        </TooltipRow>
        <TooltipRow label="三面评分">{scoreText}</TooltipRow>
      </div>
      <p className="mt-1.5 border-t border-border pt-1.5 text-[10px] text-muted-foreground">
        {sector.is_partial
          ? `面积 不适用 · ${partialLabel(sector.partial_member_count)}`
          : `面积 ${fmtAmount(sector.market_cap)} · 成分 ${
              sector.member_count != null ? `${sector.member_count} 家` : EMPTY_TEXT
            }`}
        {sector.composite_score !== null && ` · 综合 ${sector.composite_score.toFixed(1)}`}
      </p>
    </>
  )
}

/** 板块详情抽屉（复用 3.2 接口，不新增路由） */
function SectorDrawer({
  sector,
  tradeDate,
  onClose,
}: {
  sector: SectorQuote
  tradeDate?: string
  onClose: () => void
}) {
  const detailQuery = useSectorDetail(sector.sector_code, tradeDate)
  const detail = detailQuery.data
  const isPartial = detail?.is_partial ?? sector.is_partial
  const partialSample = detail?.partial_member_count ?? sector.partial_member_count

  useEffect(() => {
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} aria-hidden="true" />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={`${sector.sector_name} 板块详情`}
        className="relative flex h-full w-full max-w-md flex-col overflow-hidden border-l border-border bg-card/98 shadow-[var(--shadow-3)] backdrop-blur-md"
      >
        <header className="border-b border-border/70 p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <span className="eyebrow">Sector Detail</span>
              <h3 className="mt-1.5 flex flex-wrap items-baseline gap-2 text-[17px] font-semibold tracking-[-0.015em]">
                {sector.sector_name}
                <span className="num text-[10px] font-normal text-muted-foreground">
                  {sector.sector_code}
                </span>
                {isPartial && (
                  <span className="rounded border border-warning/40 bg-warning/[0.08] px-1.5 py-0.5 text-[9px] font-normal text-warning">
                    名单不完整
                  </span>
                )}
              </h3>
              <p className="mt-1 text-[10px] text-muted-foreground">
                {sector.sector_type === 'concept' ? '概念板块' : '行业板块'} · 交易日{' '}
                {detail?.trade_date ?? tradeDate ?? '最新'}
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label="关闭"
              className="rounded-lg border border-transparent p-1.5 text-muted-foreground transition-colors hover:border-border hover:bg-muted hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          {detailQuery.isPending && (
            <div className="space-y-3" aria-busy="true">
              <div className="space-y-2">
                <SkeletonLine className="h-5 w-40" />
                <SkeletonLine className="h-3 w-64 max-w-full" />
              </div>
              <SkeletonChart height={180} />
              <SkeletonLine className="h-3 w-full" />
              <SkeletonLine className="h-3 w-4/5" />
            </div>
          )}
          {detailQuery.isError && (
            <div className="space-y-2 py-10 text-center text-sm">
              <p className="text-alert">
                板块详情加载失败：
                {detailQuery.error instanceof Error ? detailQuery.error.message : '请求失败'}
              </p>
              <button
                type="button"
                onClick={() => detailQuery.refetch()}
                className="rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted"
              >
                重试
              </button>
            </div>
          )}
          {detail && (
            <>
              {isPartial && (
                <p className="rounded-lg border border-warning/30 bg-warning/[0.07] px-2.5 py-2 text-[11px] leading-relaxed text-warning">
                  {partialLabel(partialSample)}：同花顺只暴露前后各 5 页，名单仅为样本；
                  涨跌家数、市值等聚合字段不可用。
                </p>
              )}
              <section className="grid grid-cols-2 gap-2">
                <Stat label="涨跌幅">
                  <span className={pctColor(detail.market_data.pct_chg)}>
                    {pctLabel(detail.market_data.pct_chg, 2)}
                  </span>
                </Stat>
                <Stat label="收盘">{fmtNumber(detail.market_data.close, 2)}</Stat>
                <Stat label="成交额">{fmtAmount(detail.market_data.amount)}</Stat>
                <Stat label="换手率">
                  {detail.market_data.turnover_rate === null
                    ? EMPTY_TEXT
                    : `${detail.market_data.turnover_rate.toFixed(2)}%`}
                </Stat>
                <Stat label="涨跌家数">
                  {countText(detail.market_data.up_count, detail.market_data.down_count)}
                </Stat>
                <Stat label="龙头">
                  {detail.market_data.leading_stock
                    ? `${detail.market_data.leading_stock} ${fmtPct(detail.market_data.leading_stock_pct, 1)}`
                    : EMPTY_TEXT}
                </Stat>
              </section>

              <section>
                <h4 className="mb-2 font-mono text-[10px] tracking-[0.14em] text-muted-foreground">
                  三面评分（0–10）
                </h4>
                <div className="grid grid-cols-4 gap-2 text-center">
                  {[
                    { label: '技术', value: detail.scores.technical },
                    { label: '情绪', value: detail.scores.sentiment },
                    { label: '消息', value: detail.scores.news },
                    { label: '综合', value: detail.scores.composite },
                  ].map((score, index) => (
                    <div
                      key={score.label}
                      className={`rounded-lg border p-2.5 ${
                        index === 3
                          ? 'border-brass/40 bg-brass/[0.06]'
                          : 'border-border/60 bg-muted/25'
                      }`}
                    >
                      <p className="text-[10px] text-muted-foreground">{score.label}</p>
                      <p className="num mt-1 text-[13px] font-semibold">
                        {score.value === null ? EMPTY_TEXT : score.value.toFixed(1)}
                      </p>
                    </div>
                  ))}
                </div>
              </section>

              <LeaderList title="强势龙头" leaders={detail.leaders.strength} />
              <LeaderList title="情绪龙头" leaders={detail.leaders.sentiment} />

              <section>
                <h4 className="mb-1.5 text-xs font-medium text-muted-foreground">
                  {isPartial ? '样本成分股' : '成分股'}（{detail.members.length}）
                </h4>
                {detail.members.length === 0 ? (
                  <p className="text-xs text-muted-foreground">名单为空</p>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {detail.members.slice(0, 40).map((member) => (
                      <span
                        key={member.code}
                        title={`${member.code} ${fmtPct(member.pct_chg)}`}
                        className={`rounded border px-1.5 py-0.5 text-[11px] num ${priceBgClass(member.pct_chg)}`}
                      >
                        {member.name || member.code} {fmtPct(member.pct_chg, 1)}
                      </span>
                    ))}
                  </div>
                )}
              </section>
            </>
          )}
        </div>
      </aside>
    </div>
  )
}

function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="rounded-lg border border-border/60 bg-muted/25 px-2.5 py-2">
      <p className="text-[10px] text-muted-foreground">{label}</p>
      <p className="num mt-1 text-[12px] font-medium">{children}</p>
    </div>
  )
}

function LeaderList({ title, leaders }: { title: string; leaders: LeaderStock[] }) {
  if (!leaders.length) return null
  return (
    <section>
      <h4 className="mb-1.5 font-mono text-[10px] tracking-[0.14em] text-muted-foreground">{title}</h4>
      <ul>
        {leaders.slice(0, 5).map((leader) => (
          <li
            key={leader.code}
            className="flex items-center justify-between gap-2 rounded-md px-1.5 py-1.5 text-[12px] transition-colors hover:bg-muted/50"
          >
            <span className="min-w-0 truncate">
              {leader.name}
              <span className="num ml-1.5 text-[10px] text-muted-foreground">{leader.code}</span>
            </span>
            <span className={`num shrink-0 ${pctColor(leader.change_pct)}`}>
              {fmtPct(leader.change_pct, 1)}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}
