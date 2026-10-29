/**
 * 404 页面（契约第 7 节：`*` → 404）
 */
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Compass, Home, TrendingUp } from 'lucide-react'

export default function NotFoundPage() {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="max-w-2xl mx-auto px-4 py-24 text-center space-y-6"
    >
      <div className="flex justify-center">
        <Compass className="w-16 h-16 text-primary/60" />
      </div>
      <div>
        <p className="text-[28px] font-bold text-primary num">404</p>
        <h1 className="mt-3 text-lg font-semibold">页面不存在</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          你访问的地址可能已迁移或输入有误。V5 已将旧量化页面统一到「盘后分析」。
        </p>
      </div>
      <div className="flex flex-wrap items-center justify-center gap-3">
        <Link
          to="/"
          className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm bg-primary text-primary-foreground hover:opacity-90 transition-opacity"
        >
          <Home className="w-4 h-4" />
          返回首页
        </Link>
        <Link
          to="/dashboard"
          className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm border border-border hover:bg-muted transition-colors"
        >
          <TrendingUp className="w-4 h-4" />
          前往盘后分析
        </Link>
      </div>
    </motion.div>
  )
}
