/**
 * 信号依据 / 评分拆解（V6 T7）
 * ==============================
 * - B1–B5：展示同一份 CONDITION_SPECS 的逐条求值结果，market 条件单独分组；
 * - A3：读取 run 中的 score_breakdown（来源 fact_stock_score_daily.detail），
 *   用 0–10 分条展示分项贡献，不新增前端计算；
 * - 依据缺失时按 run.signal_schema 区分“旧版快照”与“当前版本缺字段”，
 *   不伪造全通过列表。
 */
import type { ReactNode } from 'react'
import { Info } from 'lucide-react'
import { scorePercent } from '../lib/format'
import {
  isLegacySignalSnapshot,
  rowBreakdown,
  rowTriggered,
  type AlgoMetricScalar,
  type AlgoParamsSnapshot,
  type AlgoStockRow,
  type ScoreBreakdown,
  type ScoreBreakdownPart,
  type TriggeredCondition,
} from '../types/algo'

interface Props {
  algo?: string
  row: AlgoStockRow
  fullDesc?: string | null
  params?: AlgoParamsSnapshot | null
  /** run.signal_schema；旧版（<2）与当前版本缺字段要给出不同提示 */
  signalSchema?: number | null
}

const OP_TEXT: Record<string, string> = {
  gt: '>',
  ge: '≥',
  lt: '<',
  le: '≤',
  eq: '=',
  ne: '≠',
  notna: '非空',
  isna: '为空',
  between: '介于',
  top_pct: '前百分位',
  bottom_pct: '后百分位',
}

const RAW_LABELS: Record<string, string> = {
  pe_ttm: 'PE(TTM)',
  pb: 'PB',
  ps_ttm: 'PS(TTM)',
  dv_ttm: '股息率',
  momentum_20: '20日动量',
  momentum_60: '60日动量',
  volatility_20: '20日波动率',
  amount_ma20: '20日均额',
  amount: '成交额',
}

/** 条件实际值 / 阈值的人类可读格式（不做字段语义猜测） */
function fmtCondValue(value: AlgoMetricScalar | undefined): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'string') return value
  if (!Number.isFinite(value)) return '—'
  const abs = Math.abs(value)
  if (abs >= 1e8) return `${(value / 1e8).toFixed(2)} 亿`
  if (abs >= 1e4) return `${(value / 1e4).toFixed(2)} 万`
  if (abs >= 100) return value.toFixed(2)
  if (abs < 1) return value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '')
  return value.toFixed(2).replace(/\.?0+$/, '')
}

/** 单条条件：通过用正常色，未通过弱化为灰色（不显示为错误） */
function ConditionRow({ item }: { item: TriggeredCondition }) {
  const requiredText = item.required ? '必须' : '可选'
  const opText = item.op ? (OP_TEXT[item.op] ?? item.op) : null
  const hasThreshold = item.threshold !== undefined && item.threshold !== null
  const requirement = opText
    ? hasThreshold
      ? `${opText} ${fmtCondValue(item.threshold)}`
      : opText
    : requiredText

  return (
    <li className={`flex items-start gap-2 py-1 ${item.passed ? '' : 'text-muted-foreground'}`}>
      <span
        className={`mt-0.5 w-4 shrink-0 text-center text-[13px] font-semibold ${
          item.passed ? 'text-foreground' : 'text-muted-foreground/70'
        }`}
        aria-label={item.passed ? '通过' : '未通过'}
      >
        {item.passed ? '✓' : '✗'}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5">
          <span className={`text-xs ${item.passed ? 'text-foreground' : 'text-muted-foreground'}`}>
            {item.label}
          </span>
          <span className="text-[11px] num text-muted-foreground">
            实际 {fmtCondValue(item.actual)} · 要求 {requirement}
          </span>
        </div>
        {item.detail && (
          <p className="mt-0.5 text-[11px] font-mono text-muted-foreground/80 break-all">
            {item.detail}
          </p>
        )}
      </div>
    </li>
  )
}

/** 分项原始输入摘要 */
function rawSummary(part: ScoreBreakdownPart): string {
  const entries = Object.entries(part.raw ?? {}).filter(
    ([, value]) => value !== null && value !== undefined,
  )
  if (entries.length === 0) return '因子输入缺失，按中性分位计'
  return entries
    .map(([key, value]) => `${RAW_LABELS[key] ?? key} ${fmtCondValue(value)}`)
    .join(' · ')
}

