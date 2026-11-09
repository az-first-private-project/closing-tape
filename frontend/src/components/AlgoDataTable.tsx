/**
 * 选股结果表格（契约 5.2 data[]）
 * =================================
 * 固定列：排名 / 代码 / 名称 / 得分；其余列由 data[].metrics 的键并集动态生成。
 * 复用泛型 DataTable：支持排序、行点击跳个股详情、前端 CSV 导出。
 */
import { useMemo } from 'react'
import { Info } from 'lucide-react'
import {
  rowScore,
  supportsSignalExplanation,
  type AlgoMetricScalar,
  type AlgoParamsSnapshot,
  type AlgoStockRow,
  type MarketGate,
} from '../types/algo'
import { fmtMetricValue } from '../lib/format'
import DataTable, { type DataTableColumn, type DataTableSortState } from './DataTable'
import RangeNotice from './RangeNotice'
import SignalExplanation from './SignalExplanation'

interface Props {
  data: AlgoStockRow[]
  algo?: string
  onRowClick?: (row: AlgoStockRow, rowKey: string) => void
  csvFilename?: string
  /** 策略说明（底部固定行） */
  fullDesc?: string | null
  /** run 参数快照（底部固定行） */
  params?: AlgoParamsSnapshot | null
  /** run 信号快照 schema（区分旧版无依据与当前版本缺字段） */
  signalSchema?: number | null
  /** B4/B5 的 run 级市场门槛（0 信号时解释原因） */
  marketGate?: MarketGate | null
  /** 本 run 保存的信号总数（C1 total）；缺省等于当前展示条数 */
  total?: number | null
  /** 额外范围说明（如 run 按 limit 截断） */
  rangeNote?: string
  /** 恢复的表格状态（页面按 run 持久化后回传；undefined 表示无历史状态） */
  initialSort?: DataTableSortState | null
  initialExpandedKey?: string | null
  initialActiveKey?: string | null
  initialScrollTop?: number | null
  onSortChange?: (sort: DataTableSortState | null) => void
  onExpandedChange?: (expandedKey: string | null) => void
  onScrollTopChange?: (scrollTop: number) => void
}

/** 无综合评分时，「得分」列展示各策略的排序指标 */
const SCORE_META: Record<string, { key: string; header: string }> = {
  a3: { key: 'composite_score', header: '综合评分' },
  a4: { key: 'dv_ttm', header: '股息率' },
  b1: { key: 'roc10', header: 'ROC10(%)' },
  b2: { key: 'rsi6', header: 'RSI6' },
  b3: { key: 'kdj_j', header: 'KDJ J 值' },
  b4: { key: 'boll_width_pct60', header: '带宽分位(%)' },
  b5: { key: 'kdj_j', header: 'KDJ J 值' },
}

function fmtStrategyScore(algo: string | undefined, value: number): string {
  return fmtMetricValue(SCORE_META[algo ?? '']?.key ?? 'composite_score', value)
}

