/**
 * 统一 HTTP 客户端（V5）
 * =========================
 * 职责：
 *  1. 统一 base URL（开发环境走 Vite 代理，禁止在这里写死后端地址）；
 *  2. 30s 超时（AbortController）；
 *  3. 解析后端统一错误体 `{ ok:false, error:{ code, message, detail } }`，抛 ApiError；
 *  4. 只对 5xx / 网络错误重试 1 次（4xx 不重试，避免无意义放大）；
 *  5. 文件下载（CSV）封装。
 */
import type { ApiErrorBody } from '../types/common'

/** API 前缀：由 Vite dev proxy / nginx 转发到后端，组件与页面不得自行拼域名 */
export const BASE_URL = '/api/v1'

/** 默认超时时间：30 秒（回测等长任务需要） */
export const DEFAULT_TIMEOUT_MS = 30_000

/** query 参数值类型 */
export type QueryValue = string | number | boolean | null | undefined

/** 请求方法 */
export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

/** request() 选项 */
export interface RequestOptions {
  method?: HttpMethod
  query?: Record<string, QueryValue>
  /** 请求体：对象按 JSON 发送 */
  body?: unknown
  /** 超时时间（毫秒），默认 30s */
  timeoutMs?: number
  /** 重试次数上限，GET 默认 1，其余默认 0；仅对 5xx / 网络错误生效 */
  retry?: number
  /** 覆盖 base URL（如 /health 不在 /api/v1 下时传 ''） */
  baseUrl?: string
  headers?: Record<string, string>
  signal?: AbortSignal
}

/** 统一 API 异常：页面可直接读取 code / message / detail */
export class ApiError extends Error {
  readonly code: string
  readonly status: number
  readonly detail: unknown
  readonly requestId: string | null

  constructor(params: {
    code: string
    message: string
    status: number
    detail?: unknown
    requestId?: string | null
  }) {
    super(params.message)
    this.name = 'ApiError'
    this.code = params.code
    this.status = params.status
    this.detail = params.detail ?? null
    this.requestId = params.requestId ?? null
  }

  /** 是否属于「可重试」错误：网络错误 / 超时 / 5xx */
  get isRetryable(): boolean {
    return this.status === 0 || this.status >= 500
  }
}

/** 判断是否为后端统一错误体 */
function isApiErrorBody(value: unknown): value is ApiErrorBody {
  if (typeof value !== 'object' || value === null) return false
  const body = value as Record<string, unknown>
  const error = body.error
  if (typeof error !== 'object' || error === null) return false
  const err = error as Record<string, unknown>
  return typeof err.code === 'string' && typeof err.message === 'string'
}

/** 把 query 对象拼成查询串（跳过 undefined / null） */
export function buildQueryString(query?: Record<string, QueryValue>): string {
  if (!query) return ''
  const search = new URLSearchParams()
  Object.entries(query).forEach(([key, value]) => {
    if (value === undefined || value === null) return
    search.set(key, String(value))
  })
  const text = search.toString()
  return text ? `?${text}` : ''
}

/** 拼出完整请求 URL */
export function buildUrl(path: string, query?: Record<string, QueryValue>, baseUrl: string = BASE_URL): string {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return `${baseUrl}${normalizedPath}${buildQueryString(query)}`
}

/** 将失败响应转换为 ApiError */
async function toApiError(response: Response): Promise<ApiError> {
  let code = `HTTP_${response.status}`
  let message = response.statusText || `请求失败（HTTP ${response.status}）`
  let detail: unknown = null
  let requestId: string | null = null

  try {
    const text = await response.text()
    if (text) {
      const parsed: unknown = JSON.parse(text)
      if (isApiErrorBody(parsed)) {
        code = parsed.error.code
        message = parsed.error.message
        detail = parsed.error.detail ?? null
        requestId = parsed.request_id ?? null
      } else if (typeof parsed === 'object' && parsed !== null) {
        const payload = parsed as Record<string, unknown>
        const rawDetail = payload.detail
        if (typeof rawDetail === 'string') {
          // FastAPI 默认 HTTPException：{"detail": "..."}
          message = rawDetail
        } else if (Array.isArray(rawDetail)) {
          // FastAPI 参数校验失败：{"detail":[{loc, msg, type}, ...]}
          const first = rawDetail[0] as Record<string, unknown> | undefined
          if (first && typeof first.msg === 'string') {
            const loc = Array.isArray(first.loc) ? first.loc.join('.') : ''
            message = loc ? `${loc}: ${first.msg}` : first.msg
          }
          detail = rawDetail
        } else if (typeof rawDetail === 'object' && rawDetail !== null) {
          // 后端部分路由仍使用 HTTPException(detail={code,message})
          const detailRecord = rawDetail as Record<string, unknown>
          if (typeof detailRecord.code === 'string') code = detailRecord.code
          if (typeof detailRecord.message === 'string') message = detailRecord.message
          detail = rawDetail
        }
        if (typeof payload.message === 'string') message = payload.message
      }
    }
  } catch {
    // 非 JSON 错误体：保留 HTTP 状态信息
  }

  return new ApiError({ code, message, status: response.status, detail, requestId })
}

