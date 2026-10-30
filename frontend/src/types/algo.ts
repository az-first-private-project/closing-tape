/**
 * 选股相关类型 —— 严格对齐 docs/V5-API-CONTRACT.md 第 5 节
 */
import type { Freshness } from './common'

/** 参数值快照（run.params 中可能出现数字/字符串/布尔/null） */
export type AlgoParamValue = number | string | boolean | null

/** 查询串形式的策略参数（未设置则不传） */
export type AlgoParams = Record<string, string | number | undefined>

/** run 参数快照 */
export type AlgoParamsSnapshot = Record<string, AlgoParamValue>

/** 参数 schema 单项（5.1；契约未定义 step，前端按 min/max/default 推导） */
export interface AlgoParamDef {
  key: string
  label: string
  /** 后端实际还会返回 "integer"（如 limit），前端按数值控件处理 */
  type: 'number' | 'integer' | 'select' | 'text'
  default: AlgoParamValue
  min?: number
  max?: number
  options?: AlgoParamValue[]
}

/** 算法元信息（5.1） */
export interface AlgoMeta {
  /** 契约示例为 "a1"，字符串以兼容后续新增策略 */
  algo_code: string
  algo_name: string
  algo_type: string
  short_desc: string
  full_desc: string
  params_schema: AlgoParamDef[]
  /** 契约 5.1 为布尔；兼容后端 0/1 时按 truthy 判断 */
  is_active: boolean
  sort_order: number
}

/** 单次运行元信息（5.2 run） */
export interface AlgoRun {
  run_id: string
  created_at: string | null
  elapsed_ms: number | null
  universe_count: number | null
  signal_count: number | null
  data_version: string | null
  params: AlgoParamsSnapshot
  /** 后端额外返回：running / success / failed */
  status?: string | null
  error?: string | null
  is_backtest?: boolean
}

/** 动态指标值（data[].metrics，值可能是数字/字符串/布尔） */
export type AlgoMetricValue = number | string | boolean | null

/** 选股结果行（5.2 data[]） */
export interface AlgoStockRow {
  code: string
  name: string
  rank: number | null
  close: number | null
  pct_chg: number | null
  pe_ttm: number | null
  pb: number | null
  ps_ttm: number | null
  dv_ttm: number | null
  macd_dif: number | null
  macd_dea: number | null
  rsi14: number | null
  composite_score: number | null
  /** 后端当前额外返回的排名百分位分（0–100），仅作兜底展示 */
  score?: number | null
  /** 策略依据的完整指标，前端表格列由它动态生成 */
  metrics: Record<string, AlgoMetricValue>
}

/**
 * 取「得分」列展示值：
 * 优先 0–10 的综合评分 composite_score；没有综合评分时退回策略排序指标的原始值
 * （A4=股息率、B1=ROC10、B2=RSI6、B3/B5=KDJ J、B4=带宽分位），
 * 后端不再提供排名百分位。
 */
export function rowScore(row: AlgoStockRow): number | null {
  if (row.composite_score !== null && row.composite_score !== undefined) return row.composite_score
  if (row.score !== null && row.score !== undefined) return row.score
  return null
}

/** GET /api/v1/algo/{algo_code} 响应（5.2） */
export interface AlgoResult {
  algo: string
  algo_name: string
  trade_date: string
  run: AlgoRun
  from_cache: boolean
  count: number
  data: AlgoStockRow[]
}

/** 历史 run 摘要（5.3；后端返回 {items:[...]}，元素含 run 元信息 + 状态） */
export interface AlgoRunSummary extends AlgoRun {
  trade_date?: string | null
  algo?: string
  algo_name?: string
}

/**
 * 单次 run 明细（5.3 GET /algo/runs/{run_id}）。
 * 契约未定义响应结构，前端暂按与 5.2 结果一致处理。
 */
export type AlgoRunDetail = AlgoResult

/** 执行算法时的选项 */
export interface AlgoExecuteOptions {
  tradeDate?: string
  forceRefresh?: boolean
}

/** 天梯 sparkline 单点（5.4；win_rate 为 0–1 小数） */
export interface LadderSparkPoint {
  trade_date: string
  win_rate: number | null
  samples: number
}

/** 单策略单 horizon 的信号级统计（5.4） */
export interface LadderHorizonStats {
  /** 已成熟且可成交的有效信号数 */
  sample_signals: number
  sample_stocks: number
  /** 收益/超额单位为百分数（3.41 表示 +3.41%） */
  avg_ret: number | null
  median_ret: number | null
  /** 0–1 小数；信号级口径，非回测 FIFO 回合胜率 */
  win_rate: number | null
  avg_excess: number | null
  best: number | null
  worst: number | null
  immature: number
  unfillable: number
  no_data: number
  total_signals: number
  low_sample: boolean
  spark: LadderSparkPoint[]
}

/** 天梯单行（顶层字段对应所选 horizon） */
export interface LadderItem extends LadderHorizonStats {
  algo_code: string
  algo_name: string
  rank: number
  /** key 为 "5"/"10"/"20" */
  horizons: Record<string, LadderHorizonStats>
}

/** GET /api/v1/algo/ladder（5.4） */
export interface LadderResponse {
  trade_date: string
  window: number
  horizon: number
  benchmark: string
  window_start: string
  items: LadderItem[]
  immature_total: number
  unfillable_total: number
  freshness: Freshness | null
  note?: string
}
