/**
 * 路由配置（契约第 7 节）
 * /            新闻首页
 * /article/:id 新闻详情
 * /dashboard   盘后分析（原 /dashboard-v3）
 * /algo-center 选股中心
 * /paper       纸面组合跟踪
 * /stocks/:code 个股详情
 * /watchlist   自选股与信号提醒
 * /jobs        任务监控
 * /about       关于
 * *            404
 */
import { Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import HomePage from './pages/HomePage'
import DetailPage from './pages/DetailPage'
import DashboardPage from './pages/DashboardPage'
import AlgoCenter from './pages/AlgoCenter'
import PaperPage from './pages/PaperPage'
import StockDetailPage from './pages/StockDetailPage'
import WatchlistPage from './pages/WatchlistPage'
import JobsPage from './pages/JobsPage'
import AboutPage from './pages/AboutPage'
import NotFoundPage from './pages/NotFoundPage'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<HomePage />} />
        <Route path="/article/:id" element={<DetailPage />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/algo-center" element={<AlgoCenter />} />
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
