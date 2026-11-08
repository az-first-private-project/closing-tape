/**
 * 算法参数面板（契约 5.1 params_schema 驱动）
 * ============================================
 * - 数值型：number 输入框 +（schema 同时给出 min/max 时）slider，step 由 schema 推导；
 * - 选择型：下拉框；文本型：文本框；
 * - 参数变化只更新本地状态，「执行算法」按钮才真正发请求（不再滑杆一动就请求）。
 */
import { Play, RotateCcw, SlidersHorizontal } from 'lucide-react'
import type { AlgoMeta, AlgoParamDef, AlgoParamValue, AlgoParams } from '../types/algo'
import { fmtAmount } from '../lib/format'

interface AlgoParamPanelProps {
  algo: AlgoMeta
  values: AlgoParams
  onChange: (key: string, value: number | string) => void
  /** 恢复 schema 默认值 */
  onReset: () => void
  /** 执行算法（useMutation 触发） */
  onExecute: () => void
  isRunning: boolean
  /** 面板值与当前展示 run 的快照是否不一致（U01） */
  dirty?: boolean
  /** 不一致的参数 key，用于明确提示 */
  changedKeys?: string[]
  /** 显式选择的交易日与展示结果交易日不一致 */
  dateMismatch?: boolean
}

/**
 * 由 schema 推导 slider 步长（契约未定义 step）：
 * - 小范围整数参数（如 limit 1–1000）用 1；
 * - 大范围数值参数按量级取整（如 0–1e12 成交额用 1e9）；
 * - 小数参数按 span/200 的量级取 0.1 / 0.01。
 */
function deriveStep(schema: AlgoParamDef): number {
  const defaultValue = typeof schema.default === 'number' ? schema.default : 1
  const min = schema.min ?? 0
  const max = schema.max ?? Math.max(100, defaultValue * 2)
  const span = Math.abs(max - min) || 1
  const allIntegers = Number.isInteger(min) && Number.isInteger(max) && Number.isInteger(defaultValue)
  if (allIntegers && span <= 1000) return 1
  const rough = span / 200
  const magnitude = Math.pow(10, Math.floor(Math.log10(rough)))
  if (!Number.isFinite(magnitude) || magnitude <= 0) return 0.01
  return magnitude
}

/** 范围端点展示：大数按 亿/万 自适应，否则原样 */
function fmtRangeBound(value: number | undefined): string {
  if (value === undefined) return '—'
  if (Math.abs(value) >= 1e4) return fmtAmount(value)
  return Number.isInteger(value) ? String(value) : String(value)
}

/** 当前参数值（未设置时回落到 schema 默认值） */
function currentValue(schema: AlgoParamDef, values: AlgoParams): AlgoParamValue {
  const value = values[schema.key]
  return value !== undefined ? value : schema.default
}

