import { useCallback, useEffect, useState } from 'react'
import { Outlet } from 'react-router-dom'
import { ThemeProvider } from './ThemeProvider'
import Navbar from './Navbar'
import Footer from './Footer'
import CommandPalette from './CommandPalette'
import AmbientBackdrop from './AmbientBackdrop'
import IntroCurtain, { shouldPlayIntro } from './IntroCurtain'

export default function Layout() {
  /**
   * 开场特效状态：
   *  - mounted：幕布是否还在 DOM 里（分离动画需要它继续存在）
   *  - revealed：页面是否开始归位（与幕布分离同时发生）
   * 两者分开，避免幕布过早卸载导致「页面直接出现」。
   */
  const [intro, setIntro] = useState(() => {
    const play = shouldPlayIntro()
    return { mounted: play, revealed: !play }
  })

  const handleReveal = useCallback(() => {
    setIntro((current) => (current.revealed ? current : { ...current, revealed: true }))
  }, [])

  const handleFinish = useCallback(() => {
    setIntro({ mounted: false, revealed: true })
  }, [])

  // 兜底：任何异常都不能让页面停留在不可见状态
  useEffect(() => {
    if (!intro.mounted) return
    const timer = window.setTimeout(() => setIntro({ mounted: false, revealed: true }), 10000)
    return () => window.clearTimeout(timer)
  }, [intro.mounted])

  return (
    <ThemeProvider>
      {/* 背景独立于 app-shell：开场归位的 blur/scale 不会把它一起缩放 */}
      <AmbientBackdrop />

      <div
        className={`app-shell flex flex-col ${
          intro.revealed ? 'is-intro-ready' : 'is-intro-pending'
        }`}
      >
        <Navbar />
        <CommandPalette />
        <main className="flex-1">
          <Outlet />
        </main>
        <Footer />
      </div>

      {intro.mounted && (
        <IntroCurtain onReveal={handleReveal} onFinish={handleFinish} />
      )}
    </ThemeProvider>
  )
}
