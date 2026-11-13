/**
 * 路由配置（契约第 7 节）
 * /            新闻首页
 * /article/:id 新闻详情
 * /dashboard   盘后分析（原 /dashboard-v3）
 * /index-compare 指数对比（创业板50 / 红利低波50 风格轮动）
 * /algo-center 选股中心
 * /ladder      策略天梯（重定向到 /algo-center#ladder）
 * /paper       纸面组合跟踪
 * /stocks/:code 个股详情
 * /watchlist   自选股与信号提醒
 * /jobs        任务监控
 * /about       关于
 * *            404
 *
 * P3-1：页面全部按路由懒加载，重图表页面（dashboard/algo-center/paper/stocks）
 * 不再进入入口包；Layout 常驻，Suspense 边界在 Layout 的 Outlet 外侧。
 */
import { lazy } from 'react'
import { Navigate, Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'

const HomePage = lazy(() => import('./pages/HomePage'))
const DetailPage = lazy(() => import('./pages/DetailPage'))
const DashboardPage = lazy(() => import('./pages/DashboardPage'))
const IndexComparePage = lazy(() => import('./pages/IndexComparePage'))
const AlgoCenter = lazy(() => import('./pages/AlgoCenter'))
const PaperPage = lazy(() => import('./pages/PaperPage'))
const StockDetailPage = lazy(() => import('./pages/StockDetailPage'))
const WatchlistPage = lazy(() => import('./pages/WatchlistPage'))
const JobsPage = lazy(() => import('./pages/JobsPage'))
const AboutPage = lazy(() => import('./pages/AboutPage'))
const NotFoundPage = lazy(() => import('./pages/NotFoundPage'))

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<HomePage />} />
        <Route path="/article/:id" element={<DetailPage />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/index-compare" element={<IndexComparePage />} />
        <Route path="/algo-center" element={<AlgoCenter />} />
        <Route path="/ladder" element={<Navigate to="/algo-center#ladder" replace />} />
        <Route path="/paper" element={<PaperPage />} />
        <Route path="/stocks/:code" element={<StockDetailPage />} />
        <Route path="/watchlist" element={<WatchlistPage />} />
        <Route path="/jobs" element={<JobsPage />} />
        <Route path="/about" element={<AboutPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}
