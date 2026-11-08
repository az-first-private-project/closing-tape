import { Suspense, useCallback, useEffect, useState } from 'react'
import { Outlet } from 'react-router-dom'
import { MotionConfig } from 'framer-motion'
import { ThemeProvider } from './ThemeProvider'
import Navbar from './Navbar'
import Footer from './Footer'
import CommandPalette from './CommandPalette'
import AmbientBackdrop from './AmbientBackdrop'
import IntroCurtain, { shouldPlayIntro } from './IntroCurtain'
import { SkeletonCard } from './StateViews'

/** 路由懒加载占位：保持导航可见，只替换内容区（P3-1） */
function PageFallback() {
  return (
    <div className="mx-auto max-w-7xl space-y-3 px-4 py-6 sm:px-6 lg:px-8" role="status" aria-live="polite">
      <SkeletonCard lines={2} />
      <SkeletonCard lines={4} />
    </div>
  )
}

export default function Layout() {
  // U08：开场只作为不拦截交互的短幕布，页面壳始终可见
  const [introMounted, setIntroMounted] = useState(() => shouldPlayIntro())
  const handleFinish = useCallback(() => setIntroMounted(false), [])

  // 兜底：任何异常都不能让幕布滞留
  useEffect(() => {
    if (!introMounted) return
    const timer = window.setTimeout(() => setIntroMounted(false), 5000)
    return () => window.clearTimeout(timer)
  }, [introMounted])

  return (
    <MotionConfig reducedMotion="user">
      <ThemeProvider>
        {/* 背景独立于 app-shell：装饰层不参与页面布局 */}
        <AmbientBackdrop />

        <div className="app-shell flex flex-col">
          <Navbar />
          <CommandPalette />
          <main className="flex-1">
            <Suspense fallback={<PageFallback />}>
              <Outlet />
            </Suspense>
          </main>
          <Footer />
        </div>

        {introMounted && <IntroCurtain onFinish={handleFinish} />}
      </ThemeProvider>
    </MotionConfig>
  )
}