/** 渲染单个参数控件 */
function ParamControl({
  schema,
  values,
  onChange,
}: {
  schema: AlgoParamDef
  values: AlgoParams
  onChange: (key: string, value: number | string) => void
}) {
  const value = currentValue(schema, values)

  // ---- 下拉选择 ----
  if (schema.type === 'select') {
    const options = schema.options ?? []
    return (
      <select
        value={String(value ?? '')}
        onChange={(event) => {
          const raw = event.target.value
          // 尽量保留原始类型（number 选项回传 number）
          const matched = options.find((option) => String(option) === raw)
          onChange(schema.key, typeof matched === 'number' ? matched : raw)
        }}
        className="field"
      >
        {options.length === 0 && <option value={String(value ?? '')}>{String(value ?? '')}</option>}
        {options.map((option) => (
          <option key={String(option)} value={String(option)}>
            {String(option)}
          </option>
        ))}
      </select>
    )
  }

  // ---- 文本 ----
  if (schema.type === 'text') {
    return (
      <input
        type="text"
        value={String(value ?? '')}
        onChange={(event) => onChange(schema.key, event.target.value)}
        className="field"
      />
    )
  }

  // ---- 数值（number + 可选 slider） ----
  const numericValue = typeof value === 'number' ? value : Number(value ?? 0)
  const hasRange = schema.min !== undefined && schema.max !== undefined
  const step = deriveStep(schema)

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <input
          type="number"
          value={Number.isFinite(numericValue) ? numericValue : ''}
          min={schema.min}
          max={schema.max}
          step={step}
          onChange={(event) => {
            const next = event.target.value === '' ? 0 : Number(event.target.value)
            onChange(schema.key, Number.isFinite(next) ? next : 0)
          }}
          className="field num !w-24"
        />
        {hasRange && (
          <input
            type="range"
            min={schema.min}
            max={schema.max}
            step={step}
            value={Number.isFinite(numericValue) ? numericValue : Number(schema.min ?? 0)}
            onChange={(event) => onChange(schema.key, Number(event.target.value))}
            className="flex-1 accent-[var(--color-brass)]"
          />
        )}
        {hasRange && (
          <span className="num whitespace-nowrap text-[12px] text-muted-foreground">
            {fmtRangeBound(schema.min)}–{fmtRangeBound(schema.max)}
          </span>
        )}
      </div>
    </div>
  )
}

export default function AlgoParamPanel({
  algo,
  values,
  onChange,
  onReset,
  onExecute,
  isRunning,
  dirty = false,
  changedKeys = [],
  dateMismatch = false,
}: AlgoParamPanelProps) {
  return (
    <div className="card space-y-4 p-4">
      {/* 标题 */}
      <div>
        <div className="flex items-baseline gap-2">
          <SlidersHorizontal className="h-3.5 w-3.5 shrink-0 self-center text-brass" />
          <span className="eyebrow">Parameters</span>
        </div>
        <h3 className="mt-1.5 text-[14px] font-semibold">参数面板</h3>
        <p className="mt-1.5 text-[12px] leading-relaxed text-muted-foreground">{algo.full_desc}</p>
      </div>

      {dirty && (
        <div
          className="rounded-lg border border-warning/45 bg-warning/10 px-3 py-2 text-[12px] leading-relaxed text-warning"
          role="status"
        >
          参数已修改
          {changedKeys.length > 0 && (
            <span className="num">（{changedKeys.join('、')}）</span>
          )}
          {dateMismatch && <span>，交易日也已切换</span>}
          ：下方展示的仍是上一次 run 的结果；点击「执行算法」按新参数重算，导出与信号依据仍使用旧 run 快照。
        </div>
      )}

      {/* schema 驱动控件 */}
      <div className="space-y-3">
        {algo.params_schema.map((schema) => (
          <div key={schema.key}>
            <label className="mb-1.5 block text-[12px] font-medium" title={schema.key}>
              {schema.label}
              <span className="ml-1.5 font-mono text-[12px] tracking-wide text-muted-foreground/70">{schema.key}</span>
            </label>
            <ParamControl schema={schema} values={values} onChange={onChange} />
          </div>
        ))}
        {algo.params_schema.length === 0 && (
          <p className="text-[12px] text-muted-foreground">该算法无可调参数。</p>
        )}
      </div>

      {/* 操作按钮 */}
      <div className="space-y-2 pt-1">
        <button
          onClick={onExecute}
          disabled={isRunning}
          className="btn btn-primary w-full"
        >
          {isRunning ? (
            <span className="skeleton h-4 w-4 rounded-full" aria-hidden="true" />
          ) : (
            <Play className="w-4 h-4" />
          )}
          {isRunning ? '执行中…' : dirty ? '按新参数执行' : '执行算法'}
        </button>
        <button
          onClick={onReset}
          disabled={isRunning}
          className="btn btn-sm w-full"
        >
          <RotateCcw className="w-3.5 h-3.5" />
          恢复默认参数
        </button>
        <p className="text-center text-[12px] text-muted-foreground/80">修改参数不会自动请求，点击「执行算法」后生效</p>
      </div>
    </div>
  )
}
