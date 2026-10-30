import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import './index.css'

/**
 * 全局 QueryClient：
 * - 重试策略统一交给 src/api/client.ts（只对 5xx / 网络错误重试 1 次），
 *   这里关闭 react-query 的默认重试，避免 4xx（参数错误/数据未就绪）被重复请求；
 * - 窗口重新聚焦时不自动刷新，避免盘后分析页频繁重算。
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60 * 5,
      retry: false,
      refetchOnWindowFocus: false,
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
