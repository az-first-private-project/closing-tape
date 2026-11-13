import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import { ApiError } from './api/client'
import { recordStatus } from './lib/statusFeed'
import { hydrateWatchlist } from './lib/watchlist'
import './index.css'

/**
 * 全局 QueryClient：
 * - 重试策略统一交给 src/api/client.ts（只对 5xx / 网络错误重试 1 次），
 *   这里关闭 react-query 的默认重试，避免 4xx（参数错误/数据未就绪）被重复请求；
 * - networkMode 必须为 always：默认 online 模式下断网查询会被挂起（paused），
 *   页面会落到空态且没有重试入口；这里让请求真实发出，由 client.ts 统一报网络错误；
 * - 窗口重新聚焦时不自动刷新，避免盘后分析页频繁重算。
 */
/** detail.next_action 兜底建议（与 ErrorState 口径一致） */
function readNextAction(detail: unknown): string | undefined {
  if (typeof detail !== 'object' || detail === null || !('next_action' in detail)) return undefined
  const value = (detail as Record<string, unknown>).next_action
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}

/** U09：把接口异常记入会话状态中心；ABORTED 是主动取消，DATA_NOT_READY 是业务未就绪，都不记 */
function recordRequestError(error: unknown): void {
  const path = `${window.location.pathname}${window.location.search}`
  if (error instanceof ApiError) {
    if (error.code === 'ABORTED' || error.code === 'DATA_NOT_READY') return
    recordStatus({
      level: error.isRetryable ? 'error' : 'warning',
      title: error.isRetryable ? '接口暂时不可用' : '接口请求未完成',
      detail: error.message,
      code: error.code,
      suggestion: error.suggestion ?? readNextAction(error.detail),
      path,
      requestId: error.requestId ?? undefined,
    })
    return
  }
  if (error instanceof Error) {
    recordStatus({ level: 'error', title: '页面请求失败', detail: error.message, path })
  }
}

const queryClient = new QueryClient({
  queryCache: new QueryCache({ onError: recordRequestError }),
  mutationCache: new MutationCache({ onError: recordRequestError }),
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60 * 5,
      retry: false,
      refetchOnWindowFocus: false,
      networkMode: 'always',
    },
    mutations: {
      retry: false,
    },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
)

// 启动即拉取服务端自选状态（localStorage 仅作缓存；后端不可用时保留本地）
void hydrateWatchlist()
