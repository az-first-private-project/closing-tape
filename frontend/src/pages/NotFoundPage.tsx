/**
 * 404 页面（契约第 7 节：`*` → 404）
 * 视觉：把 404 当作一块行情读数牌——大号等宽数字 + 铜色刻度。
 */
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Compass, Home, TrendingUp } from 'lucide-react'
import PageHeader from '../components/PageHeader'

export default function NotFoundPage() {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, ease: [0.16, 1, 0.3, 1] }}
      className="mx-auto flex min-h-[68vh] max-w-2xl flex-col items-center justify-center px-4 py-16 text-center"
    >
      <span className="grid h-12 w-12 place-items-center rounded-full border border-border/70 bg-muted/30">
        <Compass className="h-5 w-5 text-muted-foreground/70" />
      </span>

      <p className="readout mt-6 text-[72px] leading-none text-foreground sm:text-[88px]">
        4<span className="text-brass">0</span>4
      </p>

      <PageHeader
        align="center"
        size="compact"
        eyebrow="Error 404 · 页面未找到"
        title="页面不存在"
        description="你访问的地址可能已迁移或输入有误。V5 已将旧量化页面统一到「盘后分析」。"
        className="mt-4"
      />

      <div className="mt-7 flex flex-wrap items-center justify-center gap-2.5">
        <Link to="/" className="btn btn-primary btn-sm">
          <Home className="h-3.5 w-3.5" />
          返回首页
        </Link>
        <Link to="/dashboard" className="btn btn-sm">
          <TrendingUp className="h-3.5 w-3.5" />
          前往盘后分析
        </Link>
      </div>
    </motion.div>
  )
}