/** 已知 metrics 键 → 中文列名 */
const METRIC_LABELS: Record<string, string> = {
  close: '收盘价',
  pct_chg: '涨跌幅',
  pe_ttm: 'PE(TTM)',
  pb: 'PB',
  ps_ttm: 'PS(TTM)',
  pcf_ttm: 'PCF(TTM)',
  dv_ttm: '股息率',
  dividend_ratio_ttm: '股息率',
  macd_dif: 'MACD DIF',
  macd_dea: 'MACD DEA',
  macd_hist: 'MACD 柱',
  macd_golden_cross: 'MACD金叉',
  rsi14: 'RSI14',
  rsi6: 'RSI6',
  roc10: 'ROC10',
  atr14: 'ATR14',
  ma5: 'MA5',
  ma10: 'MA10',
  ma20: 'MA20',
  ma60: 'MA60',
  ma120: 'MA120',
  ema5: 'EMA5',
  ema20: 'EMA20',
  ema60: 'EMA60',
  ema20_up: 'EMA20上行',
  ema_golden_cross: 'EMA金叉',
  ema_cross_3d: 'EMA近3日金叉',
  kdj_k: 'KDJ K',
  kdj_d: 'KDJ D',
  kdj_j: 'KDJ J',
  kdj_j_min2: '近2日最低J',
  kdj_golden_cross: 'KDJ金叉',
  boll_width: '布林带宽',
  boll_width_pct60: '带宽60日分位',
  high20_close: '20日最高收盘',
  range20_pct: '20日区间宽度',
  amp60: '60日振幅',
  vol_ma5: '5日均量',
  ma_bullish: '均线多头',
  main_sum_5d: '5日主力净流入',
  main_net_5d: '5日主力净流入',
  main_net_5d_wan: '5日主力净流入(万元)',
  main_t0: '今日主力净流入',
  main_t1: 'T-1 主力净流入',
  main_t2: 'T-2 主力净流入',
  main_t3: 'T-3 主力净流入',
  main_t4: 'T-4 主力净流入',
  main_net_t0: '今日主力净流入',
  main_net_t1: 'T-1 主力净流入',
  main_net_t2: 'T-2 主力净流入',
  main_net_t3: 'T-3 主力净流入',
  main_net_t4: 'T-4 主力净流入',
  turnover_rate: '换手率',
  market_cap: '总市值',
  float_cap: '流通市值',
  volume_ratio: '量比',
  momentum_20: '20日动量',
  volatility_20: '20日波动率',
  composite_score: '综合评分',
  value_score: '价值分',
  quality_score: '质量分',
  momentum_score: '动量分',
  liquidity_score: '流动性分',
  sentiment_score: '情绪分',
  fundamental_score: '基本面分',
  technical_score: '技术面分',
  capital_score: '资金面分',
  risk_score: '风险面分',
  state_owned: '央企/国企',
  ownership: '所有制',
  industry: '行业',
  industry_l1: '行业',
  amount: '成交额',
  volatility_score: '波动率分',
}

/** 未登记键 → 可读列名：下划线转空格并大写缩写 */
function prettifyMetricKey(key: string): string {
  return key
    .split('_')
    .map((part) => (part.length <= 3 ? part.toUpperCase() : part))
    .join(' ')
}

/** 固定列 + 结构化说明字段（后者不铺成平表列） */
const FIXED_KEYS = new Set(['rank', 'code', 'name', 'composite_score', 'triggered', 'score_breakdown'])

function fmtGateValue(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : value.toFixed(2)
}

/** 市场门槛提示：未通过或指数缺失时才返回文案 */
function marketGateNotice(algo: string | undefined, gate?: MarketGate | null): string | null {
  if (!gate) return null
  if (gate.available === false) {
    return '沪深300 指数数据缺失，市场门槛按放行处理。'
  }
  if (gate.passed !== false) return null
  if (algo === 'b4') {
    return `市场环境要求未满足：沪深300 收盘 ${fmtGateValue(gate.close)} < MA20 × 0.995 = ${fmtGateValue(gate.threshold)}，本次不产生信号。`
  }
  if (algo === 'b5') {
    return `市场环境要求未满足：沪深300 当日涨跌幅 ${fmtGateValue(gate.pct_chg)}% ≤ -2%，本次不产生信号。`
  }
  return '市场环境要求未满足，本次不产生信号。'
}

