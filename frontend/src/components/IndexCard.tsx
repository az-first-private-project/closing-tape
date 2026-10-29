/**
 * 指数对比条（契约 3.1 indices / us_indices）
 * ============================================
 * 一行一个指数：名称/收盘 + 1/5/20/60 日与年内收益（含同期排名）+ 相对300 徽章。
 * 颜色遵循 A 股口径：红涨绿跌；缺失数据一律显示 "—"，不拿 0 冒充。
 */
import { motion } from 'framer-motion'
import { fmtNumber, fmtPct, pctColor, EMPTY_TEXT } from '../lib/format'

export type IndexPeriodKey = 'ret_1d' | 'ret_5d' | 'ret_20d' | 'ret_60d' | 'ret_ytd'

export interface IndexPeriodRank {
  rank: number
  total: number
}

/** 指数渲染数据（A 股与美股统一成这个结构） */
export interface IndexCardData {
  index_code: string
  index_name: string
  close: number | null
  pct_chg: number | null
  high?: number | null
  low?: number | null
  ret_1d?: number | null
  ret_5d?: number | null
  ret_20d?: number | null
  ret_60d?: number | null
  ret_ytd?: number | null
  rel_300_20d?: number | null
  rel_300_ytd?: number | null
  /** 同周期内 A 股指数间的名次（美股不传） */
  ranks?: Partial<Record<IndexPeriodKey, IndexPeriodRank>>
  /** 底部补充说明（如美股交易日） */
  subtitle?: string | null
}

const PERIODS: { key: IndexPeriodKey; label: string }[] = [
  { key: 'ret_1d', label: '1日' },
  { key: 'ret_5d', label: '5日' },
  { key: 'ret_20d', label: '20日' },
  { key: 'ret_60d', label: '60日' },
  { key: 'ret_ytd', label: '年内' },
]

/** 表头与数据行必须共用同一套列宽，否则右侧徽章会错位 */
const ROW_GRID =
  'grid grid-cols-[minmax(150px,1.4fr)_repeat(5,minmax(72px,0.8fr))_minmax(160px,1.2fr)] items-center gap-x-2'

/** 同周期内按收益降序排名；缺失周期不参与排名 */
export function computeIndexRanks(
  items: IndexCardData[],
): Record<string, Partial<Record<IndexPeriodKey, IndexPeriodRank>>> {
  const result: Record<string, Partial<Record<IndexPeriodKey, IndexPeriodRank>>> = {}
  for (const period of PERIODS) {
    const valid = items.filter((item) => typeof item[period.key] === 'number')
    const sorted = [...valid].sort(
      (left, right) => (right[period.key] as number) - (left[period.key] as number),
    )
    sorted.forEach((item, position) => {
      result[item.index_code] = {
        ...(result[item.index_code] ?? {}),
        [period.key]: { rank: position + 1, total: valid.length },
      }
    })
  }
  return result
}

/** 所有周期都缺失 → 视为历史尚未回补（不是"全 0"） */
export function isIndexWarming(index: IndexCardData): boolean {
  return PERIODS.every((period) => typeof index[period.key] !== 'number')
}

function fmtRelative(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return EMPTY_TEXT
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}pp`
}

function relativeBadgeClass(value: number | null | undefined): string {
  return pctColor(value)
}

export function IndexCompareHeader({ showRelative = true }: { showRelative?: boolean }) {
  return (
    <div
      className={`${ROW_GRID} px-3 py-2 text-[11px] text-muted-foreground bg-muted/40 border-b border-border`}
    >
      <span>指数 / 收盘</span>
      {PERIODS.map((period) => (
        <span
          key={period.key}
          className="text-right"
          title={`${period.label}收益；下方小字为该周期 A 股指数间排名`}
        >
          {period.label}
        </span>
      ))}
      <span className="text-right" title="相对沪深300 的超额收益，单位百分点">
        {showRelative ? '相对300' : '交易日'}
      </span>
    </div>
  )
}

interface Props {
  index: IndexCardData
  /** A 股显示相对300；美股不显示（跨市场比较无意义） */
  showRelative?: boolean
  /** stagger 动画序号 */
  order?: number
}

export default function IndexCard({ index, showRelative = true, order = 0 }: Props) {
  const warming = showRelative && isIndexWarming(index)

  return (
    <motion.div
      initial={{ opacity: 0, x: -6 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.2, delay: Math.min(order * 0.025, 0.2) }}
      className={`${ROW_GRID} px-3 py-2.5 border-b border-border/60 last:border-b-0 hover:bg-muted/30 transition-colors`}
    >
      {/* 名称 + 收盘 */}
      <div className="min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="text-sm font-medium truncate" title={index.index_name}>
            {index.index_name}
          </span>
          {warming && (
            <span
              className="shrink-0 rounded border border-warning/40 bg-warning/10 px-1 py-0.5 text-[11px] text-warning"
              title="该指数历史数据回补中"
            >
              回补中
            </span>
          )}
        </div>
        <p
          className="text-xs text-muted-foreground num truncate"
          title={`高 ${fmtNumber(index.high, 2)} / 低 ${fmtNumber(index.low, 2)}`}
        >
          {fmtNumber(index.close, 2)}
        </p>
      </div>

      {/* 5 个周期 + 同期排名 */}
      {PERIODS.map((period) => {
        const value = index[period.key]
        const rank = index.ranks?.[period.key]
        return (
          <div key={period.key} className="flex flex-col items-end leading-tight num">
            <span className={`text-sm font-medium ${pctColor(value)}`}>
              {fmtPct(value)}
            </span>
            <span className="text-[11px] text-muted-foreground">
              {rank && rank.total > 1 ? `${rank.rank}/${rank.total}` : '\u00A0'}
            </span>
          </div>
        )
      })}

      {/* 末列：相对300 或美股交易日 */}
      <div className="flex flex-col items-end justify-center gap-0.5 text-[11px] num">
        {showRelative ? (
          <>
            <span className="flex items-center gap-1.5">
              <span className="text-muted-foreground">20日</span>
              <span className={relativeBadgeClass(index.rel_300_20d)}>
                {fmtRelative(index.rel_300_20d)}
              </span>
            </span>
            <span className="flex items-center gap-1.5">
              <span className="text-muted-foreground">年内</span>
              <span className={relativeBadgeClass(index.rel_300_ytd)}>
                {fmtRelative(index.rel_300_ytd)}
              </span>
            </span>
          </>
        ) : (
          <span className="text-muted-foreground">{index.subtitle ?? EMPTY_TEXT}</span>
        )}
      </div>
    </motion.div>
  )
}