/** 单次请求（不做重试） */
async function requestOnce<T>(url: string, options: RequestOptions): Promise<T> {
  const method = options.method ?? 'GET'
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)

  // 外部 signal（如组件卸载）与超时 controller 联动
  const onExternalAbort = () => controller.abort()
  if (options.signal) {
    if (options.signal.aborted) controller.abort()
    else options.signal.addEventListener('abort', onExternalAbort, { once: true })
  }

  try {
    const init: RequestInit = {
      method,
      signal: controller.signal,
      headers: {
        Accept: 'application/json',
        ...(options.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...options.headers,
      },
    }
    if (options.body !== undefined) {
      init.body = JSON.stringify(options.body)
    }

    let response: Response
    try {
      response = await fetch(url, init)
    } catch (error) {
      // fetch 在网络故障/超时/主动取消时抛 TypeError / DOMException
      if (controller.signal.aborted) {
        if (options.signal?.aborted) {
          throw new ApiError({ code: 'ABORTED', message: '请求已取消', status: 0 })
        }
        throw new ApiError({
          code: 'TIMEOUT',
          message: `请求超时（${Math.round(timeoutMs / 1000)}s），请稍后重试`,
          status: 0,
        })
      }
      throw new ApiError({
        code: 'NETWORK_ERROR',
        message: error instanceof Error ? `网络请求失败：${error.message}` : '网络请求失败',
        status: 0,
      })
    }

    if (!response.ok) {
      throw await toApiError(response)
    }

    // 204 / 205 无响应体
    if (response.status === 204 || response.status === 205) {
      return undefined as T
    }

    const text = await response.text()
    if (!text) return undefined as T

    try {
      return JSON.parse(text) as T
    } catch {
      throw new ApiError({
        code: 'PARSE_ERROR',
        message: '接口返回的数据不是合法 JSON',
        status: response.status,
      })
    }
  } finally {
    clearTimeout(timer)
    if (options.signal) options.signal.removeEventListener('abort', onExternalAbort)
  }
}

/** 统一请求入口：自动重试（5xx / 网络错误，最多 1 次） */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const url = buildUrl(path, options.query, options.baseUrl ?? BASE_URL)
  const method = options.method ?? 'GET'
  const maxRetry = options.retry ?? (method === 'GET' ? 1 : 0)
  let lastError: unknown

  for (let attempt = 0; attempt <= maxRetry; attempt += 1) {
    try {
      return await requestOnce<T>(url, options)
    } catch (error) {
      lastError = error
      const retryable = error instanceof ApiError && error.isRetryable
      if (!retryable || attempt >= maxRetry) throw error
    }
  }

  throw lastError instanceof Error ? lastError : new Error('请求失败')
}

/**
 * 宽松提取列表字段。
 * 用途：契约里 /meta/jobs、/algo/{code}/runs 只写了「列表」未固定包裹形状，
 * 前端同时兼容裸数组、{items:[]}、{runs:[]}、{jobs:[]} 三种返回。
 */
export function extractList<T>(payload: unknown): T[] {
  if (Array.isArray(payload)) return payload as T[]
  if (typeof payload === 'object' && payload !== null) {
    const record = payload as Record<string, unknown>
    for (const key of ['items', 'runs', 'jobs', 'data', 'list']) {
      const value = record[key]
      if (Array.isArray(value)) return value as T[]
    }
  }
  return []
}

/** 从 Content-Disposition 解析文件名（支持 filename* 与 filename） */
function parseFilename(header: string | null, fallback: string): string {
  if (!header) return fallback
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header)
  if (encoded?.[1]) {
    try {
      return decodeURIComponent(encoded[1])
    } catch {
      return encoded[1]
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header)
  return plain?.[1] ? plain[1] : fallback
}

/** 触发浏览器下载 Blob */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.style.display = 'none'
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  // 延迟回收，避免部分浏览器取消下载
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/**
 * 下载服务端生成的 CSV（如 /algo/{code}/export.csv、/backtest/{id}/export.csv）
 * 失败时同样抛 ApiError，页面可提示后端 error.message。
 */
export async function downloadCsv(
  path: string,
  query?: Record<string, QueryValue>,
  fallbackFilename = 'export.csv',
  options?: { timeoutMs?: number },
): Promise<void> {
  const url = buildUrl(path, query)
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), options?.timeoutMs ?? DEFAULT_TIMEOUT_MS)

  try {
    let response: Response
    try {
      response = await fetch(url, { signal: controller.signal, headers: { Accept: 'text/csv' } })
    } catch (error) {
      if (controller.signal.aborted) {
        throw new ApiError({ code: 'TIMEOUT', message: '导出超时，请稍后重试', status: 0 })
      }
      throw new ApiError({
        code: 'NETWORK_ERROR',
        message: error instanceof Error ? `下载失败：${error.message}` : '下载失败',
        status: 0,
      })
    }

    if (!response.ok) {
      throw await toApiError(response)
    }

    const blob = await response.blob()
    const filename = parseFilename(response.headers.get('Content-Disposition'), fallbackFilename)
    saveBlob(blob, filename)
  } finally {
    clearTimeout(timer)
  }
}