/** A3 评分拆解：条形长度 = 分项得分 / 10 */
function BreakdownView({ breakdown }: { breakdown: ScoreBreakdown }) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-medium">评分拆解</span>
        <span className="text-[11px] text-muted-foreground">
          综合分{' '}
          <span className="num font-semibold text-foreground">
            {breakdown.composite !== null && breakdown.composite !== undefined
              ? breakdown.composite.toFixed(2)
              : '—'}
          </span>{' '}
          / 10
          {breakdown.weight_sum !== null && breakdown.weight_sum !== undefined && (
            <> · 可用权重 {Math.round(breakdown.weight_sum * 100)}%</>
          )}
        </span>
      </div>

      <div className="space-y-2.5">
        {breakdown.parts.map((part) => (
          <div key={part.key}>
            <div className="flex flex-wrap items-baseline justify-between gap-x-3 text-[11px]">
              <span className="text-foreground">
                {part.name}
                <span className="ml-1.5 text-muted-foreground">
                  权重 {part.weight !== null && part.weight !== undefined
                    ? `${Math.round(part.weight * 100)}%`
                    : '—'}
                </span>
              </span>
              <span className="num text-muted-foreground">
                {part.score !== null && part.score !== undefined ? part.score.toFixed(2) : '—'} 分
                {part.contribution !== null && part.contribution !== undefined && (
                  <> · 贡献 {part.contribution.toFixed(2)}</>
                )}
                {' · '}分位 {part.z !== null && part.z !== undefined ? part.z.toFixed(2) : '—'}
              </span>
            </div>
            <div className="mt-1 h-1.5 rounded-full bg-muted overflow-hidden">
              <div
                className="h-full rounded-full bg-brass/70"
                style={{ width: `${scorePercent(part.score)}%` }}
              />
            </div>
            <p className="mt-0.5 text-[10px] text-muted-foreground">{rawSummary(part)}</p>
          </div>
        ))}
      </div>

      <p className="text-[11px] leading-relaxed text-muted-foreground">
        条形长度 = 分项得分 / 10。综合分 = 10 × Σ(分位 × 权重) / Σ(当日可用权重)，四舍五入到 2
        位；贡献 = 分项得分 × 权重 / Σ(当日可用权重)，逐项贡献之和与综合分一致（±0.01
        四舍五入）。分项得分 = 分位 × 10；分位缺失时显示中性 5.0 分，但不计入可用权重与贡献。
      </p>
      {breakdown.sentiment_source && (
        <p className="text-[10px] text-muted-foreground">
          情绪因子来源：{breakdown.sentiment_source === 'neutral' ? '中性（无情绪数据）' : breakdown.sentiment_source}
        </p>
      )}
    </div>
  )
}

function DegradedHint({ text }: { text: string }) {
  return (
    <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
      <Info className="w-3.5 h-3.5 shrink-0" />
      {text}
    </p>
  )
}

/** 参数快照（run.params）摘要 */
function paramsText(params?: AlgoParamsSnapshot | null): string {
  if (!params) return ''
  const entries = Object.entries(params)
    .filter(([, value]) => value !== null && value !== undefined)
    .slice(0, 12)
    .map(([key, value]) => `${key}=${typeof value === 'boolean' ? (value ? '是' : '否') : value}`)
  return entries.join(' · ')
}

export default function SignalExplanation({ algo, row, fullDesc, params, signalSchema }: Props) {
  const isA3 = algo === 'a3'
  const legacySnapshot = isLegacySignalSnapshot(algo, signalSchema)
  const triggered = rowTriggered(row)
  const breakdown = rowBreakdown(row)
  const stockItems = (triggered ?? []).filter((item) => item.scope !== 'market')
  const marketItems = (triggered ?? []).filter((item) => item.scope === 'market')
  const passedCount = (triggered ?? []).filter((item) => item.passed).length
  const snapshot = paramsText(params)

  let body: ReactNode
  if (isA3) {
    body = breakdown ? (
      <BreakdownView breakdown={breakdown} />
    ) : (
      <DegradedHint
        text={
          legacySnapshot
            ? '该 run 为旧版本快照，无评分拆解。'
            : '当前版本 run 未记录该信号的评分拆解（当日评分明细可能缺失）。'
        }
      />
    )
  } else if (triggered && triggered.length > 0) {
    body = (
      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-xs font-medium">信号依据</span>
          <span className="text-[11px] text-muted-foreground">
            共 {triggered.length} 条 · 通过 {passedCount} 条
          </span>
        </div>
        <ul className="divide-y divide-border/60">
          {stockItems.map((item, index) => (
            <ConditionRow key={`${item.key}-${index}`} item={item} />
          ))}
        </ul>
        {marketItems.length > 0 && (
          <div>
            <p className="mb-0.5 text-[11px] font-medium text-muted-foreground">市场环境要求</p>
            <ul className="divide-y divide-border/60">
              {marketItems.map((item, index) => (
                <ConditionRow key={`${item.key}-market-${index}`} item={item} />
              ))}
            </ul>
          </div>
        )}
      </div>
    )
  } else {
    body = (
      <DegradedHint
        text={
          legacySnapshot
            ? '该 run 为旧版本快照，无逐条依据。'
            : '当前版本 run 未记录该信号的逐条依据。'
        }
      />
    )
  }

  return (
    <div className="space-y-3">
      {body}
      <div className="space-y-1 border-t border-border/70 pt-2 text-[11px] leading-relaxed text-muted-foreground">
        {fullDesc && <p>{fullDesc}</p>}
        <p>参数快照来自 run.params{snapshot ? `：${snapshot}` : '（本次 run 未记录）'}。</p>
      </div>
    </div>
  )
}