export default function AlgoDataTable({
  data,
  algo,
  onRowClick,
  csvFilename,
  fullDesc,
  params,
  signalSchema,
  marketGate,
  total,
  rangeNote,
  initialSort,
  initialExpandedKey,
  initialActiveKey,
  initialScrollTop,
  onSortChange,
  onExpandedChange,
  onScrollTopChange,
}: Props) {
  const explainable = supportsSignalExplanation(algo)
  const gateNotice = marketGateNotice(algo, marketGate)
  const gateBlocked = marketGate?.passed === false
  /**
   * 动态指标列：按首次出现顺序取并集。
   * 只有「得分」列本身展示该策略排序指标时才跳过，避免排序依据被隐藏
   * （有综合评分时「得分」列展示综合评分，B 系列的 ROC10/RSI6 等仍需单独成列）。
   */
  const metricKeys = useMemo(() => {
    const scoreKey = SCORE_META[algo ?? '']?.key
    const hideScoreKey = scoreKey !== undefined && !data.some((row) => row.composite_score != null)
    const keys = new Set<string>()
    data.forEach((row) => {
      Object.keys(row.metrics ?? {}).forEach((key) => {
        if (!FIXED_KEYS.has(key) && !(hideScoreKey && key === scoreKey)) keys.add(key)
      })
    })
    // A2 同时返回元/万元两种口径，只保留更易读的万元列
    if (keys.has('main_net_5d_wan')) keys.delete('main_net_5d')
    return [...keys]
  }, [algo, data])

  const columns = useMemo<DataTableColumn<AlgoStockRow>[]>(() => {
    const fixed: DataTableColumn<AlgoStockRow>[] = [
      {
        key: 'rank',
        header: '排名',
        align: 'center',
        sortable: true,
        value: (row) => row.rank,
        render: (row, index) => row.rank ?? index + 1,
      },
      {
        key: 'code',
        header: '代码',
        sortable: true,
        value: (row) => row.code,
        render: (row) => <span className="font-mono">{row.code}</span>,
      },
      { key: 'name', header: '名称', sortable: true, value: (row) => row.name },
      {
        key: 'composite_score',
        header: data.some((row) => row.composite_score != null)
          ? '综合评分'
          : (SCORE_META[algo ?? '']?.header ?? '得分'),
        align: 'right',
        sortable: true,
        value: (row) => rowScore(row),
        render: (row) => {
          const score = rowScore(row)
          if (score === null) {
            return <span className="text-muted-foreground">未评分</span>
          }
          return <span className="font-semibold">{fmtStrategyScore(algo, score)}</span>
        },
      },
    ]

    const dynamic: DataTableColumn<AlgoStockRow>[] = metricKeys.map((key) => ({
      key: `metric:${key}`,
      header: METRIC_LABELS[key] ?? prettifyMetricKey(key),
      align: 'right',
      sortable: true,
      value: (row) => row.metrics?.[key] as AlgoMetricScalar | undefined,
      render: (row) => fmtMetricValue(key, row.metrics?.[key] as AlgoMetricScalar | undefined),
    }))

    return [...fixed, ...dynamic]
  }, [metricKeys])

  return (
    <div className="space-y-2">
      {gateNotice && (
        <p className="flex items-start gap-1.5 rounded-lg border border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>{gateNotice}</span>
        </p>
      )}
      <DataTable
        columns={columns}
        rows={data}
        rowKey={(row, index) => `${row.code}-${index}`}
        onRowClick={onRowClick}
        initialSort={initialSort === undefined ? { key: 'rank', direction: 'asc' } : initialSort}
        initialExpandedKey={initialExpandedKey}
        initialActiveKey={initialActiveKey}
        initialScrollTop={initialScrollTop}
        onSortChange={onSortChange}
        onExpandedChange={onExpandedChange}
        onScrollTopChange={onScrollTopChange}
        csvFilename={csvFilename}
        emptyText={gateBlocked ? '市场环境未满足，本次无信号' : '本次运行没有选股信号'}
        maxHeight="520px"
        renderExpanded={
          explainable
            ? (row) => (
                <SignalExplanation
                  algo={algo}
                  row={row}
                  fullDesc={fullDesc}
                  params={params}
                  signalSchema={signalSchema}
                />
              )
            : undefined
        }
        footer={
          <span className="flex flex-wrap items-center gap-x-2">
            <RangeNotice shown={data.length} total={total ?? data.length} unit="条信号" />
            {rangeNote && <span>· {rangeNote}</span>}
            <span>· 动态指标 {metricKeys.length} 列</span>
            <span>· 点击行可查看个股详情</span>
            {explainable && <span>· 点击左侧箭头查看信号依据</span>}
            <span>· 导出 CSV 仅包含当前展示的 {data.length} 条</span>
          </span>
        }
      />
    </div>
  )
}
