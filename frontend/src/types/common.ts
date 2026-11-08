/**
 * 跨接口共享类型 —— 严格对齐 docs/V5-API-CONTRACT.md 第 0 节
 */

/** 错误码枚举（契约 0.2） */
export type ApiErrorCode =
  | 'DATA_NOT_READY'
  | 'DATA_STALE'
  | 'DATA_QUALITY'
  | 'NO_TRADE_DATE'
  | 'BAD_REQUEST'
  | 'UNKNOWN_ALGO'
  | 'INVALID_PARAM'
  | 'NOT_FOUND'
  | 'PROVIDER_UNAVAILABLE'
  | 'LLM_UNAVAILABLE'
  | 'DB_UNAVAILABLE'
  | 'INTERNAL'
  | 'BUSY'
  /** 以下为前端客户端自行产生的错误码（非后端返回） */
  | 'NETWORK_ERROR'
  | 'TIMEOUT'
  | 'PARSE_ERROR'
  | 'ABORTED'

/** 统一错误响应体（契约 0.2；C2 追加可选 suggestion） */
export interface ApiErrorBody {
  ok: false
  error: {
    code: ApiErrorCode | string
    message: string
    /** 可操作建议；缺省时前端使用统一兜底文案 */
    suggestion?: string
    detail?: unknown
  }
  request_id?: string
}

/** C1：列表接口追加的可选分页元信息 */
export interface PaginationMeta {
  total?: number
  has_more?: boolean
  page?: number
  page_size?: number
}

/** 提取列表后的统一包裹结果（兼容无分页字段的旧接口） */
export interface ListPage<T> extends PaginationMeta {
  items: T[]
}

/** 数据新鲜度状态（契约 0.4） */
export type FreshnessStatus = 'ok' | 'stale' | 'quality_failed' | 'error' | 'unknown'

/** 数据新鲜度对象（契约 0.4） */
export interface Freshness {
  dataset: string
  display_name: string
  status: FreshnessStatus
  /** ISO-8601，含时区；从未成功时为 null */
  last_success_at: string | null
  last_trade_date: string | null
  rows: number | null
  /** 0–1 小数 */
  coverage: number | null
  anomalies: number | null
  error: string | null
}

/** 交易日历单项（契约 1） */
export interface TradeDay {
  cal_date: string
  is_open: boolean
}

/** GET /api/v1/meta/trade-calendar */
export interface TradeCalendarResponse {
  days: TradeDay[]
}

/** GET /health（注意：不在 /api/v1 前缀下；后端实际字段为 latest_trade_date） */
export interface HealthResponse {
  status: string
  service: string
  version: string
  database: string
  /** 契约字段名；后端实际返回 latest_trade_date */
  trade_date?: string | null
  latest_trade_date?: string | null
  data_ready: boolean
}
