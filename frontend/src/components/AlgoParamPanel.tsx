/**
 * 算法参数面板（契约 5.1 params_schema 驱动）
 * ============================================
 * - 数值型：number 输入框 +（schema 同时给出 min/max 时）slider，step 由 schema 推导；
 * - 选择型：下拉框；文本型：文本框；
 * - 参数变化只更新本地状态，「执行算法」按钮才真正发请求（不再滑杆一动就请求）。
 */
import { Loader2, Play, RotateCcw, SlidersHorizontal } from 'lucide-react'
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
        className="w-full text-sm border border-border rounded-lg px-3 py-2 bg-background focus:outline-none focus:ring-2 focus:ring-primary"
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
        className="w-full text-sm border border-border rounded-lg px-3 py-2 bg-background focus:outline-none focus:ring-2 focus:ring-primary"
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
          className="w-24 text-sm border border-border rounded-lg px-3 py-1.5 bg-background num focus:outline-none focus:ring-2 focus:ring-primary"
        />
        {hasRange && (
          <input
            type="range"
            min={schema.min}
            max={schema.max}
            step={step}
            value={Number.isFinite(numericValue) ? numericValue : Number(schema.min ?? 0)}
            onChange={(event) => onChange(schema.key, Number(event.target.value))}
            className="flex-1 accent-primary"
          />
        )}
        {hasRange && (
          <span className="text-[11px] text-muted-foreground num whitespace-nowrap">
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
}: AlgoParamPanelProps) {
  return (
    <div className="card p-4 space-y-4">
      {/* 标题 */}
      <div>
        <div className="flex items-center gap-2">
          <SlidersHorizontal className="w-4 h-4 text-primary" />
          <h3 className="font-semibold text-sm">参数面板</h3>
        </div>
        <p className="text-xs text-muted-foreground mt-1 leading-relaxed">{algo.full_desc}</p>
      </div>

      {/* schema 驱动控件 */}
      <div className="space-y-3">
        {algo.params_schema.map((schema) => (
          <div key={schema.key}>
            <label className="text-xs font-medium block mb-1" title={schema.key}>
              {schema.label}
              <span className="ml-1 text-[11px] text-muted-foreground font-mono">{schema.key}</span>
            </label>
            <ParamControl schema={schema} values={values} onChange={onChange} />
          </div>
        ))}
        {algo.params_schema.length === 0 && (
          <p className="text-xs text-muted-foreground">该算法无可调参数。</p>
        )}
      </div>

      {/* 操作按钮 */}
      <div className="space-y-2 pt-1">
        <button
          onClick={onExecute}
          disabled={isRunning}
          className="w-full inline-flex items-center justify-center gap-1.5 py-2 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:opacity-90 transition-opacity disabled:opacity-50"
        >
          {isRunning ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
          {isRunning ? '执行中…' : '执行算法'}
        </button>
        <button
          onClick={onReset}
          disabled={isRunning}
          className="w-full inline-flex items-center justify-center gap-1.5 py-1.5 rounded-lg text-xs border border-border hover:bg-muted transition-colors disabled:opacity-50"
        >
          <RotateCcw className="w-3.5 h-3.5" />
          恢复默认参数
        </button>
        <p className="text-[11px] text-muted-foreground text-center">修改参数不会自动请求，点击「执行算法」后生效</p>
      </div>
    </div>
  )
}
